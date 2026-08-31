#!/usr/bin/env python3
"""
netviz.py - simple gate-level netlist parser, DOT exporter and ASCII visualizer


Supported gate instance syntax (very permissive):
  G1 : and_gate port map (A, B, N1);
  G2 : not_gate port map (C, N2);

Ports declared in an entity block with lines like:
  A : in std_logic;

Signals declared with `signal NAME : std_logic;`

This is intentionally small and easy to extend.
"""

import re
import sys
import argparse
import itertools
import os
from datetime import datetime
import subprocess
import shutil
from collections import defaultdict, deque


def parse_netlist(path):
    text = open(path).read()
    # ports
    ports = []
    inputs = []
    outputs = []
    # Find lines inside port (...) block
    m = re.search(r"port\s*\((.*?)\);", text, re.S | re.I)
    if m:
        port_block = m.group(1)
        for line in port_block.split(';'):
            line = line.strip()
            if not line:
                continue
            # e.g. A : in std_logic
            mm = re.match(r"([A-Za-z0-9_]+)\s*:\s*(in|out)", line, re.I)
            if mm:
                name = mm.group(1)
                direction = mm.group(2).lower()
                ports.append((name, direction))
                if direction == 'in':
                    inputs.append(name)
                else:
                    outputs.append(name)
    # signals
    signals = re.findall(r"signal\s+([A-Za-z0-9_]+)\s*:\s*std_logic", text, re.I)

    # instances
    # pattern: NAME : gate_type port map (arg1, arg2, out);
    inst_re = re.compile(r"([A-Za-z0-9_]+)\s*:\s*([A-Za-z0-9_]+)\s*port\s*map\s*\(([^)]*)\)", re.I)
    instances = []
    for mm in inst_re.finditer(text):
        inst_name = mm.group(1)
        gate_type = mm.group(2).lower()
        args = [a.strip() for a in mm.group(3).split(',') if a.strip()]
        instances.append({'name': inst_name, 'type': gate_type, 'args': args})

    return {
        'inputs': inputs,
        'outputs': outputs,
        'signals': signals,
        'instances': instances
    }


def build_graph(parsed):
    # We'll treat nets as names (ports and signals)
    nets = set(parsed['inputs']) | set(parsed['outputs']) | set(parsed['signals'])
    gates = []
    net_drivers = defaultdict(list)  # net -> driving gate (or 'INPUT')
    net_loads = defaultdict(list)    # net -> gates that read it

    # Inputs are drivers
    for inp in parsed['inputs']:
        net_drivers[inp].append({'kind': 'INPUT', 'name': inp})

    for inst in parsed['instances']:
        gname = inst['name']
        gtype = inst['type']
        args = inst['args']
        # convention: for simple gates, last arg is output, others inputs
        if len(args) == 1:
            in_nets = []
            out_net = args[0]
        elif len(args) == 2:
            in_nets = [args[0]]
            out_net = args[1]
        else:
            in_nets = args[:-1]
            out_net = args[-1]
        gate = {'name': gname, 'type': gtype, 'inputs': in_nets, 'output': out_net}
        gates.append(gate)
        nets.add(out_net)
        for n in in_nets:
            nets.add(n)
            net_loads[n].append(gate)
        net_drivers[out_net].append(gate)

    # Outputs that are connected to nets: mark loads
    for outp in parsed['outputs']:
        net_loads[outp].append({'kind': 'OUTPUT', 'name': outp})

    return {
        'nets': sorted(nets),
        'signals': sorted(set(parsed['signals'])),
        'gates': gates,
        'net_drivers': net_drivers,
        'net_loads': net_loads,
        'inputs': parsed['inputs'],
        'outputs': parsed['outputs']
    }


def compute_levels(graph):
    # level per net and per gate
    net_level = {}
    gate_level = {}
    # inputs nets level = 0
    for inp in graph['inputs']:
        net_level[inp] = 0
    # topological process: repeatedly assign gate levels when all its input nets known
    pending = set(g['name'] for g in graph['gates'])
    gate_by_name = {g['name']: g for g in graph['gates']}
    changed = True
    while changed and pending:
        changed = False
        for gname in list(pending):
            g = gate_by_name[gname]
            if all((inp in net_level) for inp in g['inputs']):
                lvl = 1 + max((net_level[inp] for inp in g['inputs']), default=0)
                gate_level[gname] = lvl
                net_level[g['output']] = lvl
                pending.remove(gname)
                changed = True
    # For any outputs not assigned, set to max level
    max_lvl = max(list(net_level.values()) or [0])
    for outp in graph['outputs']:
        if outp not in net_level:
            net_level[outp] = max_lvl + 1
    return net_level, gate_level


