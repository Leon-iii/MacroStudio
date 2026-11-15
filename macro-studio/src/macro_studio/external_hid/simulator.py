from collections import OrderedDict, deque
from enum import IntEnum
from struct import Struct, pack

from .codec import MAX_PAYLOAD_SIZE, PROTOCOL_MAJOR, Frame, FrameError, FrameParser, Opcode, encode


class McuState(IntEnum):
    DISARMED = 0
    IDLE = 1
    ARMED = 2


class ErrorCode(IntEnum):
    BAD_PAYLOAD = 1
    UNSUPPORTED = 2
    INVALID_STATE = 3
    INVALID_SESSION = 4
    SEQUENCE_CONFLICT = 5
    INVALID_FRAME = 6
    WATCHDOG_EXPIRED = 7
    INCOMPATIBLE_VERSION = 8


class InputKind(IntEnum):
    KEY = 1
    MOUSE_BUTTON = 2
    RELATIVE_MOVE = 3


_HELLO = Struct("<BBI")
_KEY = Struct("<BBHH")
_BUTTON = Struct("<BBB")
_MOVE = Struct("<Bhh")
_EVENT_FORMAT = {
    InputKind.KEY: _KEY,
    InputKind.MOUSE_BUTTON: _BUTTON,
    InputKind.RELATIVE_MOVE: _MOVE,
}
_PROTOCOL = (PROTOCOL_MAJOR, 0)
_FIRMWARE = (0, 1, 0)
_WATCHDOG_MS = 1500
_REQUEST_HISTORY_SIZE = 32
_SESSION_HISTORY_SIZE = 32
_CLOSE_OPCODES = (Opcode.RELEASE_ALL, Opcode.DISARM_SESSION)
_JOB_OPCODES = (Opcode.PREPARE_JOB, Opcode.START_JOB, Opcode.CANCEL_JOB)
_KEYBOARD_USAGES = frozenset(range(0x04, 0x74)) | frozenset(range(0xE0, 0xE8))


