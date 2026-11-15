from dataclasses import FrozenInstanceError

import pytest

from macro_studio.external_hid import Frame, FrameError, FrameParser, Opcode, decode, encode
from macro_studio.external_hid.codec import crc16_ccitt_false


def test_crc_matches_ccitt_false_check_value() -> None:
    assert crc16_ccitt_false(b"123456789") == 0x29B1


def test_empty_hello_matches_v1_wire_vector() -> None:
    assert encode(Frame(Opcode.HELLO)) == bytes.fromhex(
        "02 01 02 01 01 01 01 01 01 01 01 01 03 c9 e8 00"
    )


def test_round_trip_preserves_fields_and_zero_bytes_in_payload() -> None:
    frame = Frame(
        Opcode.INPUT_EVENT,
        b"\x00\x04\x00\x07\xff",
        seq=0xBEEF,
        session_id=0x12345678,
    )

    wire = encode(frame)

    assert wire.endswith(b"\x00")
    assert b"\x00" not in wire[:-1]
    assert decode(wire) == frame
    with pytest.raises(FrozenInstanceError):
        frame.seq = 2


def test_empty_and_maximum_payloads_round_trip() -> None:
    frames = [
        Frame(Opcode.HELLO),
        Frame(Opcode.PREPARE_JOB, bytes(range(64)), seq=0xFFFF, session_id=0xFFFFFFFF),
    ]

    assert [decode(encode(frame)) for frame in frames] == frames


def test_decode_rejects_missing_delimiter_corruption_and_extra_bytes() -> None:
    wire = encode(Frame(Opcode.HEARTBEAT, seq=8))
    damaged = bytearray(wire)
    damaged[3] ^= 1

    with pytest.raises(FrameError, match="delimiter"):
        decode(wire[:-1])
    with pytest.raises(FrameError, match="CRC"):
        decode(bytes(damaged))
    with pytest.raises(FrameError, match="delimiter"):
        decode(wire + b"\x01")


def test_incremental_parser_handles_fragments_and_multiple_frames() -> None:
    first = Frame(Opcode.HELLO, seq=1)
    second = Frame(Opcode.ACK, payload=b"ok", seq=2)
    stream = encode(first) + encode(second)
    parser = FrameParser()

    assert parser.feed(stream[:3]) == []
    assert parser.feed(stream[3:len(encode(first))]) == [first]
    assert parser.feed(stream[len(encode(first)):]) == [second]


def test_parser_reports_bad_packet_in_order_then_recovers_at_delimiter() -> None:
    good_before = encode(Frame(Opcode.HELLO, seq=1))
    bad = bytearray(encode(Frame(Opcode.INPUT_EVENT, payload=b"x", seq=2)))
    bad[3] ^= 0x40
    good_after = encode(Frame(Opcode.GET_STATUS, seq=3))
    parser = FrameParser()

    events = parser.feed_events(good_before + bytes(bad) + good_after)

    assert events[0] == Frame(Opcode.HELLO, seq=1)
    assert isinstance(events[1], FrameError)
    assert events[2] == Frame(Opcode.GET_STATUS, seq=3)
    assert parser.feed(good_after) == [Frame(Opcode.GET_STATUS, seq=3)]


def test_parser_bounds_oversized_packet_and_recovers_after_delimiter() -> None:
    frame = Frame(Opcode.HELLO, seq=9)
    parser = FrameParser()

    events = parser.feed_events(b"\x01" * 200 + b"\x00" + encode(frame))

    assert len(events) == 2
    assert isinstance(events[0], FrameError)
    assert events[1] == frame


def test_frame_and_decoder_reject_invalid_protocol_values() -> None:
    with pytest.raises(ValueError, match="payload"):
        Frame(Opcode.HELLO, bytes(65))
    with pytest.raises(ValueError, match="opcode"):
        Frame(0x70)  # type: ignore[arg-type]

    with pytest.raises(FrameError, match="empty frame"):
        decode(b"\x00")
    with pytest.raises(FrameError, match="COBS"):
        decode(b"\x03\x01\x00")
