"""Pure wire codec and bounded incremental parser for External HID v1."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


PROTOCOL_MAJOR = 1
MAX_PAYLOAD_SIZE = 64
HEADER_SIZE = 12
CRC_SIZE = 2
MAX_DECODED_SIZE = HEADER_SIZE + MAX_PAYLOAD_SIZE + CRC_SIZE
MAX_ENCODED_SIZE = MAX_DECODED_SIZE + 1  # one COBS code byte for these frame sizes
FRAME_DELIMITER = 0


class FrameError(ValueError):
    """A wire packet is malformed or violates the v1 frame contract."""


class Opcode(IntEnum):
    HELLO = 0x01
    GET_CAPS = 0x02
    ARM_SESSION = 0x03
    DISARM_SESSION = 0x04
    HEARTBEAT = 0x05
    INPUT_EVENT = 0x06
    RELEASE_ALL = 0x07
    PREPARE_JOB = 0x08
    START_JOB = 0x09
    CANCEL_JOB = 0x0A
    GET_STATUS = 0x0B
    ACK = 0x80
    NACK = 0x81
    HELLO_ACK = 0x82
    CAPS = 0x83
    STATUS = 0x84
    JOB_DONE = 0x85
    FAULT = 0x86


@dataclass(frozen=True, slots=True)
class Frame:
    """Validated logical frame; payload is the message-specific opaque bytes."""

    opcode: Opcode
    payload: bytes = b""
    major: int = PROTOCOL_MAJOR
    minor: int = 0
    flags: int = 0
    seq: int = 0
    session_id: int = 0

    def __post_init__(self) -> None:
        try:
            opcode = Opcode(self.opcode)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unsupported opcode: {self.opcode!r}") from exc
        object.__setattr__(self, "opcode", opcode)
        if not isinstance(self.payload, bytes):
            raise TypeError("payload must be bytes")
        for name, value in (
            ("major", self.major),
            ("minor", self.minor),
            ("flags", self.flags),
            ("seq", self.seq),
            ("session_id", self.session_id),
        ):
            if type(value) is not int:
                raise TypeError(f"{name} must be an integer")
        if self.major != PROTOCOL_MAJOR:
            raise ValueError(f"unsupported protocol major: {self.major}")
        if self.minor not in range(256):
            raise ValueError("minor must fit in u8")
        if self.flags != 0:
            raise ValueError("flags must be zero in protocol v1")
        if self.seq not in range(1 << 16):
            raise ValueError("seq must fit in u16")
        if self.session_id not in range(1 << 32):
            raise ValueError("session_id must fit in u32")
        if len(self.payload) > MAX_PAYLOAD_SIZE:
            raise ValueError(f"payload exceeds {MAX_PAYLOAD_SIZE} bytes")


def crc16_ccitt_false(data: bytes) -> int:
    """Compute CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def _cobs_encode(data: bytes) -> bytes:
    encoded = bytearray(b"\x00")
    code_index = 0
    code = 1
    for byte in data:
        if byte == 0:
            encoded[code_index] = code
            code_index = len(encoded)
            encoded.append(0)
            code = 1
        else:
            encoded.append(byte)
            code += 1
            if code == 0xFF:
                encoded[code_index] = code
                code_index = len(encoded)
                encoded.append(0)
                code = 1
    encoded[code_index] = code
    return bytes(encoded)


def _cobs_decode(data: bytes) -> bytes:
    if not data:
        raise FrameError("empty COBS packet")
    decoded = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        if code == 0:
            raise FrameError("zero byte inside COBS packet")
        index += 1
        end = index + code - 1
        if end > len(data):
            raise FrameError("COBS code exceeds packet length")
        decoded.extend(data[index:end])
        index = end
        if code != 0xFF and index < len(data):
            decoded.append(0)
    return bytes(decoded)


