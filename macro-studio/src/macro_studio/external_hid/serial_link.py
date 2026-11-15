"""Bounded serial I/O for a single explicitly selected port."""

from secrets import randbelow
from struct import pack, unpack
from time import monotonic_ns

from .codec import MAX_ENCODED_SIZE, MAX_PAYLOAD_SIZE, Frame, Opcode, encode
from .transport import RequestTracker, TransportError


def _now_ms() -> int:
    return monotonic_ns() // 1_000_000


class SerialLink:
    """Use from one I/O thread or process. At most one command is in flight."""

    def __init__(self, serial_port, session_id: int, timeout_ms: int = 250) -> None:
        self._port = serial_port
        self._session_id = session_id
        self._tracker = RequestTracker(session_id, timeout_ms)
        self._closed = False

    def exchange(self, opcode: Opcode, payload: bytes = b"") -> Frame:
        packet = self._tracker.request(opcode, payload, _now_ms())
        try:
            if self._port.write(packet) != len(packet):
                raise TransportError("incomplete HID serial write")
            while True:
                chunk = self._port.read(min(self._port.in_waiting or 1, MAX_ENCODED_SIZE + 1))
                response = self._tracker.receive(chunk, _now_ms())
                if response is not None:
                    if opcode in (Opcode.RELEASE_ALL, Opcode.DISARM_SESSION):
                        self._close_port()
                    return response
        except (OSError, TransportError):
            self._abort()
            raise

    def inspect(self) -> dict:
        hello = self.exchange(Opcode.HELLO, pack("<BBI", 1, 0, self._session_id)).payload
        if len(hello) < 7 or hello[:2] != b"\x01\x00" or hello[5] != 1:
            raise TransportError("invalid HID HELLO_ACK")
        caps = self.exchange(Opcode.GET_CAPS).payload
        if len(caps) != 5:
            raise TransportError("invalid HID CAPS")
        flags, max_payload, watchdog_ms, onboard_jobs = unpack("<BBHB", caps)
        if not 1 <= max_payload <= MAX_PAYLOAD_SIZE or watchdog_ms == 0:
            raise TransportError("invalid HID capability limits")
        return {
            "board": hello[6:].decode("ascii"),
            "firmware": ".".join(map(str, hello[2:5])),
            "protocol": "1.0",
            "keyboard": bool(flags & 1),
            "mouse_buttons": bool(flags & 2),
            "mouse_relative": bool(flags & 4),
            "timed_batch": bool(flags & 8),
            "max_payload": max_payload,
            "watchdog_ms": watchdog_ms,
            "max_onboard_jobs": onboard_jobs,
        }

    def close(self) -> None:
        if not self._closed:
            try:
                self.exchange(Opcode.RELEASE_ALL)
            finally:
                self._close_port()

    def job_done(self) -> Frame | None:
        try:
            if size := self._port.in_waiting:
                self._tracker.receive(self._port.read(min(size, MAX_ENCODED_SIZE + 1)), _now_ms())
            return self._tracker.take_job_done()
        except (OSError, TransportError):
            self._abort()
            raise

    def _abort(self) -> None:
        # A partial write can leave the device in the middle of a frame.
        release = Frame(Opcode.RELEASE_ALL, session_id=self._session_id)
        try:
            self._port.write(b"\0" + encode(release))
        except OSError:
            pass
        try:
            self._close_port()
        except OSError:
            pass

    def _close_port(self) -> None:
        if not self._closed:
            self._closed = True
            self._tracker.close()
            self._port.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc is None:
            self.close()
        elif not self._closed:
            self._abort()


def open_port(port: str, timeout_ms: int = 250) -> SerialLink:
    try:
        from serial import Serial
    except ImportError as exc:
        raise TransportError("install macro-studio[hid] for serial port support") from exc

    session = randbelow(0xFFFFFFFF) + 1
    serial = Serial(port, 115200, timeout=0.02, write_timeout=min(0.1, timeout_ms / 1000))
    return SerialLink(serial, session, timeout_ms)


def probe_port(port: str) -> dict:
    with open_port(port) as link:
        return {"port": port, **link.inspect()}


def list_ports() -> list[dict]:
    try:
        from serial.tools.list_ports import comports
    except ImportError as exc:
        raise TransportError("install macro-studio[hid] for serial port support") from exc

    return [
        {"port": port.device, "description": port.description, "vid": port.vid, "pid": port.pid,
         "serial_number": port.serial_number}
        for port in sorted(comports())
    ]
