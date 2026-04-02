# Quick UV-5R Repeater Programmer

A simple terminal-based Python tool for quickly programming a single memory channel in a Baofeng UV-5R.

This script is meant for fast repeater entry, not full radio management. It reads the existing memory and name record, builds a new record from your inputs, writes it back to the radio, and saves a backup of the old data.

## What it does

* programs one memory location at a time
* sets receive frequency
* sets duplex as `+`, `-`, or `0`
* sets offset
* supports:

  * `NONE`
  * `Tone`
  * `TSQL`
  * `DTCS`
  * `Cross`
* writes a channel name
* saves backup files before writing

## Notes

This script is designed to stay simple.

* Duplex is entered directly as `+`, `-`, or `0`
* Tones are entered directly, not through large menus
* Standard CTCSS tones are checked for validity
* DTCS codes are checked against the UV-5R supported list

This version does **not** do immediate verify after write. In practice, the write may succeed, but the radio may still need to be power-cycled after clone/write mode.

## Requirements

* Python 3
* `pyserial`
* a working UV-5R programming cable
* a serial port that your computer recognizes

Install `pyserial` if needed:

```bash
python3 -m pip install pyserial
```

## Run the script

On macOS or Linux:

```bash
python3 quick_repeater_tines.py --port /dev/cu.usbserial-XXXX
```

On Windows:

```bash
python quick_repeater_tines.py --port COM3
```

Replace the port name with the one used by your cable.

## Find your serial port

### macOS

```bash
ls /dev/cu.*
```

### Linux

```bash
ls /dev/ttyUSB* /dev/ttyACM*
```

### Windows

Check Device Manager under **Ports (COM & LPT)**.

## Example workflow

When you run the script, it will prompt you for:

* memory location
* receive frequency in MHz
* duplex (`+`, `-`, or `0`)
* offset in MHz
* tone mode
* channel name

Depending on tone mode, it will then ask for the needed tone values.

Examples:

* CTCSS tone: `127.3`
* DTCS code: `D411N`
* simplex: duplex `0`, offset `0`

After showing a summary, the script asks you to type `YES` before writing.

## Tone input

### Tone modes

* `NONE`
* `Tone`
* `TSQL`
* `DTCS`
* `Cross`

### Examples

#### No tone

```text
NONE
```

#### Encode only

```text
Tone
127.3
```

#### Tone squelch both directions

```text
TSQL
127.3
```

#### Digital tone both directions

```text
DTCS
D411N
```

#### Cross mode

```text
Cross
127.3
D411N
```

## Duplex input

Use:

* `+`
* `-`
* `0`

Examples:

* repeater with positive offset: `+`
* repeater with negative offset: `-`
* simplex: `0`

## Output files

Before writing, the script saves backups of the original memory and name records:

* `backup_record_XXX_YYYYMMDD_HHMMSS.bin`
* `backup_name_XXX_YYYYMMDD_HHMMSS.bin`

## Important practical note

After writing, you may need to power-cycle the radio before returning to normal use.

That is expected behavior with some clone/write workflows.

## Use at your own risk

This is a simple utility for hobby and practical ham use. Double-check your frequency, offset, and tone settings before writing to the radio.

If your script filename is actually `quick_repeater_tones.py` instead of `quick_repeater_tines.py`, replace those two command lines with the correct filename.
