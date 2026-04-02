import serial
import time
import struct
import argparse
from datetime import datetime
from pathlib import Path

MAGIC = bytes.fromhex("50 BB FF 20 12 07 25 00")
ACK = b"\x06"
ENQ = b"\x02"

MEM_RECORD_SIZE = 16
NAME_TABLE_OFFSET = 0x1000
NAME_RECORD_SIZE = 16
NAME_LENGTH = 7

DTCS_CODES = [
    23, 25, 26, 31, 32, 36, 43, 47, 51, 53, 54, 65, 71, 72, 73, 74,
    114, 115, 116, 122, 125, 131, 132, 134, 143, 145, 152, 155, 156,
    162, 165, 172, 174, 205, 212, 223, 225, 226, 243, 244, 245, 246,
    251, 252, 255, 261, 263, 265, 266, 271, 274, 306, 311, 315, 325,
    331, 332, 343, 346, 351, 356, 364, 365, 371, 411, 412, 413, 423,
    431, 432, 445, 446, 452, 454, 455, 462, 464, 465, 466, 503, 506,
    516, 523, 526, 532, 546, 565, 606, 612, 624, 627, 631, 632, 654,
    662, 664, 703, 712, 723, 731, 732, 734, 743, 754
]

CTCSS_TONES = [
    67.0, 69.3, 71.9, 74.4, 77.0, 79.7, 82.5, 85.4, 88.5, 91.5,
    94.8, 97.4, 100.0, 103.5, 107.2, 110.9, 114.8, 118.8, 123.0,
    127.3, 131.8, 136.5, 141.3, 146.2, 151.4, 156.7, 159.8, 162.2,
    165.5, 167.9, 171.3, 173.8, 177.3, 179.9, 183.5, 186.2, 189.9,
    192.8, 196.6, 199.5, 203.5, 206.5, 210.7, 218.1, 225.7, 229.1,
    233.6, 241.8, 250.3, 254.1
]

PTTID_MAP = {
    "OFF": 0,
    "BOT": 1,
    "EOT": 2,
    "BOTH": 3,
}


