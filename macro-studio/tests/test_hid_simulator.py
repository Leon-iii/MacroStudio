from struct import pack

import pytest

from macro_studio.external_hid.codec import Frame, FrameParser, Opcode, decode, encode
from macro_studio.external_hid.simulator import McuSimulator


SESSION = 0xAABBCCDD
NEXT_SESSION = 0x11223344
KEY_DOWN = bytes.fromhex("01 01 07 00 04 00")
KEY_UP = bytes.fromhex("01 02 07 00 04 00")
BUTTON_DOWN = bytes.fromhex("02 01 01")
BUTTON_UP = bytes.fromhex("02 02 01")
MOVE = bytes.fromhex("03 F9 FF 09 00")


def _wire(opcode, seq, payload=b"", *, session=SESSION, minor=0):
    return encode(Frame(opcode, payload, seq=seq, session_id=session, minor=minor))


def _exchange(device, opcode, seq, payload=b"", *, session=SESSION, minor=0):
    wire = _wire(opcode, seq, payload, session=session, minor=minor)
    response = decode(device.receive(wire))
    assert (response.seq, response.session_id) == (seq, session)
    return response


def _hello(device, session=SESSION):
    return _exchange(device, Opcode.HELLO, 1, pack("<BBI", 1, 0, session), session=session)


def _armed_device():
    device = McuSimulator()
    assert _hello(device).opcode == Opcode.HELLO_ACK
    assert _exchange(device, Opcode.ARM_SESSION, 2).opcode == Opcode.ACK
    return device


def _status(device, seq, session=0):
    response = _exchange(device, Opcode.GET_STATUS, seq, session=session)
    assert response.opcode == Opcode.STATUS
    return response.payload


def _assert_nack(response, opcode, error):
    assert response.opcode == Opcode.NACK
    assert response.payload == pack("<BH", opcode, error)


def test_requires_valid_hello_and_explicit_arm():
    device = McuSimulator()
    assert _status(device, 0) == bytes.fromhex("00 00 00 00 00")
    _assert_nack(_exchange(device, Opcode.INPUT_EVENT, 1, KEY_DOWN), Opcode.INPUT_EVENT, 4)
    _assert_nack(_exchange(device, Opcode.ARM_SESSION, 2, session=0), Opcode.ARM_SESSION, 3)

    incompatible = _exchange(device, Opcode.HELLO, 3, pack("<BBI", 2, 0, SESSION))
    _assert_nack(incompatible, Opcode.HELLO, 8)
    inconsistent = _exchange(device, Opcode.HELLO, 4, pack("<BBI", 1, 0, NEXT_SESSION))
    _assert_nack(inconsistent, Opcode.HELLO, 4)
    _assert_nack(
        _exchange(device, Opcode.HELLO, 5, pack("<BBI", 1, 0, 0), session=0),
        Opcode.HELLO,
        4,
    )
    assert _status(device, 6) == bytes.fromhex("00 00 00 08 00")
    assert device.pressed_keys == frozenset()

    hello = _hello(device)
    assert (hello.opcode, hello.payload) == (Opcode.HELLO_ACK, b"\x01\x00\x00\x01\x00\x01simulator")
    assert _hello(device) == hello
    caps = _exchange(device, Opcode.GET_CAPS, 7)
    assert (caps.opcode, caps.payload) == (Opcode.CAPS, bytes.fromhex("07 40 DC 05 00"))
    assert _status(device, 8, SESSION) == bytes.fromhex("01 00 00 00 00")
    _assert_nack(_exchange(device, Opcode.INPUT_EVENT, 9, KEY_DOWN), Opcode.INPUT_EVENT, 3)
    assert device.pressed_keys == frozenset()

    arm = _exchange(device, Opcode.ARM_SESSION, 10)
    assert (arm.opcode, arm.payload) == (Opcode.ACK, b"\x03")
    assert _status(device, 11, SESSION) == bytes.fromhex("02 00 00 00 00")


def test_keyboard_buttons_and_relative_events_change_only_simulated_state():
    device = _armed_device()
    for seq, payload in enumerate((KEY_DOWN, KEY_DOWN, BUTTON_DOWN, MOVE), start=3):
        response = _exchange(device, Opcode.INPUT_EVENT, seq, payload)
        assert (response.opcode, response.payload) == (Opcode.ACK, b"\x06")
    assert device.pressed_keys == {(0x07, 0x04)}
    assert device.pressed_buttons == {1}
    assert device.pointer_offset == (-7, 9)

    assert _exchange(device, Opcode.INPUT_EVENT, 7, KEY_UP).opcode == Opcode.ACK
    assert _exchange(device, Opcode.INPUT_EVENT, 8, BUTTON_UP).opcode == Opcode.ACK
    assert device.pressed_keys == frozenset()
    assert device.pressed_buttons == frozenset()


