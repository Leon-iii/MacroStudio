from dataclasses import dataclass, field
from typing import Protocol


class Clock(Protocol):
    @property
    def now_ns(self) -> int: ...

    def advance_to(self, deadline_ns: int) -> None: ...


@dataclass
class VirtualClock:
    now_ns: int = 0

    def advance_to(self, deadline_ns: int) -> None:
        if deadline_ns < self.now_ns:
            raise ValueError("virtual time cannot move backwards")
        self.now_ns = deadline_ns


class InputBackend(Protocol):
    def send_key_down(self, key: str) -> None: ...

    def send_key_up(self, key: str) -> None: ...

    def send_mouse_down(self, button: str) -> None: ...

    def send_mouse_up(self, button: str) -> None: ...


@dataclass
class FakeInputBackend:
    clock: Clock
    events: list[dict[str, int | str]] = field(default_factory=list, init=False)

    def send_key_down(self, key: str) -> None:
        self.events.append({"time_ns": self.clock.now_ns, "action": "key_down", "key": key})

    def send_key_up(self, key: str) -> None:
        self.events.append({"time_ns": self.clock.now_ns, "action": "key_up", "key": key})

    def send_mouse_down(self, button: str) -> None:
        self.events.append({"time_ns": self.clock.now_ns, "action": "mouse_down", "button": button})

    def send_mouse_up(self, button: str) -> None:
        self.events.append({"time_ns": self.clock.now_ns, "action": "mouse_up", "button": button})
