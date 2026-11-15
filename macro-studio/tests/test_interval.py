import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError
from macro_studio.runtime import Runtime

MS = 1_000_000


def workspace(period=10, hold=2, policy="ignore_if_running"):
    return {"project_id": "timer", "workspace": {"blocks": {"blocks": [{
        "type": "ms_interval", "id": "interval", "fields": {"MS": period, "POLICY": policy},
        "inputs": {"BODY": {"block": {
            "type": "ms_key_tap", "id": "tap", "fields": {"KEY": "A", "MS": hold},
        }}},
    }]}}}


def run(clock, *, hold=2, policy="ignore_if_running", poll=None, duration=50):
    program, detached = compile_project(workspace(hold=hold, policy=policy))
    assert not detached
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)
    runtime.run(duration * MS, [], poll=poll)
    assert not runtime.errors
    return [(row["time_ns"] // MS, row["action"]) for row in backend.events]


def test_interval_only_session_runs_until_cutoff():
    assert run(VirtualClock(), duration=30) == [
        (10, "key_down"), (12, "key_up"), (20, "key_down"), (22, "key_up"),
    ]


def test_overdue_intervals_are_coalesced_without_drift():
    class LateClock(VirtualClock):
        def advance_to(self, deadline_ns):
            super().advance_to(35 * MS if deadline_ns == 10 * MS else deadline_ns)

    assert run(LateClock(), duration=50) == [
        (35, "key_down"), (37, "key_up"), (40, "key_down"), (42, "key_up"),
    ]


def test_resume_skips_ticks_without_changing_session_schedule():
    class PollClock(VirtualClock):
        def advance_to(self, deadline_ns):
            super().advance_to(min(deadline_ns, self.now_ns + MS))

    clock = PollClock()
    commands = {12: "pause", 34: "resume", 45: "stop"}
    assert run(clock, hold=18, poll=lambda: (commands.pop(clock.now_ns // MS, None), [])) == [
        (10, "key_down"), (12, "key_up"), (40, "key_down"), (45, "key_up"),
    ]


def test_restart_tick_releases_previous_input_before_pressing_again():
    assert run(VirtualClock(), hold=25, policy="restart", duration=25) == [
        (10, "key_down"), (20, "key_up"), (20, "key_down"), (25, "key_up"),
    ]


def test_invalid_interval_identifies_block():
    with pytest.raises(ValidationError, match=r"\(interval\)"):
        compile_project(workspace(period=0))