def eval_gate(gtype, inputs):
    # inputs: list of '0'/'1'/'X'
    t = gtype.lower()
    if t.endswith('_gate'):
        t = t[:-5]
    # propagate unknowns
    if any(v not in ('0', '1') for v in inputs):
        return 'X'
    vals = [1 if v == '1' else 0 for v in inputs]
    if t in ('and',):
        return '1' if all(vals) else '0'
    if t in ('or',):
        return '1' if any(vals) else '0'
    if t in ('not',):
        if not vals:
            return 'X'
        return '0' if vals[0] == 1 else '1'
    if t in ('nand',):
        return '0' if all(vals) else '1'
    if t in ('nor',):
        return '0' if any(vals) else '1'
    if t in ('xor',):
        s = sum(vals)
        return '1' if (s % 2) == 1 else '0'
    if t in ('xnor',):
        s = sum(vals)
        return '0' if (s % 2) == 1 else '1'
    if t in ('buf', 'buffer'):
        return '1' if vals and vals[0] == 1 else '0'
    # unknown gate type: try simple pass-through for single input
    if len(vals) == 1:
        return '1' if vals[0] == 1 else '0'
    return 'X'


def evaluate(graph, input_values):
    """Simulate combinationally. input_values: dict net->'0'/'1'. Returns net_values dict."""
    net_values = {}
    # initialize inputs
    for inp in graph['inputs']:
        v = input_values.get(inp)
        if v is None:
            raise ValueError(f"No value provided for input '{inp}'")
        if v not in ('0', '1'):
            raise ValueError(f"Invalid value for {inp}: {v} (expected 0 or 1)")
        net_values[inp] = v
    # topological order
    _, gate_level = compute_levels(graph)
    sorted_gates = sorted(graph['gates'], key=lambda g: gate_level.get(g['name'], 0))
    for g in sorted_gates:
        in_vals = [net_values.get(n, 'X') for n in g['inputs']]
        out_val = eval_gate(g['type'], in_vals)
        net_values[g['output']] = out_val
    return net_values


def gate_shape(gtype):
    t = str(gtype).lower().replace('_gate', '')
    if t == 'and':
        return 'box'
    if t == 'or':
        return 'diamond'
    if t == 'xor':
        return 'diamond'
    if t == 'not':
        return 'ellipse'
    if t in ('nand', 'nor', 'xnor'):
        return 'box'
    return 'box'


def gate_attr(gtype):
    t = str(gtype).lower().replace('_gate', '')
    base = {
        'and': {'shape': 'box', 'fillcolor': '#edf6ff', 'style': 'filled', 'width': '1.4', 'height': '0.9'},
        'or': {'shape': 'diamond', 'fillcolor': '#f4f4f4', 'style': 'filled', 'width': '1.5', 'height': '1.0'},
        'xor': {'shape': 'diamond', 'fillcolor': '#f4f4f4', 'style': 'filled', 'width': '1.5', 'height': '1.0'},
        'not': {'shape': 'ellipse', 'fillcolor': '#fff7ed', 'style': 'filled', 'width': '1.2', 'height': '0.8'},
        'nand': {'shape': 'box', 'fillcolor': '#edf6ff', 'style': 'filled', 'width': '1.4', 'height': '0.9'},
        'nor': {'shape': 'diamond', 'fillcolor': '#f4f4f4', 'style': 'filled', 'width': '1.5', 'height': '1.0'},
        'xnor': {'shape': 'diamond', 'fillcolor': '#f4f4f4', 'style': 'filled', 'width': '1.5', 'height': '1.0'},
        'buf': {'shape': 'box', 'fillcolor': '#edf6ff', 'style': 'filled', 'width': '1.4', 'height': '0.8'},
    }.get(t, {'shape': 'box', 'fillcolor': '#edf6ff', 'style': 'filled', 'width': '1.4', 'height': '0.9'})
    return base


