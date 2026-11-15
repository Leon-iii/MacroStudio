from contextlib import contextmanager
from pathlib import Path

import pytest

from macro_studio.backend import VirtualClock
from macro_studio.benchmark import make_program, summarize
from macro_studio.ir import validate_program
from macro_studio.live import ArmStore, run_live


class Desktop:
    def __init__(self, clock):
        self.clock = clock
        self.events = []
        self.failed = []
        self.signals = {}
        self.blurred = set()

    def target(self, hwnd):
        return hwnd

    def alive(self, target):
        return True

    def focused(self, target):
        return self.clock.now_ns not in self.blurred

    @contextmanager
    def hotkeys(self, mapping):
        yield self.failed

    def poll_hotkeys(self):
        return self.signals.pop(self.clock.now_ns, [])

    def unmodified_hotkey_conflict(self, key, hotkeys):
        return False

    def hotkey_conflict(self, key, hotkeys):
        return False

    def send_key_down(self, key):
        self.events.append((self.clock.now_ns, "down", key))

    def send_key_up(self, key):
        self.events.append((self.clock.now_ns, "up", key))


class PollClock(VirtualClock):
    def advance_to(self, deadline_ns):
        super().advance_to(min(self.now_ns + 1_000_000, deadline_ns))


@pytest.mark.parametrize("frequency", [100, 200])
def test_benchmark_counts_inserted_edges(frequency):
    clock = PollClock()
    desktop = Desktop(clock)
    result = run_live(make_program(frequency, 4, "A"), desktop, 1, 100_000_000, metrics=True, clock=clock)
    report = summarize(frequency, result["events"], result["missed_ticks"], result["stop_latency_ns"])
    assert report["injected_pairs"] == 4
    assert report["injected_edges"] == 8
    assert report["failed_edges"] == 0
    assert report["jitter_ns"]["max"] == 0


def test_focus_loss_releases_input_and_resume_requires_explicit_hotkey():
    clock = PollClock()
    desktop = Desktop(clock)
    desktop.blurred = {1_000_000, 2_000_000}
    desktop.signals = {2_000_000: [4], 3_000_000: [4], 4_000_000: [2], 5_000_000: [4], 6_000_000: [1]}
    program = validate_program({
        "ir_version": 1, "project_id": "focus",
        "workflows": {"tap": {"steps": [{"op": "key_tap", "key": "A", "hold_ns": 20_000_000}]}},
        "handlers": [
            {"id": "start", "workflow_id": "tap", "event": {"type": "project_start"}},
            {"id": "react", "workflow_id": "tap", "event": {"type": "hotkey_pressed", "hotkey": "CTRL+F7"}},
        ],
    })
    result = run_live(program, desktop, 1, 10_000_000, clock=clock)
    assert result["termination"] == "stopped"
    assert desktop.events == [
        (0, "down", "scan:1E"), (1_000_000, "up", "scan:1E"),
        (5_000_000, "down", "scan:1E"), (6_000_000, "up", "scan:1E"),
    ]


def test_missing_emergency_hotkey_prevents_input():
    desktop = Desktop(PollClock())
    desktop.failed = [1]
    with pytest.raises(ValueError, match="emergency stop"):
        run_live(make_program(100, 3, "A"), desktop, 1, 100_000_000, clock=desktop.clock)
    assert desktop.events == []


def test_arm_is_local_to_project_and_path(tmp_path):
    path = Path(tmp_path / "demo.json")
    settings = tmp_path / "settings" / "armed.json"
    program = {"project_id": "demo"}
    ArmStore(settings).set(path, program, True)
    assert ArmStore(settings).armed(path, program)
    assert not ArmStore(settings).armed(tmp_path / "copy.json", program)
    assert not ArmStore(settings).armed(path, {"project_id": "changed"})
    ArmStore(settings).set(path, program, False)
    assert not ArmStore(settings).armed(path, program)
