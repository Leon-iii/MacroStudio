import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.ir import validate_program
from macro_studio.runtime import Runtime


def run(policy, events, *, owner=None, cutoff=21):
    handlers = [{"id": "f7", "event": {"type": "hotkey_pressed", "hotkey": "F7"},
                 "policy": policy, "priority": 1, "workflow_id": "tap"}]
    workflows = {"tap": {"steps": [
        {"op": "key_tap", "key": "A", "hold_ns": 10, "block_id": "hotkey-tap"},
    ]}}
    if owner is not None:
        workflows["owner"] = {"steps": [{"op": "key_tap", "key": "A", "hold_ns": 15}]}
        handlers.append({"id": "owner", "event": {"type": "project_start"},
                         "priority": owner, "workflow_id": "owner"})
    program = validate_program({"ir_version": 1, "project_id": "policies",
                                "handlers": handlers, "workflows": workflows})
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)
    runtime.run(cutoff, [(time, "F7") for time in events])
    return [(e["time_ns"], e["action"]) for e in backend.events], [e["code"] for e in runtime.errors]


def test_queue_runs_serially_and_stop_discards_pending():
    assert run("queue", [1, 2, 3, 4], cutoff=16) == (
        [(1, "key_down"), (11, "key_up"), (11, "key_down"), (16, "key_up")], [],
    )


def test_queue_is_bounded(monkeypatch):
    monkeypatch.setattr("macro_studio.runtime.MAX_PENDING_TASKS", 2)
    assert run("queue", [1, 2, 3, 4], cutoff=5) == (
        [(1, "key_down"), (5, "key_up")], ["queue_limit"],
    )


@pytest.mark.parametrize("policy", ["queue", "parallel", "override"])
def test_existing_owner_keeps_key_after_collision(policy):
    assert run(policy, [2], owner=1, cutoff=8) == (
        [(0, "key_down"), (8, "key_up")], ["input_conflict"],
    )


def test_parallel_collision_leaves_first_instance_running():
    assert run("parallel", [1, 2], cutoff=8) == (
        [(1, "key_down"), (8, "key_up")], ["input_conflict"],
    )


def test_stop_releases_both_parallel_owners():
    program = validate_program({
        "ir_version": 1, "project_id": "parallel-stop", "globals": {"first": True},
        "handlers": [{"id": "f7", "event": {"type": "hotkey_pressed", "hotkey": "F7"},
                      "policy": "parallel", "workflow_id": "tap"}],
        "workflows": {"tap": {"steps": [{
            "op": "if", "condition": {"var": "first"},
            "then": [{"op": "set", "var": "first", "value": False},
                     {"op": "key_tap", "key": "A", "hold_ns": 10}],
            "else": [{"op": "key_tap", "key": "B", "hold_ns": 10}],
        }]}},
    })
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)
    runtime.run(4, [(1, "F7"), (2, "F7")])
    assert not runtime.errors
    assert [(e["time_ns"], e["action"], e["key"]) for e in backend.events] == [
        (1, "key_down", "scan:1E"), (2, "key_down", "scan:30"),
        (4, "key_up", "scan:1E"), (4, "key_up", "scan:30"),
    ]


def test_override_releases_lower_priority_before_press_and_discards_old_deadline():
    assert run("override", [7], owner=0, cutoff=16) == (
        [(0, "key_down"), (7, "key_up"), (7, "key_down"), (16, "key_up")], [],
    )