def encode(frame: Frame) -> bytes:
    """Encode a Frame as COBS(DecodedFrame) followed by a zero delimiter."""
    body = bytearray(
        (
            frame.major,
            frame.minor,
            int(frame.opcode),
            frame.flags,
        )
    )
    body.extend(frame.seq.to_bytes(2, "little"))
    body.extend(frame.session_id.to_bytes(4, "little"))
    body.extend(len(frame.payload).to_bytes(2, "little"))
    body.extend(frame.payload)
    body.extend(crc16_ccitt_false(body).to_bytes(2, "little"))
    return _cobs_encode(bytes(body)) + bytes((FRAME_DELIMITER,))


def decode(data: bytes) -> Frame:
    """Decode exactly one zero-terminated wire frame."""
    if not isinstance(data, bytes):
        raise TypeError("data must be bytes")
    if not data.endswith(b"\x00"):
        raise FrameError("frame is missing its delimiter")
    encoded = data[:-1]
    if not encoded:
        raise FrameError("empty frame")
    if len(encoded) > MAX_ENCODED_SIZE:
        raise FrameError("encoded frame exceeds maximum size")
    decoded = _cobs_decode(encoded)
    if len(decoded) < HEADER_SIZE + CRC_SIZE:
        raise FrameError("decoded frame is shorter than the fixed header and CRC")
    if len(decoded) > MAX_DECODED_SIZE:
        raise FrameError("decoded frame exceeds maximum size")
    content, received_crc = decoded[:-CRC_SIZE], int.from_bytes(decoded[-CRC_SIZE:], "little")
    if crc16_ccitt_false(content) != received_crc:
        raise FrameError("CRC mismatch")

    major, minor, raw_opcode, flags = content[:4]
    seq = int.from_bytes(content[4:6], "little")
    session_id = int.from_bytes(content[6:10], "little")
    payload_length = int.from_bytes(content[10:12], "little")
    payload = content[HEADER_SIZE:]
    if payload_length != len(payload):
        raise FrameError("payload length does not match frame length")
    if payload_length > MAX_PAYLOAD_SIZE:
        raise FrameError("payload exceeds protocol maximum")
    if major != PROTOCOL_MAJOR:
        raise FrameError(f"unsupported protocol major: {major}")
    if flags != 0:
        raise FrameError(f"reserved flags must be zero, got 0x{flags:02x}")
    try:
        opcode = Opcode(raw_opcode)
    except ValueError as exc:
        raise FrameError(f"unsupported opcode: 0x{raw_opcode:02x}") from exc
    return Frame(opcode, payload, major, minor, flags, seq, session_id)


class FrameParser:
    """Incremental delimiter parser with a fixed-size packet buffer."""

    __slots__ = ("_buffer", "_overflow", "_max_encoded_size")

    def __init__(self, max_encoded_size: int = MAX_ENCODED_SIZE) -> None:
        if max_encoded_size < 1 or max_encoded_size > MAX_ENCODED_SIZE:
            raise ValueError(f"max_encoded_size must be between 1 and {MAX_ENCODED_SIZE}")
        self._buffer = bytearray()
        self._overflow = False
        self._max_encoded_size = max_encoded_size

    def feed_events(self, data: bytes) -> list[Frame | FrameError]:
        """Return valid frames and packet errors in input order."""
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        events: list[Frame | FrameError] = []
        for byte in data:
            if byte == FRAME_DELIMITER:
                if self._overflow:
                    events.append(FrameError("encoded frame exceeds parser buffer"))
                elif self._buffer:
                    try:
                        events.append(decode(bytes(self._buffer) + b"\x00"))
                    except FrameError as exc:
                        events.append(exc)
                self._buffer.clear()
                self._overflow = False
            elif not self._overflow:
                if len(self._buffer) == self._max_encoded_size:
                    self._buffer.clear()
                    self._overflow = True
                else:
                    self._buffer.append(byte)
        return events

    def feed(self, data: bytes) -> list[Frame]:
        """Return valid frames, silently dropping malformed packets."""
        return [event for event in self.feed_events(data) if isinstance(event, Frame)]