def export_dot(graph, net_level, gate_level, outpath, net_values=None, show_inputs=True, show_internal_nets=False):
    lines = []
    lines.append('digraph G {')
    lines.append('  rankdir=LR;')
    lines.append('  node [fontname="Helvetica"];')
    color_map = {'1': 'lightgreen', '0': 'lightcoral', 'X': 'lightgray'}
    internal_nets = set(graph['signals']) - set(graph['inputs']) - set(graph['outputs'])

    if show_inputs:
        for inp in graph['inputs']:
            lab = inp
            attrs = ['shape=oval']
            if net_values and inp in net_values:
                v = net_values[inp]
                lab = f"{inp}\n{v}"
                col = color_map.get(v, 'white')
                attrs.append('style=filled')
                attrs.append(f'fillcolor={col}')
            attr_str = ', '.join(attrs)
            lines.append(f'  "{inp}" [{attr_str}, label="{lab}"];')

    for g in graph['gates']:
        label = g['type'].upper().replace('_', ' ')
        attr_list = [f'shape={gate_shape(g["type"])}', 'fixedsize=true', 'width=1.1', 'height=0.8']
        if net_values and g['output'] in net_values:
            v = net_values[g['output']]
            label = f"{label}\n{v}"
            col = color_map.get(v, 'white')
            attr_list.append('style=filled')
            attr_list.append(f'fillcolor={col}')
        attr_str = ', '.join(attr_list)
        lines.append(f'  "{g["name"]}" [{attr_str}, label="{label}"];')

    for outp in graph['outputs']:
        lab = outp
        attrs = ['shape=oval']
        if net_values and outp in net_values:
            v = net_values[outp]
            lab = f"{outp}\n{v}"
            col = color_map.get(v, 'white')
            attrs.append('style=filled')
            attrs.append(f'fillcolor={col}')
        attr_str = ', '.join(attrs)
        lines.append(f'  "{outp}" [{attr_str}, label="{lab}"];')

    edges = set()

    for g in graph['gates']:
        for inp in g['inputs']:
            drivers = graph['net_drivers'].get(inp, [])
            if not drivers:
                if not show_internal_nets and inp in internal_nets:
                    continue
                edges.add((inp, g['name']))
                continue
            for d in drivers:
                if isinstance(d, dict) and d.get('kind') == 'INPUT':
                    src = d['name']
                    if not show_inputs:
                        continue
                elif isinstance(d, dict) and d.get('kind') == 'OUTPUT':
                    src = d['name']
                else:
                    src = d['name'] if isinstance(d, dict) else getattr(d, 'name', str(d))
                if not show_internal_nets and inp in internal_nets and src in internal_nets:
                    continue
                edges.add((src, g['name']))

        outn = g['output']
        consumers = graph['net_loads'].get(outn, [])
        if consumers:
            for c in consumers:
                if isinstance(c, dict) and c.get('kind') == 'OUTPUT':
                    dst = c['name']
                elif isinstance(c, dict):
                    dst = c['name']
                else:
                    dst = getattr(c, 'name', str(c))
                if not show_internal_nets and outn in internal_nets and dst in internal_nets:
                    continue
                edges.add((g['name'], dst))
        if outn in graph['outputs']:
            edges.add((g['name'], outn))

    for src, dst in sorted(edges):
        lines.append(f'  "{src}" -> "{dst}";')

    lines.append('}')
    open(outpath, 'w').write('\n'.join(lines))
    print(f'Wrote DOT to {outpath}')


def layout_positions(graph, net_level, gate_level):
    # create nodes for inputs, gates, outputs and assign (x=level, y)
    # Start with inputs at integer y positions
    node_pos = {}
    level_nodes = defaultdict(list)
    # inputs
    for i, inp in enumerate(graph['inputs']):
        lvl = net_level.get(inp, 0)
        node_pos[inp] = [lvl, float(i)]
        level_nodes[lvl].append(inp)
    # gates - place by level, y = avg of input y's
    for g in graph['gates']:
        gname = g['name']
        lvl = gate_level.get(gname, net_level.get(g['output'], 0))
        in_ys = [node_pos.get(inp, [None, 0])[1] for inp in g['inputs'] if inp in node_pos]
        y = float(sum(in_ys)/len(in_ys)) if in_ys else float(len(level_nodes[lvl]))
        node_pos[gname] = [lvl, y]
        level_nodes[lvl].append(gname)
        # also ensure net (output) gets same pos
        node_pos[g['output']] = [lvl, y]
    # outputs: put at level max+1 or assigned level
    max_lvl = max((lv for (lv, _) in node_pos.values()), default=0)
    out_lvl = max_lvl + 1
    for outp in graph['outputs']:
        lvl = node_pos.get(outp, [out_lvl, 0])[0]
        if lvl == out_lvl:
            y = float(len(level_nodes[lvl]))
            node_pos[outp] = [lvl, y]
            level_nodes[lvl].append(outp)
    return node_pos


