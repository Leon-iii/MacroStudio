from struct import pack, unpack

from ..output import BackendCapabilities
from .codec import Opcode
from .transport import TransportError

_USAGES = {
    0x01: 0x29, 0x0C: 0x2D, 0x0D: 0x2E, 0x0E: 0x2A, 0x0F: 0x2B,
    0x1A: 0x2F, 0x1B: 0x30, 0x1C: 0x28, 0x1D: 0xE0,
    0x27: 0x33, 0x28: 0x34, 0x29: 0x35, 0x2A: 0xE1, 0x2B: 0x31,
    0x33: 0x36, 0x34: 0x37, 0x35: 0x38, 0x36: 0xE5, 0x37: 0x55,
    0x38: 0xE2, 0x39: 0x2C, 0x3A: 0x39, 0x45: 0x53, 0x46: 0x47,
    0x47: 0x5F, 0x48: 0x60, 0x49: 0x61, 0x4A: 0x56, 0x4B: 0x5C,
    0x4C: 0x5D, 0x4D: 0x5E, 0x4E: 0x57, 0x4F: 0x59, 0x50: 0x5A,
    0x51: 0x5B, 0x52: 0x62, 0x53: 0x63, 0x56: 0x64, 0x57: 0x44, 0x58: 0x45,
    0xE01C: 0x58, 0xE01D: 0xE4, 0xE035: 0x54, 0xE037: 0x46, 0xE038: 0xE6,
    0xE047: 0x4A, 0xE048: 0x52, 0xE049: 0x4B, 0xE04B: 0x50,
    0xE04D: 0x4F, 0xE04F: 0x4D, 0xE050: 0x51, 0xE051: 0x4E,
    0xE052: 0x49, 0xE053: 0x4C, 0xE05B: 0xE3, 0xE05C: 0xE7, 0xE05D: 0x65,
}
for _start, _row in ((0x10, "QWERTYUIOP"), (0x1E, "ASDFGHJKL"), (0x2C, "ZXCVBNM")):
    _USAGES.update((_start + i, ord(letter) - ord("A") + 4) for i, letter in enumerate(_row))
_USAGES.update((2 + i, 0x1E + i) for i in range(10))
_USAGES.update((0x3B + i, 0x3A + i) for i in range(10))

_BUTTONS = {"left": 1, "right": 2, "middle": 3}
_BATCH_ACTIONS = {"key_down": 1, "key_up": 2, "mouse_down": 3, "mouse_up": 4, "mouse_move": 5}
_HEARTBEAT_NS = 250_000_000


def key_usage(key: str) -> int:
    try:
        return _USAGES[int(key.removeprefix("scan:"), 16)]
    except (KeyError, ValueError) as error:
        raise ValueError(f"external HID does not support key {key}") from error