class McuSimulator:
    """In-memory streaming device with a clock advanced explicitly by callers."""

    def __init__(self) -> None:
        self._parser = FrameParser()
        self._time_ms = 0
        self._deadline_ms: int | None = None
        self._state = McuState.DISARMED
        self._session_id = 0
        # The host supplies fresh tokens even after the replay window expires.
        self._recent_sessions: deque[int] = deque(maxlen=_SESSION_HISTORY_SIZE)
        self._recent: OrderedDict[int, tuple[Frame, Frame]] = OrderedDict()
        self._last_error: ErrorCode | None = None
        self._keys: set[tuple[int, int]] = set()
        self._buttons: set[int] = set()
        self._x = 0
        self._y = 0

    @property
    def pressed_keys(self) -> frozenset[tuple[int, int]]:
        return frozenset(self._keys)

    @property
    def pressed_buttons(self) -> frozenset[int]:
        return frozenset(self._buttons)

    @property
    def pointer_offset(self) -> tuple[int, int]:
        return self._x, self._y

    def receive(self, data: bytes) -> bytes:
        responses = bytearray()
        for event in self._parser.feed_events(data):
            response = (
                self._fault(ErrorCode.INVALID_FRAME)
                if isinstance(event, FrameError)
                else self._handle(event)
            )
            if response is not None:
                responses.extend(encode(response))
        return bytes(responses)

    def advance(self, milliseconds: int) -> bytes:
        if milliseconds < 0:
            raise ValueError("simulator time cannot move backwards")
        self._time_ms += milliseconds
        if self._deadline_ms is not None and self._time_ms >= self._deadline_ms:
            return encode(self._fault(ErrorCode.WATCHDOG_EXPIRED))
        return b""

    def disconnect(self) -> None:
        self._parser = FrameParser()
        self._disarm()

    def _disarm(self) -> None:
        self._session_id = 0
        self._deadline_ms = None
        self._state = McuState.DISARMED
        self._keys.clear()
        self._buttons.clear()
        self._recent.clear()

    def _fault(self, reason: ErrorCode) -> Frame:
        session_id = self._session_id
        self._fail(reason)
        return Frame(opcode=Opcode.FAULT, session_id=session_id, payload=pack("<H", reason))

    def _fail(self, reason: ErrorCode) -> None:
        self._last_error = reason
        self._disarm()

    def _handle(self, request: Frame) -> Frame | None:
        if request.opcode >= Opcode.ACK:
            return None
        starting_session = request.opcode == Opcode.HELLO and self._state == McuState.DISARMED
        ending_session = request.opcode in _CLOSE_OPCODES
        expected_session = self._session_id
        if ending_session:
            if not self._recent_sessions:
                return _nack(request, ErrorCode.INVALID_SESSION)
            expected_session = self._recent_sessions[-1]
        if not starting_session and request.session_id != expected_session:
            return _nack(request, ErrorCode.INVALID_SESSION)
        if (request.major, request.minor) != _PROTOCOL:
            self._fail(ErrorCode.INCOMPATIBLE_VERSION)
            return _nack(request, ErrorCode.INCOMPATIBLE_VERSION)
        if starting_session:
            return self._hello(request)
        if ending_session:
            if request.payload:
                return self._bad_payload(request)
            self._disarm()
            return _ack(request)

        if recent := self._recent.get(request.seq):
            previous_request, reply = recent
            if request == previous_request:
                return reply
            return _nack(request, ErrorCode.SEQUENCE_CONFLICT)

        reply = self._dispatch(request)
        if self._session_id and reply.opcode != Opcode.NACK:
            self._remember(request, reply)
        return reply

    def _hello(self, request: Frame) -> Frame:
        if len(request.payload) != _HELLO.size:
            return self._bad_payload(request)
        major, minor, session_id = _HELLO.unpack(request.payload)
        if (major, minor) != _PROTOCOL:
            self._last_error = ErrorCode.INCOMPATIBLE_VERSION
            return _nack(request, ErrorCode.INCOMPATIBLE_VERSION)
        if not session_id or session_id != request.session_id or session_id in self._recent_sessions:
            return _nack(request, ErrorCode.INVALID_SESSION)

        self._recent_sessions.append(session_id)
        self._session_id = session_id
        self._state = McuState.IDLE
        self._last_error = None
        payload = bytes((*_PROTOCOL, *_FIRMWARE, self._state)) + b"simulator"
        reply = _reply(request, Opcode.HELLO_ACK, payload)
        self._remember(request, reply)
        return reply

    def _dispatch(self, request: Frame) -> Frame:
        opcode = request.opcode
        if opcode == Opcode.HELLO:
            return _nack(request, ErrorCode.INVALID_STATE)
        if opcode in _JOB_OPCODES:
            return _nack(request, ErrorCode.UNSUPPORTED)
        if opcode == Opcode.INPUT_EVENT:
            if self._state != McuState.ARMED:
                return _nack(request, ErrorCode.INVALID_STATE)
            return self._apply_event(request)
        if request.payload:
            return self._bad_payload(request)
        if opcode == Opcode.GET_CAPS:
            payload = pack("<BBHB", 0b111, MAX_PAYLOAD_SIZE, _WATCHDOG_MS, 0)
            return _reply(request, Opcode.CAPS, payload)
        if opcode == Opcode.GET_STATUS:
            return _reply(request, Opcode.STATUS, pack("<BHH", self._state, 0, self._last_error or 0))
        if opcode == Opcode.ARM_SESSION and self._state == McuState.IDLE:
            self._state = McuState.ARMED
        elif opcode != Opcode.HEARTBEAT or self._state != McuState.ARMED:
            return _nack(request, ErrorCode.INVALID_STATE)
        self._deadline_ms = self._time_ms + _WATCHDOG_MS
        return _ack(request)

    def _apply_event(self, request: Frame) -> Frame:
        payload = request.payload
        event_format = _EVENT_FORMAT.get(payload[0]) if payload else None
        if event_format is None or len(payload) != event_format.size:
            return self._bad_payload(request)

        kind, *fields = event_format.unpack(payload)
        if kind == InputKind.RELATIVE_MOVE:
            dx, dy = fields
            self._x += dx
            self._y += dy
            return _ack(request)

        action = fields[0]
        if action not in (1, 2):
            return self._bad_payload(request)
        if kind == InputKind.KEY:
            page, usage = fields[1:]
            if page != 0x07 or usage not in _KEYBOARD_USAGES:
                return _nack(request, ErrorCode.UNSUPPORTED)
            if action == 1:
                self._keys.add((page, usage))
            else:
                self._keys.discard((page, usage))
        else:
            button = fields[1]
            if button not in (1, 2, 3):
                return _nack(request, ErrorCode.UNSUPPORTED)
            if action == 1:
                self._buttons.add(button)
            else:
                self._buttons.discard(button)
        return _ack(request)

    def _bad_payload(self, request: Frame) -> Frame:
        self._fail(ErrorCode.BAD_PAYLOAD)
        return _nack(request, ErrorCode.BAD_PAYLOAD)

    def _remember(self, request: Frame, reply: Frame) -> None:
        self._recent[request.seq] = (request, reply)
        if len(self._recent) > _REQUEST_HISTORY_SIZE:
            self._recent.popitem(last=False)


def _reply(request: Frame, opcode: Opcode, payload: bytes) -> Frame:
    return Frame(opcode=opcode, seq=request.seq, session_id=request.session_id, payload=payload)


def _ack(request: Frame) -> Frame:
    return _reply(request, Opcode.ACK, bytes((request.opcode,)))


def _nack(request: Frame, reason: ErrorCode) -> Frame:
    return _reply(request, Opcode.NACK, pack("<BH", request.opcode, reason))