def render_ascii(graph, node_pos, width_per_col=12, net_values=None):
    # compute canvas size
    max_x = max(int(p[0]) for p in node_pos.values())
    max_y = int(max(p[1] for p in node_pos.values()))
    H = max(7, max_y*2 + 5)
    W = (max_x + 2) * width_per_col
    # initialize canvas
    canvas = [[' ' for _ in range(W)] for _ in range(H)]

    def put_text(x, y, s):
        # x is column index (0..), convert to char pos
        cx = int(x * width_per_col + 1)
        cy = int(y*2) + 1
        for i,ch in enumerate(s):
            if 0 <= cy < H and 0 <= cx+i < W:
                canvas[cy][cx+i] = ch

    # precompute gate name set for speed
    gate_names = {g['name'] for g in graph['gates']}

    # draw nodes with inline values if available
    for name, (lvl, y) in node_pos.items():
        # determine base label
        if name in gate_names:
            g = next((gg for gg in graph['gates'] if gg['name']==name), None)
            base = g['type'].upper().replace('_', ' ') if g else name.upper()
            # get output net value for this gate
            val = net_values.get(g['output'], 'X') if net_values and g else None
        else:
            base = name
            val = net_values.get(name, None) if net_values else None
        label = f"{base}={val}" if val is not None else base
        put_text(lvl, y, label)

    # draw edges
    for g in graph['gates']:
        tgt = g['name']
        tx, ty = node_pos[tgt]
        # draw from each input net (which may be an input node or net driven by gate)
        for inp in g['inputs']:
            if inp not in node_pos:
                continue
            sx, sy = node_pos[inp]
            # horizontal from sx to tx at sy
            # try to compute a reasonable offset using input label length
            in_label = inp
            if net_values and inp in net_values:
                in_label = f"{inp}={net_values[inp]}"
            x1 = int(sx * width_per_col + len(in_label) + 2)
            x2 = int(tx * width_per_col - 1)
            cy = int(sy*2) + 1
            if x1 < x2:
                for x in range(x1, x2):
                    if 0 <= cy < H and 0 <= x < W:
                        canvas[cy][x] = '-'
            # vertical from sy to ty at x2
            cy2 = int(ty*2) + 1
            col = x2
            if cy <= cy2:
                for y in range(cy, cy2+1):
                    if 0 <= y < H and 0 <= col < W:
                        canvas[y][col] = '|' if canvas[y][col] == ' ' else canvas[y][col]
            else:
                for y in range(cy2, cy+1):
                    if 0 <= y < H and 0 <= col < W:
                        canvas[y][col] = '|' if canvas[y][col] == ' ' else canvas[y][col]

    # produce lines
    out_lines = [''.join(row).rstrip() for row in canvas]
    # trim empty top and bottom
    while out_lines and not out_lines[0].strip():
        out_lines.pop(0)
    while out_lines and not out_lines[-1].strip():
        out_lines.pop()
    return '\n'.join(out_lines)



import csv

