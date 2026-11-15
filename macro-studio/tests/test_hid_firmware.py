from pathlib import Path
import shutil
from struct import pack
import subprocess

import pytest

from macro_studio.external_hid import Frame, FrameParser, Opcode, encode


def test_leonardo_sketch_accepts_host_frames_and_releases_inputs(tmp_path):
    compiler = shutil.which("g++")
    if compiler is None:
        pytest.skip("g++ is required for the native Leonardo protocol test")
    tests = Path(__file__).parent
    stubs = tests / "firmware_stub"
    sketch = tests.parent / "firmware" / "MacroStudioLeonardo" / "streaming.cpp"
    program = tmp_path / "firmware.exe"
    subprocess.run(
        [compiler, "-std=c++11", "-I", stubs, stubs / "main.cpp", sketch, "-o", program],
        check=True, capture_output=True, text=True, timeout=30,
    )

    def frame(opcode, seq, payload=b"", token=0x3412):
        return encode(Frame(opcode, payload, seq=seq, session_id=token)).hex()

    hello = frame(Opcode.HELLO, 1, pack("<BBI", 1, 0, 0x3412))
    arm = frame(Opcode.ARM_SESSION, 2)
    key = frame(Opcode.INPUT_EVENT, 3, pack("<BBHH", 1, 1, 7, 4))
    button = frame(Opcode.INPUT_EVENT, 4, pack("<BBB", 2, 1, 1))
    move = frame(Opcode.INPUT_EVENT, 5, pack("<Bhh", 3, 7, 0))
    heartbeat = frame(Opcode.HEARTBEAT, 6)
    release = frame(Opcode.RELEASE_ALL, 0)
    hello_again = frame(Opcode.HELLO, 1, pack("<BBI", 1, 0, 0x7654), token=0x7654)
    arm_again = frame(Opcode.ARM_SESSION, 2, token=0x7654)
    key_again = frame(Opcode.INPUT_EVENT, 3, pack("<BBHH", 1, 1, 7, 4), token=0x7654)
    hello_after_release = frame(Opcode.HELLO, 1, pack("<BBI", 1, 0, 0x5432), token=0x5432)
    arm_after_release = frame(Opcode.ARM_SESSION, 2, token=0x5432)
    key_after_release = frame(Opcode.INPUT_EVENT, 3, pack("<BBHH", 1, 1, 7, 4), token=0x5432)
    corrupt = bytearray.fromhex(key_after_release)
    corrupt[-2] ^= 0x80 if corrupt[-2] != 0x80 else 0x40
    caps = frame(Opcode.GET_CAPS, 7)
    inputs = [
        (0, 1, hello), (0, 1, key), (0, 1, arm), (0, 1, key), (0, 1, button),
        (0, 1, move), (0, 1, move), (1000, 1, heartbeat), (0, 1, caps),
        (1499, 1, key), (1, 1, "-"), (0, 1, release),
        (0, 1, hello_again), (0, 1, arm_again), (0, 1, key_again),
        (0, 1, frame(Opcode.RELEASE_ALL, 0, token=0x7654)),
        (0, 1, hello_after_release), (0, 1, arm_after_release), (0, 1, key_after_release),
        (0, 1, corrupt.hex()),
        (0, 1, frame(Opcode.HELLO, 1, pack("<BBI", 1, 0, 0x4321), token=0x4321)),
        (0, 1, frame(Opcode.ARM_SESSION, 2, token=0x4321)),
        (0, 1, frame(Opcode.INPUT_EVENT, 3, pack("<BBHH", 1, 1, 7, 4), token=0x4321)),
        (0, 0, "-"),
    ]
    result = subprocess.run(
        [program],
        input="".join(f"{ms} {connected} {packet}\n" for ms, connected, packet in inputs),
        check=True, capture_output=True, text=True, timeout=10,
    )
    rows = []
    for row in result.stdout.splitlines():
        wire, keys, buttons, x = row.split(" ")
        replies = FrameParser().feed(bytes.fromhex(wire))
        rows.append((replies, int(keys), int(buttons), int(x)))
    assert [replies[0].opcode if replies else None for replies, *_ in rows] == [
        Opcode.HELLO_ACK, Opcode.NACK, Opcode.ACK, Opcode.ACK, Opcode.ACK,
        Opcode.ACK, Opcode.ACK, Opcode.ACK, Opcode.CAPS, Opcode.NACK, Opcode.FAULT,
        Opcode.ACK, Opcode.HELLO_ACK, Opcode.ACK, Opcode.ACK, Opcode.ACK,
        Opcode.HELLO_ACK, Opcode.ACK, Opcode.ACK, Opcode.FAULT,
        Opcode.HELLO_ACK, Opcode.ACK, Opcode.ACK, None,
    ]
    assert rows[8][0][0].payload == pack("<BBHB", 15, 64, 1500, 1)
    assert rows[9][1:] == (1, 1, 7)
    assert rows[10][1:] == (0, 0, 7)
    assert rows[14][1] == 1
    assert rows[15][1] == 0
    assert rows[18][1] == 1
    assert rows[19][0][0].payload == pack("<H", 6)
    assert rows[19][1] == 0
    assert rows[22][1] == 1
    assert rows[23][1] == 0

    token = 0x2110
    train = [
        (0, frame(Opcode.HELLO, 1, pack("<BBI", 1, 0, token), token=token)),
        (0, frame(Opcode.ARM_SESSION, 2, token=token)),
        (0, frame(Opcode.PREPARE_JOB, 3, pack("<HHHIII", 1, 7, 4, 10_000, 3_000, 2), token=token)),
        (0, frame(Opcode.START_JOB, 4, pack("<H", 1), token=token)),
        (3, "-"), (7, "-"), (3, "-"),
        (0, frame(Opcode.PREPARE_JOB, 5, pack("<HHHIII", 2, 7, 4, 10_000, 3_000, 10), token=token)),
        (0, frame(Opcode.START_JOB, 6, pack("<H", 2), token=token)),
        (0, frame(Opcode.CANCEL_JOB, 7, pack("<H", 2), token=token)),
        (0, frame(Opcode.PREPARE_JOB, 8, pack("<HHHIII", 3, 7, 4, 10_000, 3_000, 10), token=token)),
        (0, frame(Opcode.START_JOB, 9, pack("<H", 3), token=token)),
        (1500, "-"),
    ]
    result = subprocess.run(
        [program], input="".join(f"{ms} 1 {packet}\n" for ms, packet in train),
        check=True, capture_output=True, text=True, timeout=10,
    )
    rows = [row.split(" ") for row in result.stdout.splitlines()]
    assert [int(row[1]) for row in rows] == [0, 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 1, 0]
    replies = [FrameParser().feed(bytes.fromhex(row[0])) for row in rows]
    assert len(replies[3]) == 1 and replies[3][0].opcode is Opcode.ACK
    assert replies[6][0] == Frame(Opcode.JOB_DONE, pack("<HBIH", 1, 0, 2, 0), session_id=token)
    assert replies[9][1] == Frame(Opcode.JOB_DONE, pack("<HBIH", 2, 1, 1, 0), session_id=token)
    assert replies[12][0] == Frame(Opcode.FAULT, pack("<H", 7), session_id=token)

    batch = pack("<HB", 1, 2) + pack("<IBbb", 0, 1, 4, 0) + pack("<IBbb", 10_000, 2, 4, 0)
    invalid = pack("<HB", 1, 2) + pack("<IBbb", 0, 1, 4, 0) + pack("<IBbb", 10_000, 2, 5, 0)
    commands = [
        (0, frame(Opcode.HELLO, 1, pack("<BBI", 1, 0, token), token=token)),
        (0, frame(Opcode.ARM_SESSION, 2, token=token)),
        (0, frame(Opcode.QUEUE_BATCH, 3, invalid, token=token)),
        (0, frame(Opcode.START_JOB, 4, pack("<H", 1), token=token)),
        (0, frame(Opcode.QUEUE_BATCH, 5, batch, token=token)),
        (12, "-"),
        (0, frame(Opcode.START_JOB, 6, pack("<H", 1), token=token)),
        (10, "-"),
    ]
    result = subprocess.run(
        [program], input="".join(f"{ms} 1 {packet}\n" for ms, packet in commands),
        check=True, capture_output=True, text=True, timeout=10,
    )
    rows = [row.split(" ") for row in result.stdout.splitlines()]
    replies = [FrameParser().feed(bytes.fromhex(row[0])) for row in rows]
    assert [int(row[1]) for row in rows] == [0, 0, 0, 0, 0, 0, 1, 0]
    assert replies[2][0].opcode == replies[3][0].opcode == Opcode.NACK
    assert replies[4][0].payload == b"\x0c\x01\x00"
    assert replies[7][0] == Frame(Opcode.JOB_DONE, pack("<HBIH", 1, 0, 2, 0), session_id=token)