def hx(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def bcd_bytes_to_int_le(data: bytes) -> int:
    digits = ""
    for b in reversed(data):
        hi = (b >> 4) & 0x0F
        lo = b & 0x0F
        if hi > 9 or lo > 9:
            raise ValueError(f"Invalid BCD byte: 0x{b:02X}")
        digits += f"{hi}{lo}"
    return int(digits)


def int_to_bcd_le_4(value: int) -> bytes:
    s = f"{value:08d}"
    out = bytearray()
    for i in range(0, 8, 2):
        hi = int(s[i])
        lo = int(s[i + 1])
        out.append((hi << 4) | lo)
    out.reverse()
    return bytes(out)


def freq_bytes_to_mhz(data: bytes) -> float:
    return bcd_bytes_to_int_le(data) / 100000.0


def mhz_to_freq_bytes(mhz: float) -> bytes:
    if mhz <= 0:
        raise ValueError("Frequency must be > 0")
    return int_to_bcd_le_4(round(mhz * 100000.0))


def decode_tone(word: int):
    if word in (0, 0xFFFF):
        return {"mode": "", "value": None, "polarity": "N"}

    if word >= 0x0258:
        return {"mode": "Tone", "value": word / 10.0, "polarity": "N"}

    ndcs = len(DTCS_CODES)
    if 1 <= word <= ndcs:
        return {"mode": "DTCS", "value": DTCS_CODES[word - 1], "polarity": "N"}
    if (ndcs + 2) <= word <= (2 * ndcs + 1):
        return {"mode": "DTCS", "value": DTCS_CODES[word - ndcs - 2], "polarity": "R"}

    return {"mode": "Unknown", "value": word, "polarity": "?"}


def encode_tone_token(token: str) -> int:
    token = token.strip().upper()
    if token in ("", "0", "OFF", "NONE", "-"):
        return 0

    if token.startswith("D"):
        if len(token) < 5:
            raise ValueError(f"Invalid DCS token: {token}")
        code_str = token[1:-1]
        pol = token[-1]
        try:
            code = int(code_str)
        except ValueError:
            raise ValueError(f"Invalid DCS code: {token}")

        if code not in DTCS_CODES:
            raise ValueError(f"DCS code {code} not in UV-5R DTCS table")

        index = DTCS_CODES.index(code) + 1
        if pol == "N":
            return index
        elif pol in ("R", "I"):
            return index + len(DTCS_CODES) + 1
        else:
            raise ValueError(f"Invalid DCS polarity in {token}; use N or R")

    try:
        hz = float(token)
    except ValueError:
        raise ValueError(f"Invalid tone token: {token}")

    hz = round(hz, 1)
    if hz not in CTCSS_TONES:
        valid = ", ".join(f"{t:.1f}" for t in CTCSS_TONES)
        raise ValueError(
            f"CTCSS tone {hz:.1f} is not a standard tone.\n"
            f"Valid tones are: {valid}"
        )

    value = round(hz * 10.0)
    if value < 0x0258:
        raise ValueError(f"CTCSS tone too small/invalid for UV-5R encoding: {token}")
    return value


def decode_duplex_offset(rx_mhz: float, tx_mhz: float):
    if abs(rx_mhz - tx_mhz) < 0.00001:
        return ("", 0.0)
    if tx_mhz > rx_mhz:
        return ("+", round(tx_mhz - rx_mhz, 5))
    return ("-", round(rx_mhz - tx_mhz, 5))


def compute_tx_mhz(rx_mhz: float, duplex: str, offset_mhz: float) -> float:
    duplex = duplex.strip().lower()
    if duplex in ("", "none", "off", "simplex", "s", "0"):
        return rx_mhz
    if duplex == "+":
        return rx_mhz + offset_mhz
    if duplex == "-":
        return rx_mhz - offset_mhz
    raise ValueError("Duplex must be +, -, or 0/none")


def sanitize_name(name: str) -> str:
    name = name.strip().upper()
    if len(name) > NAME_LENGTH:
        raise ValueError(f"Channel name must be at most {NAME_LENGTH} characters")
    try:
        name.encode("ascii", errors="strict")
    except UnicodeEncodeError:
        raise ValueError("Channel name must be ASCII")
    return name


def build_name_record(existing_raw: bytes, name: str) -> bytes:
    if len(existing_raw) != NAME_RECORD_SIZE:
        raise ValueError(f"Existing name record must be {NAME_RECORD_SIZE} bytes")

    name = sanitize_name(name)
    out = bytearray(existing_raw)
    padded = name.encode("ascii").ljust(NAME_LENGTH, b"\xFF")
    out[:NAME_LENGTH] = padded
    return bytes(out)


def parse_name_record(raw: bytes) -> dict:
    chars = []
    for b in raw[:NAME_LENGTH]:
        if b in (0x00, 0xFF):
            break
        chars.append(chr(b))
    return {"name": "".join(chars), "raw_hex": hx(raw)}


def decode_record_flags(raw: bytes) -> dict:
    byte15 = raw[15]
    byte14 = raw[14]

    return {
        "lowpower": byte14 & 0x01,
        "pttid": byte15 & 0x03,
        "scan": (byte15 >> 2) & 0x01,
        "bcl": (byte15 >> 3) & 0x01,
        "wide": (byte15 >> 6) & 0x01,
    }


def encode_record_flags(existing_raw: bytes, *, wide: bool, lowpower: bool, scan: bool, bcl: bool, pttid_mode: str) -> bytes:
    out = bytearray(existing_raw)

    if lowpower:
        out[14] |= 0x01
    else:
        out[14] &= 0xFE

    out[15] &= ~0x4F
    out[15] |= PTTID_MAP[pttid_mode.upper()]

    if scan:
        out[15] |= 0x04
    if bcl:
        out[15] |= 0x08
    if wide:
        out[15] |= 0x40

    return bytes(out)


def parse_mem_record(raw: bytes):
    rx_mhz = freq_bytes_to_mhz(raw[0:4])
    tx_mhz = freq_bytes_to_mhz(raw[4:8])

    rxtone_word = int.from_bytes(raw[8:10], "little")
    txtone_word = int.from_bytes(raw[10:12], "little")

    rxtone = decode_tone(rxtone_word)
    txtone = decode_tone(txtone_word)

    duplex, offset = decode_duplex_offset(rx_mhz, tx_mhz)
    flags = decode_record_flags(raw)

    if txtone["mode"] == "Tone" and not rxtone["mode"]:
        tone_mode = "Tone"
    elif txtone["mode"] == "Tone" and rxtone["mode"] == "Tone" and txtone["value"] == rxtone["value"]:
        tone_mode = "TSQL"
    elif txtone["mode"] == "DTCS" and rxtone["mode"] == "DTCS" and txtone["value"] == rxtone["value"]:
        tone_mode = "DTCS"
    elif txtone["mode"] or rxtone["mode"]:
        tone_mode = "Cross"
    else:
        tone_mode = ""

    return {
        "raw_hex": hx(raw),
        "rx_mhz": rx_mhz,
        "tx_mhz": tx_mhz,
        "duplex": duplex,
        "offset_mhz": offset,
        "rxtone_word": f"0x{rxtone_word:04X}",
        "txtone_word": f"0x{txtone_word:04X}",
        "rxtone": rxtone,
        "txtone": txtone,
        "tone_mode": tone_mode,
        "flags_12_15": raw[12:16],
        "flags": flags,
    }


def ident_radio(ser):
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(0.15)

    for b in MAGIC:
        ser.write(bytes([b]))
        time.sleep(0.01)

    ack1 = ser.read(1)
    print(f"ack1: {hx(ack1)}")
    if ack1 != ACK:
        raise RuntimeError(f"No ACK after magic: {hx(ack1)}")

    ser.write(ENQ)
    ser.flush()
    time.sleep(0.15)

    ident = b""
    for _ in range(12):
        byte = ser.read(1)
        if not byte:
            break
        ident += byte
        if byte == b"\xDD":
            break

    print(f"ident: {hx(ident)}")
    if len(ident) < 8 or not ident.endswith(b"\xDD"):
        raise RuntimeError(f"Bad ident reply: {hx(ident)}")

    ser.write(ACK)
    ser.flush()
    time.sleep(0.05)

    ack2 = ser.read(1)
    print(f"ack2: {hx(ack2)}")
    if ack2 != ACK:
        raise RuntimeError(f"Radio refused clone after ident: {hx(ack2)}")

    return ident


def read_block(ser, start, size=0x40, first_command=False):
    cmd = struct.pack(">BHB", ord("S"), start, size)
    ser.write(cmd)

    if not first_command:
        ack = ser.read(1)
        if ack != ACK:
            raise RuntimeError(f"Missing pre-header ACK for block 0x{start:04X}: {hx(ack)}")

    header = ser.read(4)
    if len(header) != 4:
        raise RuntimeError("Short block header")

    cmd_byte, addr, length = struct.unpack(">BHB", header)
    if cmd_byte != ord("X") or addr != start or length != size:
        raise RuntimeError(f"Unexpected block header: {hx(header)}")

    data = ser.read(size)
    if len(data) != size:
        raise RuntimeError("Short block data")

    ser.write(ACK)
    time.sleep(0.03)
    return data


def write_block_16(ser, start, data):
    if len(data) != 0x10:
        raise ValueError("UV-5R write block must be 16 bytes here")

    cmd = struct.pack(">BHB", ord("X"), start, 0x10)
    ser.write(cmd)
    ser.write(data)

    ack = ser.read(1)
    if ack != ACK:
        raise RuntimeError(f"Bad/missing ACK after write to 0x{start:04X}: {hx(ack)}")


def read_record(ser, recno: int, first_command: bool) -> bytes:
    addr = recno * MEM_RECORD_SIZE
    first_block_start = addr - (addr % 0x40)
    block = read_block(ser, first_block_start, 0x40, first_command=first_command)
    offset = addr - first_block_start
    return block[offset:offset + 16]


def read_name_record(ser, recno: int, first_command: bool) -> bytes:
    addr = NAME_TABLE_OFFSET + (recno * NAME_RECORD_SIZE)
    first_block_start = addr - (addr % 0x40)
    block = read_block(ser, first_block_start, 0x40, first_command=first_command)
    offset = addr - first_block_start
    return block[offset:offset + NAME_RECORD_SIZE]


def write_name_record(ser, recno: int, data: bytes):
    addr = NAME_TABLE_OFFSET + (recno * NAME_RECORD_SIZE)
    write_block_16(ser, addr, data)


def build_tone_words_simple(tone_mode: str):
    mode = tone_mode.strip().upper()

    if mode in ("", "NONE", "OFF"):
        return 0, 0

    if mode == "TONE":
        tx = input("TX tone (e.g. 127.3): ").strip()
        return 0, encode_tone_token(tx)

    if mode == "TSQL":
        tone = input("Tone for both RX/TX (e.g. 127.3): ").strip()
        word = encode_tone_token(tone)
        return word, word

    if mode in ("DTCS", "DCS"):
        code = input("DTCS code for both RX/TX (e.g. D411N): ").strip()
        word = encode_tone_token(code)
        return word, word

    if mode == "CROSS":
        rx = input("RX tone/DCS (e.g. 127.3 or D411N): ").strip()
        tx = input("TX tone/DCS (e.g. 127.3 or D411N): ").strip()
        return encode_tone_token(rx), encode_tone_token(tx)

    raise ValueError("Tone mode must be NONE, Tone, TSQL, DTCS, or Cross")


def build_record_from_inputs(existing_raw: bytes, rx_mhz: float, duplex: str, offset_mhz: float, rxtone_word: int, txtone_word: int) -> bytes:
    tx_mhz = compute_tx_mhz(rx_mhz, duplex, offset_mhz)

    new_raw = bytearray(existing_raw)
    new_raw[0:4] = mhz_to_freq_bytes(rx_mhz)
    new_raw[4:8] = mhz_to_freq_bytes(tx_mhz)
    new_raw[8:10] = int(rxtone_word).to_bytes(2, "little")
    new_raw[10:12] = int(txtone_word).to_bytes(2, "little")

    new_raw = bytearray(encode_record_flags(
        bytes(new_raw),
        wide=True,
        lowpower=False,
        scan=True,
        bcl=False,
        pttid_mode="OFF",
    ))

    return bytes(new_raw)


def prompt_text(label: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    val = input(f"{label}{suffix}: ").strip()
    return val if val else default


def prompt_float(label: str, default=None) -> float:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if not raw and default is not None:
            return float(default)
        try:
            return float(raw)
        except ValueError:
            print("Please enter a number.")


def prompt_int(label: str, minv: int, maxv: int, default=None) -> int:
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = input(f"{label}{suffix}: ").strip()
        if not raw and default is not None:
            value = int(default)
        else:
            try:
                value = int(raw)
            except ValueError:
                print("Please enter an integer.")
                continue
        if minv <= value <= maxv:
            return value
        print(f"Please enter a value between {minv} and {maxv}.")


def print_summary(title: str, record: int, decoded: dict, name: str):
    print(f"\n{title}")
    print("-" * 50)
    print(f"Memory number : {record}")
    print(f"Name          : {name}")
    print(f"RX frequency  : {decoded['rx_mhz']:.5f} MHz")
    print(f"TX frequency  : {decoded['tx_mhz']:.5f} MHz")
    print(f"Duplex        : {decoded['duplex']}")
    print(f"Offset        : {decoded['offset_mhz']:.5f} MHz")
    print(f"Tone mode     : {decoded['tone_mode']}")
    print(f"RX tone       : {decoded['rxtone']}")
    print(f"TX tone       : {decoded['txtone']}")


def main():
    parser = argparse.ArgumentParser(description="Quick UV-5R repeater programmer")
    parser.add_argument("--port", required=True, help="Serial port, e.g. COM3 or /dev/cu.usbserial-AB0JSQN9")
    parser.add_argument("--baud", type=int, default=9600, help="Baud rate, default 9600")
    args = parser.parse_args()

    record = prompt_int("Memory location", 0, 127)
    rx_mhz = prompt_float("Receive frequency in MHz")
    duplex = prompt_text("Duplex (+, -, 0)", "0")
    offset_mhz = prompt_float("Offset in MHz (0 for simplex)", 0)
    tone_mode = prompt_text("Tone mode (NONE, Tone, TSQL, DTCS, Cross)", "NONE")
    channel_name = prompt_text("Channel name (max 7 chars)", "")

    rxtone_word, txtone_word = build_tone_words_simple(tone_mode)

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        ident = ident_radio(ser)
        print(f"\nIDENT (read session): {hx(ident)}")
        old_raw = read_record(ser, record, first_command=True)
        old_name_raw = read_name_record(ser, record, first_command=False)

    old_name = parse_name_record(old_name_raw)["name"]

    new_raw = build_record_from_inputs(
        existing_raw=old_raw,
        rx_mhz=rx_mhz,
        duplex=duplex,
        offset_mhz=offset_mhz,
        rxtone_word=rxtone_word,
        txtone_word=txtone_word,
    )
    new_name_raw = build_name_record(old_name_raw, channel_name)

    new_decoded = parse_mem_record(new_raw)
    print_summary("New record to write", record, new_decoded, channel_name)

    confirm = prompt_text("Type YES to write", "NO")
    if confirm != "YES":
        print("Aborted. Nothing written.")
        return

    rec_addr = record * MEM_RECORD_SIZE
    name_addr = NAME_TABLE_OFFSET + (record * NAME_RECORD_SIZE)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    Path(f"backup_record_{record:03d}_{stamp}.bin").write_bytes(old_raw)
    Path(f"backup_name_{record:03d}_{stamp}.bin").write_bytes(old_name_raw)

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        ident = ident_radio(ser)
        print(f"\nIDENT (write session): {hx(ident)}")
        write_block_16(ser, rec_addr, new_raw)
        time.sleep(0.15)
        write_name_record(ser, record, new_name_raw)
        time.sleep(0.25)

    print(f"Wrote memory record at address 0x{rec_addr:04X}")
    print(f"Wrote name record at address 0x{name_addr:04X}")
    print("Defaults used: Wide, High power, Include in scan, BCL Off, PTT ID Off")
    print("\nWrite completed.")
    print("Power-cycle the radio after clone/write mode if needed.")


if __name__ == "__main__":
    main()