def write_waveform_svg(csv_path, svg_path, title=None):
    """Read a truth-table CSV and render a timeline-style waveform into an SVG file."""
    rows = []
    with open(csv_path, 'r') as f:
        rdr = csv.reader(f)
        header = next(rdr)
        for r in rdr:
            if not r:
                continue
            rows.append(r)
    if not rows:
        raise RuntimeError('No rows in truth table')
    signals = header
    T = len(rows)
    # layout
    step = max(40, 60)  # px per time step
    left = 160
    top = 40
    row_h = 36
    amp = 10
    width = left + step * T + 40
    height = top + row_h * len(signals) + 40

    def val_y(i, v):
        base = top + i * row_h + row_h / 2
        if v == '1':
            return base - amp
        if v == '0':
            return base + amp
        return base  # X

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}">')
    svg.append('<style>text{font-family:monospace; font-size:12px}</style>')
    if title:
        svg.append(f'<text x="{left}" y="20" font-weight="bold">{title}</text>')
    # time ticks
    for t in range(T):
        x = left + t * step + step/2
        svg.append(f'<line x1="{x}" y1="{top-6}" x2="{x}" y2="{height-10}" stroke="#eee" stroke-width="1"/>')
        svg.append(f'<text x="{x-6}" y="{top-12}" font-size="10">{t}</text>')

    # draw each signal
    for i, sig in enumerate(signals):
        ymid = top + i * row_h + row_h / 2
        # label
        svg.append(f'<text x="10" y="{ymid+4}">{sig}</text>')
        # baseline
        svg.append(f'<line x1="{left-10}" y1="{ymid}" x2="{width-10}" y2="{ymid}" stroke="#ddd" stroke-width="1"/>')
        # draw waveform path
        path_commands = []
        # initial
        v0 = rows[0][i]
        x0 = left
        y0 = val_y(i, v0)
        path_commands.append(f'M {x0} {y0}')
        # horizontal to end of first step
        x_end = left + step
        if v0 == 'X':
            path_commands.append(f'L {x_end} {y0}')
        else:
            path_commands.append(f'L {x_end} {y0}')
        prev_v = v0
        for t in range(1, T):
            x_start = left + t * step
            x_end = left + (t+1) * step
            v = rows[t][i]
            y_prev = val_y(i, prev_v)
            y_curr = val_y(i, v)
            # vertical transition at x_start
            if v != prev_v:
                # vertical line
                path_commands.append(f'L {x_start} {y_curr}')
            # horizontal for this step
            path_commands.append(f'L {x_end} {y_curr}')
            prev_v = v
        # choose stroke style depending on presence of X
        has_x = any(rows[t][i] not in ('0','1') for t in range(T))
        stroke = '#2a9d8f'  # greenish for high
        # build path
        path_d = ' '.join(path_commands)
        stroke_color = '#000000'
        # we'll stroke with black and fill none; use different stroke-dash for X
        svg.append(f'<path d="{path_d}" fill="none" stroke="#000" stroke-width="2"/>')
        # overlay colored segments for highs
        # draw segments individually to color highs/low/X differently
        for t in range(T):
            x_s = left + t * step
            x_e = left + (t+1) * step
            v = rows[t][i]
            y = val_y(i, v)
            if v == '1':
                svg.append(f'<line x1="{x_s}" y1="{y}" x2="{x_e}" y2="{y}" stroke="#2a9d8f" stroke-width="4"/>')
            elif v == '0':
                svg.append(f'<line x1="{x_s}" y1="{y}" x2="{x_e}" y2="{y}" stroke="#e76f51" stroke-width="4"/>')
            else:
                # X: dashed gray
                svg.append(f'<line x1="{x_s}" y1="{y}" x2="{x_e}" y2="{y}" stroke="#999" stroke-width="2" stroke-dasharray="4 4"/>')
                # mark with X
                svg.append(f'<text x="{(x_s+x_e)/2 - 4}" y="{y-4}" font-size="10">X</text>')
            # vertical transitions
            if t < T-1:
                v2 = rows[t+1][i]
                if v2 != v:
                    x_v = x_e
                    y1t = val_y(i, v)
                    y2t = val_y(i, v2)
                    svg.append(f'<line x1="{x_v}" y1="{y1t}" x2="{x_v}" y2="{y2t}" stroke="#000" stroke-width="2"/>')
    svg.append('</svg>')
    with open(svg_path, 'w') as f:
        f.write('\n'.join(svg))

