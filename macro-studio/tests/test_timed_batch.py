import json
from pathlib import Path

import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError, validate_program
from macro_studio.runtime import Runtime


def test_dry_run_replays_each_batch_against_its_own_start():
    source = Path(__file__).parents[1] / "examples" / "timed-batch.json"
    program = validate_program(json.loads(source.read_text(encoding="utf-8")))
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)
    runtime.run(200_000_000, [])
    assert not runtime.errors
    assert backend.mouse_position() == (12, 12)
    assert [(event["action"], event["time_ns"] // 1_000_000) for event in backend.events] == [
        ("mouse_move", 0), ("mouse_move", 40), ("key_down", 40),
        ("key_up", 80), ("mouse_down", 110), ("mouse_up", 150),
    ]


def test_batch_compiles_blockly_events_and_cleans_up_at_cutoff():
    up = {"type": "ms_batch_key", "id": "up", "fields": {"KEY": "A", "OFFSET": 400, "ACTION": "up"}}
    down = {"type": "ms_batch_key", "id": "down", "fields": {"KEY": "A", "OFFSET": 10, "ACTION": "down"},
            "next": {"block": up}}
    batch = {"type": "ms_timed_batch", "id": "batch", "inputs": {"EVENTS": {"block": down}}}
    start = {"type": "ms_start", "id": "start", "inputs": {"BODY": {"block": batch}}}
    project = {"project_id": "batch", "workspace": {"blocks": {"blocks": [start]}}}
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    program, warnings = compile_project(project)
    assert not warnings
    runtime = Runtime(program, clock, backend)
    runtime.run(100_000_000, [])
    assert not runtime.errors
    assert [(event["action"], event["key"], event["time_ns"]) for event in backend.events] == [
        ("key_down", "scan:1E", 10_000_000), ("key_up", "scan:1E", 100_000_000),
    ]


@pytest.mark.parametrize("events", [
    [],
    [{"offset_us": 0, "action": "key_down", "key": "A"}],
    [{"offset_us": 2, "action": "key_down", "key": "A"},
     {"offset_us": 1, "action": "key_up", "key": "A"}],
    [{"offset_us": 0, "action": "mouse_move", "dx": 12, "dy": 0},
     {"offset_us": 1, "action": "mouse_down", "button": "left"},
     {"offset_us": 2, "action": "mouse_up", "button": "left"}],
])
def test_invalid_batch_is_rejected_before_any_input(events):
    with pytest.raises(ValidationError, match="events"):
        validate_program({
            "ir_version": 1, "project_id": "batch",
            "workflows": {"w": {"steps": [{"op": "timed_batch", "events": events}]}},
            "handlers": [],
        })
