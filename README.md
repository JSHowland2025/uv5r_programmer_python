# Quick UV-5R Repeater Programmer

A simple terminal-based Python tool for quickly programming a single memory channel in a Baofeng UV-5R.

This script is meant for fast repeater entry, not full radio management. It reads the existing memory and name record, builds a new record from your inputs, writes it back to the radio, and saves a backup of the old data.

## What it does

- programs one memory location at a time
- sets receive frequency
- sets duplex as `+`, `-`, or `0`
- sets offset
- supports:
  - `NONE`
  - `Tone`
  - `TSQL`
  - `DTCS`
  - `Cross`
- writes a channel name
- saves backup files before writing

## Notes

This script is designed to stay simple.

- Duplex is entered directly as `+`, `-`, or `0`
- Tones are entered directly, not through large menus
- Standard CTCSS tones are checked for validity
- DTCS codes are checked against the UV-5R supported list

This version does **not** do immediate verify after write. In practice, the write may succeed, but the radio may still need to be power-cycled after clone/write mode.

## Requirements

- Python 3
- `pyserial`
- a working UV-5R programming cable
- a serial port that your computer recognizes

Install `pyserial` if needed:

```bash
python3 -m pip install pyserial