# main starts here
def write_waveform_svg(csv_path, svg_path, title=None):
    """Improved waveform SVG renderer with clear left labels and time ticks.

    csv_path: truth-table CSV where first row is header: signal names (inputs then outputs)
    svg_path: output SVG path
    """
    import csv
    rows = []
    with open(csv_path, 'r') as f:
        rdr = csv.reader(f)
        header = next(rdr)
        for r in rdr:
            if not r:
                continue
            rows.append(r)
    if not rows:
        raise RuntimeError('No rows in truth table')
    signals = header
    T = len(rows)
    # layout parameters
    max_label_len = max(len(s) for s in signals)
    left = max(160, max_label_len * 9 + 40)  # pixels reserved for labels
    step = 80  # px per time step
    top = 60
    row_h = 40
    amp = 12
    width = left + step * T + 40
    height = top + row_h * len(signals) + 40

    def val_y(i, v):
        base = top + i * row_h + row_h / 2
        if v == '1':
            return base - amp
        if v == '0':
            return base + amp
        return base  # X

    svg = []
    svg.append(f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" style="background:white">')
    svg.append('<style>text{font-family:monospace; font-size:13px}</style>')
    if title:
        svg.append(f'<text x="{left}" y="20" font-weight="bold">{title}</text>')
    # draw label background
    svg.append(f'<rect x="0" y="{top-10}" width="{left-10}" height="{height-top-20}" fill="white" stroke="#ddd"/>')
    # separator
    svg.append(f'<line x1="{left-10}" y1="{top-10}" x2="{left-10}" y2="{height-20}" stroke="#aaa" stroke-width="1"/>')

    # time ticks and optional binary labels
    for t in range(T):
        x = left + t * step + step / 2
        # light vertical grid
        svg.append(f'<line x1="{x}" y1="{top-20}" x2="{x}" y2="{height-10}" stroke="#f0f0f0" stroke-width="1"/>')
        # time index
        svg.append(f'<text x="{x-6}" y="{top-28}" font-size="11">{t}</text>')
        # input vector as small binary under tick
        vec = ''.join(rows[t][:len(header)]) if False else ''
        # (we skip long binary labels here to avoid clutter)

    # draw each signal lane
    for i, sig in enumerate(signals):
        ymid = top + i * row_h + row_h / 2
        # label (right-aligned inside left area)
        label_x = left - 14
        svg.append(f'<text x="{label_x}" y="{ymid+5}" text-anchor="end">{sig}</text>')
        # baseline center
        svg.append(f'<line x1="{left-4}" y1="{ymid}" x2="{width-10}" y2="{ymid}" stroke="#eee" stroke-width="1"/>')
        # draw segments per time step
        prev_v = rows[0][i]
        # start at left
        x0 = left
        for t in range(T):
            x_s = left + t * step
            x_e = left + (t + 1) * step
            v = rows[t][i]
            y = val_y(i, v)
            # draw horizontal segment
            if v == '1':
                svg.append(f'<line x1="{x_s}" y1="{y}" x2="{x_e}" y2="{y}" stroke="#2a9d8f" stroke-width="4"/>')
            elif v == '0':
                svg.append(f'<line x1="{x_s}" y1="{y}" x2="{x_e}" y2="{y}" stroke="#e63946" stroke-width="4"/>')
            else:
                svg.append(f'<line x1="{x_s}" y1="{y}" x2="{x_e}" y2="{y}" stroke="#999" stroke-width="2" stroke-dasharray="6 4"/>')
                svg.append(f'<text x="{(x_s+x_e)/2 - 5}" y="{y-6}" font-size="11">X</text>')
            # vertical transition to next step
            if t < T-1:
                v2 = rows[t+1][i]
                if v2 != v:
                    y2 = val_y(i, v2)
                    svg.append(f'<line x1="{x_e}" y1="{y}" x2="{x_e}" y2="{y2}" stroke="#000" stroke-width="2"/>')
    svg.append('</svg>')
    with open(svg_path, 'w') as f:
        f.write('\n'.join(svg))


def main(argv):
    p = argparse.ArgumentParser(description='Netlist visualizer and simple simulator')
    p.add_argument('netlist')
    p.add_argument('--dot', help='Write DOT to this file (single-run mode)', default='out.dot')
    p.add_argument('--ascii', help='Write ASCII visualization to this file (single-run mode)', default='out.txt')
    p.add_argument('--inputs', help='Comma-separated input assignments, e.g. A=1,B=0')
    p.add_argument('--outdir', help='Output directory (used for all-combinations)', default=None)
    args = p.parse_args(argv)

    parsed = parse_netlist(args.netlist)
    graph = build_graph(parsed)
    net_level, gate_level = compute_levels(graph)
    inputs = graph['inputs']
    base = os.path.splitext(os.path.basename(args.netlist))[0]

    # If --inputs provided: run single simulation (non-interactive)
    if args.inputs:
        input_values = {}
        for part in args.inputs.split(','):
            if not part.strip():
                continue
            if '=' not in part:
                print(f"Ignoring malformed input assignment: {part}")
                continue
            name, val = part.split('=', 1)
            name = name.strip()
            val = val.strip()
            if val not in ('0', '1'):
                print(f"Invalid value for {name}: {val} (expected 0 or 1).")
                sys.exit(1)
            input_values[name] = val
        # ensure all inputs provided
        for inp in inputs:
            if inp not in input_values:
                print(f"Missing value for input {inp}")
                sys.exit(1)
        try:
            net_values = evaluate(graph, input_values)
        except ValueError as e:
            print('Simulation error:', e)
            sys.exit(1)
        print('\nSimulation results:')
        for outp in graph['outputs']:
            val = net_values.get(outp, 'X')
            print(f'  {outp} = {val}')
        # export single DOT and ASCII
        export_dot(graph, net_level, gate_level, args.dot, net_values=net_values)
        node_pos = layout_positions(graph, net_level, gate_level)
        ascii_art = render_ascii(graph, node_pos, net_values=net_values)
        open(args.ascii, 'w').write(ascii_art + '\n')
        print('\n--- ASCII netlist visualization ---\n')
        print(ascii_art)
        print(f'\nWrote DOT to {args.dot} and ASCII to {args.ascii}')
        return

    # No --inputs: generate all combinations of inputs and produce outputs
    n = len(inputs)
    if n == 0:
        # no inputs; just evaluate once
        try:
            net_values = evaluate(graph, {})
        except ValueError as e:
            print('Simulation error:', e)
            sys.exit(1)
        print('\nSimulation results:')
        for outp in graph['outputs']:
            val = net_values.get(outp, 'X')
            print(f'  {outp} = {val}')
        export_dot(graph, net_level, gate_level, args.dot, net_values=net_values)
        node_pos = layout_positions(graph, net_level, gate_level)
        ascii_art = render_ascii(graph, node_pos, net_values=net_values)
        open(args.ascii, 'w').write(ascii_art + '\n')
        print('\n--- ASCII netlist visualization ---\n')
        print(ascii_art)
        print(f'\nWrote DOT to {args.dot} and ASCII to {args.ascii}')
        return

    # create output directory
    if args.outdir:
        outdir = args.outdir
        os.makedirs(outdir, exist_ok=True)
    else:
        now = datetime.now().strftime('%Y%m%d_%H%M%S')
        outdir = os.path.join('outputs', f'{base}_all_{now}')
        os.makedirs(outdir, exist_ok=True)

    print(f'Generating all {2**n} combinations for inputs: {",".join(inputs)}')
    truth_rows = []
    dot_available = shutil.which('dot') is not None

    for bits in itertools.product('01', repeat=n):
        assign = {inp: bits[i] for i, inp in enumerate(inputs)}
        bitstr = ''.join(bits)
        # evaluate
        net_values = evaluate(graph, assign)
        # collect truth table row (outputs only)
        out_vals = [net_values.get(o, 'X') for o in graph['outputs']]
        truth_rows.append((bitstr, ','.join(f'{k}={v}' for k,v in assign.items()), ''.join(out_vals), out_vals))

    # write truth table (inputs then outputs)
    tt_path = os.path.join(outdir, f'{base}_truth_table.csv')
    with open(tt_path, 'w') as f:
        f.write(','.join(inputs + graph['outputs']) + '\n')
        for bits, assign_str, outbits, out_list in truth_rows:
            row = ','.join(bits) + ',' + ','.join(out_list)
            f.write(row + '\n')

    print(f'All outputs written to {outdir}')
    print(f'Truth table written to {tt_path}')

    # generate waveform SVG
    try:
        svg_path = os.path.join(outdir, f'{base}_waveform.svg')
        write_waveform_svg(tt_path, svg_path, title=base)
        print(f'Waveform SVG written to {svg_path}')
    except Exception as e:
        print('Failed to generate waveform SVG:', e)

if __name__ == '__main__':
    main(sys.argv[1:])