@pytest.mark.parametrize("opcode", [Opcode.RELEASE_ALL, Opcode.DISARM_SESSION])
def test_close_is_idempotent_and_prevents_following_input(opcode):
    device = _armed_device()
    assert _exchange(device, Opcode.INPUT_EVENT, 3, KEY_DOWN).opcode == Opcode.ACK
    assert _exchange(device, Opcode.INPUT_EVENT, 4, BUTTON_DOWN).opcode == Opcode.ACK
    packet = _wire(opcode, 4)
    later_input = _wire(Opcode.INPUT_EVENT, 5, MOVE)
    first, repeat, rejected = FrameParser().feed(device.receive(packet + packet + later_input))
    assert first == repeat == Frame(Opcode.ACK, bytes((opcode,)), seq=4, session_id=SESSION)
    _assert_nack(rejected, Opcode.INPUT_EVENT, 4)
    assert _exchange(device, Opcode.DISARM_SESSION, 6).opcode == Opcode.ACK
    assert device.pressed_keys == device.pressed_buttons == frozenset()
    assert device.pointer_offset == (0, 0)
    assert _status(device, 7) == bytes.fromhex("00 00 00 00 00")
    assert device.advance(1500) == b""


def test_duplicate_move_cannot_reapply_and_sequence_numbers_can_wrap():
    device = _armed_device()
    assert _exchange(device, Opcode.INPUT_EVENT, 0xFFFE, MOVE).opcode == Opcode.ACK
    assert _exchange(device, Opcode.INPUT_EVENT, 0xFFFF, MOVE).opcode == Opcode.ACK
    first = _exchange(device, Opcode.INPUT_EVENT, 0, MOVE)
    duplicate = _exchange(device, Opcode.INPUT_EVENT, 0xFFFF, MOVE)
    assert duplicate == Frame(Opcode.ACK, first.payload, seq=0xFFFF, session_id=SESSION)
    conflict = _exchange(device, Opcode.INPUT_EVENT, 0, KEY_DOWN)
    _assert_nack(conflict, Opcode.INPUT_EVENT, 5)
    assert device.pointer_offset == (-21, 27)
    assert device.pressed_keys == frozenset()


def test_watchdog_uses_fresh_current_session_heartbeats_and_retires_session():
    device = _armed_device()
    assert _exchange(device, Opcode.INPUT_EVENT, 3, KEY_DOWN).opcode == Opcode.ACK
    assert _exchange(device, Opcode.INPUT_EVENT, 4, BUTTON_DOWN).opcode == Opcode.ACK
    assert device.advance(1000) == b""
    heartbeat = _exchange(device, Opcode.HEARTBEAT, 5)
    assert heartbeat.opcode == Opcode.ACK
    assert device.advance(1499) == b""
    assert _exchange(device, Opcode.HEARTBEAT, 5) == heartbeat
    stale = _exchange(device, Opcode.HEARTBEAT, 6, session=NEXT_SESSION, minor=7)
    _assert_nack(stale, Opcode.HEARTBEAT, 4)
    assert _exchange(device, Opcode.INPUT_EVENT, 7, MOVE).opcode == Opcode.ACK
    assert device.pressed_keys == {(0x07, 0x04)}
    assert device.pressed_buttons == {1}

    assert decode(device.advance(1)) == Frame(Opcode.FAULT, b"\x07\x00", session_id=SESSION)
    assert device.advance(1500) == b""
    assert device.pressed_keys == device.pressed_buttons == frozenset()
    assert _status(device, 8) == bytes.fromhex("00 00 00 07 00")
    _assert_nack(_exchange(device, Opcode.ARM_SESSION, 9), Opcode.ARM_SESSION, 4)
    _assert_nack(_hello(device), Opcode.HELLO, 4)
    assert _hello(device, NEXT_SESSION).opcode == Opcode.HELLO_ACK
    assert _status(device, 10, NEXT_SESSION) == bytes.fromhex("01 00 00 00 00")