class HidBackend:
    def __init__(self, link, desktop, clock, device: dict) -> None:
        self.link = link
        self.desktop = desktop
        self.clock = clock
        relative = device["mouse_relative"] and device["max_payload"] >= 5
        self.capabilities = BackendCapabilities(
            keyboard=device["keyboard"] and device["max_payload"] >= 6,
            mouse_buttons=device["mouse_buttons"] and device["max_payload"] >= 3,
            mouse_relative=relative,
            mouse_absolute=relative,
            onboard_tap_train=device["max_onboard_jobs"] >= 1 and device["max_payload"] >= 18,
            timed_batch=device.get("timed_batch", False) and device["max_payload"] >= 59,
        )
        self._interval_ns = min(_HEARTBEAT_NS, device["watchdog_ms"] * 1_000_000 // 3)
        self._next_heartbeat = 0
        self._held_keys: set[str] = set()
        self._held_buttons: set[str] = set()
        self._job_id = 0
        self._job_key: str | None = None
        self._batch: list[dict] = []
        self._batch_started_ns: int | None = None

    def arm(self) -> None:
        self.link.exchange(Opcode.ARM_SESSION)
        self._next_heartbeat = self.clock.now_ns + self._interval_ns

    def heartbeat(self) -> None:
        if self.clock.now_ns >= self._next_heartbeat:
            self.link.exchange(Opcode.HEARTBEAT)
            self._next_heartbeat = self.clock.now_ns + self._interval_ns

    def send_key_down(self, key: str) -> None:
        # The device may press the key even if its acknowledgement is lost.
        self._held_keys.add(key)
        self._key(key, 1)

    def send_key_up(self, key: str) -> None:
        self._key(key, 2)
        self._held_keys.discard(key)

    def _key(self, key: str, action: int) -> None:
        self.link.exchange(Opcode.INPUT_EVENT, pack("<BBHH", 1, action, 7, key_usage(key)))

    def send_mouse_down(self, button: str) -> None:
        self._held_buttons.add(button)
        self.link.exchange(Opcode.INPUT_EVENT, pack("<BBB", 2, 1, _BUTTONS[button]))

    def send_mouse_up(self, button: str) -> None:
        self.link.exchange(Opcode.INPUT_EVENT, pack("<BBB", 2, 2, _BUTTONS[button]))
        self._held_buttons.discard(button)

    def release_stuck_inputs(self) -> None:
        if self._batch_started_ns is not None:
            elapsed_us = (self.clock.now_ns - self._batch_started_ns) // 1000
            for edge in self._batch:
                if edge["offset_us"] > elapsed_us:
                    break
                if edge["action"] == "key_down":
                    self._held_keys.add(edge["key"])
                elif edge["action"] == "mouse_down":
                    self._held_buttons.add(edge["button"])
        errors = []
        for held, mouse in ((self._held_keys, False), (self._held_buttons, True)):
            if not held:
                continue
            release = self.desktop.send_mouse_up if mouse else self.desktop.send_key_up
            for item in sorted(held):
                try:
                    release(item)
                except OSError as error:
                    errors.append(f"{item}: {error}")
                else:
                    held.remove(item)
        if errors:
            raise OSError("Windows HID cleanup failed: " + ", ".join(errors))

    def start_tap_train(self, key: str, period_us: int, hold_us: int, count: int) -> None:
        usage = key_usage(key)
        if usage >= 0xE0:
            raise TransportError("onboard tap train requires a non-modifier key")
        self._job_id = self._job_id % 0xFFFF + 1
        self.link.exchange(Opcode.PREPARE_JOB, pack("<HHHIII", self._job_id, 7, usage,
                                                   period_us, hold_us, count))
        self._job_key = key
        self._held_keys.add(key)
        self.link.exchange(Opcode.START_JOB, pack("<H", self._job_id))

    def queue_batch(self, edges: list[dict]) -> None:
        payload = bytearray()
        for edge in edges:
            action = edge["action"]
            if "key" in edge:
                first, second = key_usage(edge["key"]), 0
                if first >= 0xE0:
                    raise TransportError("timed batch requires non-modifier keys")
            elif "button" in edge:
                first, second = _BUTTONS[edge["button"]], 0
            else:
                first, second = edge["dx"], edge["dy"]
            payload.extend(pack("<IBbb", edge["offset_us"], _BATCH_ACTIONS[action], first, second))
        self._job_id = self._job_id % 0xFFFF + 1
        self.link.exchange(Opcode.QUEUE_BATCH, pack("<HB", self._job_id, len(edges)) + bytes(payload))
        self._batch = edges

    def start_batch(self) -> None:
        self._batch_started_ns = self.clock.now_ns
        self.link.exchange(Opcode.START_JOB, pack("<H", self._job_id))

    def job_count(self) -> int | None:
        frame = self.link.job_done()
        if frame is None:
            return None
        job_id, result, count, error = unpack("<HBIH", frame.payload)
        if job_id != self._job_id:
            return None
        if result or error:
            raise TransportError(f"onboard job failed ({result}, {error})")
        self._finish_job()
        return count

    def cancel_job(self) -> None:
        if self.job_active:
            self.link.exchange(Opcode.CANCEL_JOB, pack("<H", self._job_id))
            self._finish_job()

    @property
    def job_active(self) -> bool:
        return self._job_key is not None or bool(self._batch)

    @property
    def batch_uses_pointer(self) -> bool:
        return any(edge["action"].startswith("mouse") for edge in self._batch)

    def _finish_job(self) -> None:
        if self._job_key is not None:
            self._held_keys.discard(self._job_key)
        self._job_key = None
        self._batch = []
        self._batch_started_ns = None

    def mouse_position(self) -> tuple[int, int]:
        return self.desktop.mouse_position()

    def send_mouse_move(self, position: tuple[int, int]) -> None:
        x, y = self.mouse_position()
        self.send_delta((position[0] - x, position[1] - y))

    def send_delta(self, delta: tuple[int, int]) -> None:
        x, y = (max(-127, min(127, axis)) for axis in delta)
        if x or y:
            self.link.exchange(Opcode.INPUT_EVENT, pack("<Bhh", 3, x, y))
