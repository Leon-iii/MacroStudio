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
    def mouse_position(self) -> tuple[int, int]: ...
    def send_mouse_move(self, position: tuple[int, int]) -> None: ...


@dataclass
class FakeInputBackend:
    clock: Clock
    events: list[dict[str, int | str]] = field(default_factory=list, init=False)
    cursor: tuple[int, int] = field(default=(0, 0), init=False)

    def send_key_down(self, key: str) -> None:
        self.events.append({"time_ns": self.clock.now_ns, "action": "key_down", "key": key})

    def send_key_up(self, key: str) -> None:
        self.events.append({"time_ns": self.clock.now_ns, "action": "key_up", "key": key})

    def send_mouse_down(self, button: str) -> None:
        self.events.append({"time_ns": self.clock.now_ns, "action": "mouse_down", "button": button})

    def send_mouse_up(self, button: str) -> None:
        self.events.append({"time_ns": self.clock.now_ns, "action": "mouse_up", "button": button})

    def mouse_position(self) -> tuple[int, int]:
        return self.cursor

    def send_mouse_move(self, position: tuple[int, int]) -> None:
        self.cursor = position
        self.events.append({"time_ns": self.clock.now_ns, "action": "mouse_move", "x": position[0], "y": position[1]})
