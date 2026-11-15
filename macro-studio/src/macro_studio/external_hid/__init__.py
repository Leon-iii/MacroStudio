"""External HID protocol primitives."""

from .codec import Frame, FrameError, FrameParser, Opcode, decode, encode

__all__ = ["Frame", "FrameError", "FrameParser", "Opcode", "decode", "encode"]
