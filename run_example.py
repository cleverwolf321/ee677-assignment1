#!/usr/bin/env python3
"""
run_example.py - user-friendly runner for a chosen .net example

Workflow:
  - Lists .net files in current directory and prompts the user to pick one
  - Prompts whether to enter input assignments interactively or provide a comma-separated list
  - Creates outputs/<basename>_YYYYMMDD_HHMMSS folder and runs netviz.py
  - Saves DOT, ASCII and a run log into the folder
  - If Graphviz `dot` exists, renders PNG automatically

Usage: python3 run_example.py
"""
import os
import sys
import glob
import subprocess
import shutil
from datetime import datetime

HERE = os.path.dirname(__file__)
logicsim = os.path.join(HERE, 'logicsim.py')


def choose_netfile():
    nets = sorted([os.path.basename(p) for p in glob.glob('*.net')])
    if not nets:
        print('No .net files found in current directory.')
        sys.exit(1)
    print('Available .net examples:')
    for i, n in enumerate(nets, 1):
        print(f'  {i}. {n}')
    while True:
        choice = input(f'Select example by number (1-{len(nets)}) or name: ').strip()
        if not choice:
            continue
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(nets):
                return nets[idx-1]
            else:
                print('Number out of range')
                continue
        # allow direct name
        if choice in nets:
            return choice
        print('Invalid selection; try again')


def ask_inputs():
    print('\nInput assignment options:')
    print('  1) Enter comma-separated list (e.g. A=1,B=0)')
    print('  2) Interactive prompt for each input (press Enter for this)')
    mode = input('Choose 1 or 2 (default 2): ').strip()
    if mode == '1':
        s = input('Enter assignments: ').strip()
        return s
    return ''  # netviz will prompt interactively


def make_output_folder(basename):
    now = datetime.now().strftime('%Y%m%d_%H%M%S')
    outdir = os.path.join('outputs', f'{basename}_{now}')
    os.makedirs(outdir, exist_ok=True)
    return outdir


def run_netviz(netfile, outdir, input_assignments):
    base = os.path.splitext(os.path.basename(netfile))[0]
    dotpath = os.path.join(outdir, f'{base}.dot')
    asciipath = os.path.join(outdir, f'{base}.txt')
    logpath = os.path.join(outdir, f'{base}.log')

    cmd = ['python3', logicsim, netfile, '--dot', dotpath, '--ascii', asciipath]
    if input_assignments:
        cmd += ['--inputs', input_assignments]
        print('Running logicsim with inputs:', input_assignments)
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        out = proc.stdout
        # print logicsim console output (ASCII diagram, results) to terminal
        print('\n--- logicsim output ---\n')
        print(out)
    else:
        print('Running logicsim (interactive input expected)')
        # spawn and attach to terminal, but still capture output via tee-like approach
        # Here we run logicsim and allow it to prompt the user directly
        proc = subprocess.run(cmd)
        out = f'logicsim exited with code {proc.returncode}\n'

    with open(logpath, 'w') as f:
        f.write(out)

    print()  # spacing
    print('logicsim output saved to', logpath)
    print('DOT saved to', dotpath)
    print('ASCII saved to', asciipath)

    # try to render png if dot available
    pngpath = os.path.join(outdir, f'{base}.png')
    if shutil.which('dot'):
        try:
            subprocess.run(['dot', '-Tpng', dotpath, '-o', pngpath], check=True)
            print('Rendered PNG to', pngpath)
        except subprocess.CalledProcessError:
            print('Failed to render PNG using dot')
    else:
        print('Graphviz `dot` not found in PATH; PNG not generated')

    return outdir


def main():
    netfile = choose_netfile()
    assigns = ask_inputs()
    basename = os.path.splitext(os.path.basename(netfile))[0]
    outdir = make_output_folder(basename)

    # if interactive mode (assigns == ''), run netviz attached to terminal
    if assigns:
        run_netviz(netfile, outdir, assigns)
    else:
        # we want netviz to prompt for inputs; run it with working dir set and attach
        # but capture console output into a log file: run netviz and tee output
        # simplest approach: run netviz and let it print to console, then copy stdout to log
        cmd = ['python3', logicsim, netfile, '--dot', os.path.join(outdir, f'{basename}.dot'), '--ascii', os.path.join(outdir, f'{basename}.txt')]
        print('Launching logicsim; please enter input values when prompted...')
        proc = subprocess.run(cmd)
        # logicsim writes files itself; create a basic log
        with open(os.path.join(outdir, f'{basename}.log'), 'w') as f:
            f.write(f'logicsim exited with code {proc.returncode}\n')

        # try render
        if shutil.which('dot'):
            try:
                subprocess.run(['dot', '-Tpng', os.path.join(outdir, f'{basename}.dot'), '-o', os.path.join(outdir, f'{basename}.png')], check=True)
                print('Rendered PNG to', os.path.join(outdir, f'{basename}.png'))
            except subprocess.CalledProcessError:
                print('Failed to render PNG using dot')
        else:
            print('Graphviz `dot` not found in PATH; PNG not generated')

    print('\nAll outputs are in folder:', outdir)


if __name__ == '__main__':
    main()