def test_disconnect_discards_partial_stream_and_ignores_stale_frames():
    device = _armed_device()
    assert _exchange(device, Opcode.INPUT_EVENT, 3, KEY_DOWN).opcode == Opcode.ACK
    assert device.receive(_wire(Opcode.INPUT_EVENT, 4, MOVE)[:4]) == b""
    device.disconnect()
    assert device.pressed_keys == frozenset()
    assert _hello(device, NEXT_SESSION).opcode == Opcode.HELLO_ACK
    assert _exchange(device, Opcode.ARM_SESSION, 2, session=NEXT_SESSION).opcode == Opcode.ACK
    for opcode, payload in ((Opcode.INPUT_EVENT, MOVE), (Opcode.RELEASE_ALL, b"")):
        _assert_nack(_exchange(device, opcode, 5, payload), opcode, 4)
    assert device.receive(_wire(Opcode.ACK, 3, b"\x06")) == b""
    assert _exchange(device, Opcode.INPUT_EVENT, 3, MOVE, session=NEXT_SESSION).opcode == Opcode.ACK
    assert device.pointer_offset == (-7, 9)
    device.disconnect()
    _assert_nack(_hello(device), Opcode.HELLO, 4)


def test_session_replay_window_keeps_the_most_recent_32_tokens():
    device = McuSimulator()
    for session in range(1, 33):
        assert _hello(device, session).opcode == Opcode.HELLO_ACK
        assert _exchange(device, Opcode.DISARM_SESSION, 2, session=session).opcode == Opcode.ACK
    _assert_nack(_hello(device, 1), Opcode.HELLO, 4)

    assert _hello(device, 33).opcode == Opcode.HELLO_ACK
    device.disconnect()
    _assert_nack(_hello(device, 2), Opcode.HELLO, 4)
    _assert_nack(_exchange(device, Opcode.ARM_SESSION, 2, session=1), Opcode.ARM_SESSION, 4)
    assert _hello(device, 1).opcode == Opcode.HELLO_ACK
    assert _status(device, 3, 1) == bytes.fromhex("01 00 00 00 00")


def test_corrupt_packet_neutralizes_before_subsequent_valid_packet_and_parser_recovers():
    device = _armed_device()
    assert _exchange(device, Opcode.INPUT_EVENT, 3, KEY_DOWN).opcode == Opcode.ACK
    corrupt = bytearray(_wire(Opcode.INPUT_EVENT, 4, MOVE))
    corrupt[-2] ^= 0x80
    assert device.receive(bytes(corrupt[:-1])) == b""

    old_move = _wire(Opcode.INPUT_EVENT, 5, MOVE)
    fresh_hello = _wire(Opcode.HELLO, 1, pack("<BBI", 1, 0, NEXT_SESSION), session=NEXT_SESSION)
    fault, rejected, hello = FrameParser().feed(device.receive(b"\x00" + old_move + fresh_hello))
    assert fault == Frame(Opcode.FAULT, b"\x06\x00", session_id=SESSION)
    _assert_nack(rejected, Opcode.INPUT_EVENT, 4)
    assert hello.opcode == Opcode.HELLO_ACK
    assert hello.session_id == NEXT_SESSION
    assert device.pressed_keys == frozenset()
    assert device.pointer_offset == (0, 0)


@pytest.mark.parametrize(
    "payload",
    [b"", b"\x04", KEY_DOWN[:-1], KEY_DOWN + b"\x00", b"\x02\x00\x01", MOVE[:-1]],
)
def test_malformed_input_disarms_with_error(payload):
    device = _armed_device()
    assert _exchange(device, Opcode.INPUT_EVENT, 3, KEY_DOWN).opcode == Opcode.ACK
    _assert_nack(_exchange(device, Opcode.INPUT_EVENT, 4, payload), Opcode.INPUT_EVENT, 1)
    assert _status(device, 5) == bytes.fromhex("00 00 00 01 00")
    assert device.pressed_keys == frozenset()
    assert device.pointer_offset == (0, 0)


def test_unsupported_usage_button_and_job_are_rejected():
    device = _armed_device()
    for seq, payload in enumerate((bytes.fromhex("01 01 08 00 04 00"), b"\x02\x01\x04"), start=3):
        _assert_nack(_exchange(device, Opcode.INPUT_EVENT, seq, payload), Opcode.INPUT_EVENT, 2)
    _assert_nack(_exchange(device, Opcode.START_JOB, 5, b"\x01\x00"), Opcode.START_JOB, 2)
    assert _status(device, 6, SESSION) == bytes.fromhex("02 00 00 00 00")
    assert device.pressed_keys == device.pressed_buttons == frozenset()
