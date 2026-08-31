Logic Netlist Simulator
=======================

A small Python tool for gate-level netlists. It reads a VHDL-like structural description, evaluates all input combinations, and generates a truth table CSV and waveform SVG.

How to use
----------
1. Run a netlist:
  python3 logicsim.py example.net

2. Output goes into a timestamped folder under ./outputs, for example:
  - example_truth_table.csv
  - example_waveform.svg

3. Run one specific input assignment:
  python3 logicsim.py example.net --inputs A=1,B=0,C=0 --dot single.dot --ascii single.txt

4. Example helper:
  python3 run_example.py

Netlist format
--------------
entity circuit is
port (
 A : in std_logic;
 B : in std_logic;
 C : in std_logic;
 Y : out std_logic
);
end circuit;

architecture structural of circuit is
signal N1 : std_logic;
signal N2 : std_logic;
begin
G1 : and_gate port map (A, B, N1);
G2 : not_gate port map (C, N2);
G3 : or_gate  port map (N1, N2, Y);
end structural;


OUTPUt

-----
<img width="809" height="217" alt="Screenshot 2026-08-31 at 10 16 32 PM" src="https://github.com/user-attachments/assets/fd905265-8ee2-4d8d-a7e6-d7895a88ecf3" />
Notes
-----
- Default output is logic summary only: CSV + waveform SVG.
- Graph export is optional and mainly for debugging or single-vector runs.
- Example files: example.net, example2.net, example3.net, example_fulladder.net
