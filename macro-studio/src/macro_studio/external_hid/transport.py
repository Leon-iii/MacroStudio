"""Serial request tracking for a single HID session, independent of port I/O."""

from dataclasses import dataclass

from .codec import Frame, FrameError, FrameParser, Opcode, encode


class TransportError(RuntimeError):
    """The link cannot safely continue the current session."""


_RESPONSES = {
    Opcode.HELLO: Opcode.HELLO_ACK,
    Opcode.GET_CAPS: Opcode.CAPS,
    Opcode.ARM_SESSION: Opcode.ACK,
    Opcode.DISARM_SESSION: Opcode.ACK,
    Opcode.HEARTBEAT: Opcode.ACK,
    Opcode.INPUT_EVENT: Opcode.ACK,
    Opcode.RELEASE_ALL: Opcode.ACK,
    Opcode.GET_STATUS: Opcode.STATUS,
    Opcode.PREPARE_JOB: Opcode.ACK,
    Opcode.QUEUE_BATCH: Opcode.ACK,
    Opcode.START_JOB: Opcode.ACK,
    Opcode.CANCEL_JOB: Opcode.ACK,
}
_END_SESSION = (Opcode.RELEASE_ALL, Opcode.DISARM_SESSION)


@dataclass(frozen=True, slots=True)
class _Pending:
    opcode: Opcode
    seq: int
    deadline_ms: int
    acknowledgement: bytes


class RequestTracker:
    """Track one outstanding request without a queue or automatic retry."""

    def __init__(self, session_id: int, timeout_ms: int) -> None:
        if type(session_id) is not int or not 0 < session_id <= 0xFFFFFFFF:
            raise ValueError("session_id must be a nonzero u32")
        if type(timeout_ms) is not int or timeout_ms <= 0:
            raise ValueError("timeout_ms must be a positive integer")
        self._session_id = session_id
        self._timeout_ms = timeout_ms
        self._next_seq = 1
        self._pending: _Pending | None = None
        self._parser = FrameParser()
        self._closed = False
        self._job_done: Frame | None = None

    def request(self, opcode: Opcode, payload: bytes, now_ms: int) -> bytes:
        self.check_timeout(now_ms)
        if self._pending is not None:
            raise TransportError("a HID request is already in flight")
        if opcode not in _RESPONSES:
            raise ValueError("unsupported HID request")
        if self._next_seq == 0xFFFF and opcode not in _END_SESSION:
            raise TransportError("HID sequence exhausted, release inputs and open a new session")
        frame = Frame(opcode, payload, seq=self._next_seq, session_id=self._session_id)
        packet = encode(frame)
        self._next_seq += 1
        suffix = payload[:2] if opcode in (Opcode.PREPARE_JOB, Opcode.QUEUE_BATCH) else b""
        self._pending = _Pending(frame.opcode, frame.seq, now_ms + self._timeout_ms, bytes((opcode,)) + suffix)
        return packet

    def receive(self, chunk: bytes, now_ms: int) -> Frame | None:
        self.check_timeout(now_ms)
        response = None
        for frame in self._parser.feed_events(chunk):
            if isinstance(frame, FrameError):
                self._fail("invalid HID response frame")
            if frame.session_id != self._session_id:
                continue
            if frame.opcode is Opcode.FAULT:
                self._fail("HID device reported a fault")
            if frame.opcode is Opcode.JOB_DONE:
                if frame.seq or len(frame.payload) != 9:
                    self._fail("invalid HID job completion")
                self._job_done = frame
                continue
            pending = self._pending
            if pending is None or frame.seq != pending.seq:
                continue
            if frame.opcode is Opcode.NACK:
                if len(frame.payload) != 3 or frame.payload[0] != pending.opcode:
                    self._fail("invalid HID negative acknowledgement")
                error_code = int.from_bytes(frame.payload[1:], "little")
                self._fail(f"HID device rejected {pending.opcode.name} ({error_code})")
            if frame.opcode is not _RESPONSES[pending.opcode]:
                self._fail("unexpected HID response opcode")
            if frame.opcode is Opcode.ACK and frame.payload != pending.acknowledgement:
                self._fail("invalid HID acknowledgement")
            self._pending = None
            response = frame
            if pending.opcode in _END_SESSION:
                self.close()
        return response

    def take_job_done(self) -> Frame | None:
        frame, self._job_done = self._job_done, None
        return frame

    def check_timeout(self, now_ms: int) -> None:
        if self._closed:
            raise TransportError("HID session is closed")
        if self._pending is not None and now_ms >= self._pending.deadline_ms:
            self._fail("HID response timed out")

    def close(self) -> None:
        self._closed = True
        self._pending = None

    def _fail(self, message: str) -> None:
        self.close()
        raise TransportError(message)
