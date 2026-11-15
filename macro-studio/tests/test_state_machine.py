import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError
from macro_studio.runtime import Runtime
from macro_studio.sensors import sensor_specs


def project():
    def block(kind, identifier, fields, **inputs):
        return {"type": "ms_" + kind, "id": identifier, "fields": fields,
                "inputs": {name: {"block": child} for name, child in inputs.items()}}

    pixel = block("pixel_color", "pixel", {"X": 1, "Y": 2, "COLOR": "#ff0000"})
    blocks = [
        block("state", "idle", {"NAME": "Idle", "INITIAL": "TRUE", "WORKFLOW": "idle_entry"}),
        block("state", "active", {"NAME": "Active", "WORKFLOW": "active_entry"}),
        block("state_condition", "detect", {"FROM": "Idle", "TO": "Active"}, GUARD=pixel),
        block("state_hotkey", "reset", {"FROM": "Active", "TO": "Idle", "KEY": "ctrl+f7"}),
        block("workflow", "idle-work", {"NAME": "idle_entry"},
              BODY=block("key_tap", "idle-key", {"KEY": "A", "MS": 10})),
        block("workflow", "active-work", {"NAME": "active_entry"},
              BODY=block("key_tap", "active-key", {"KEY": "B", "MS": 300})),
    ]
    return {"project_id": "fsm", "workspace": {"blocks": {"blocks": blocks}}}


def test_sensor_and_hotkey_run_destination_workflows_and_release_previous_input():
    program, detached = compile_project(project())
    assert detached == []
    assert [spec["block_id"] for spec in sensor_specs(program)] == ["pixel"]
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)
    states = []
    runtime.on_transition = states.append
    runtime.read_sensor = lambda key: key == "pixel" and 190_000_000 <= clock.now_ns < 290_000_000
    runtime.run(700_000_000, [(50_000_000, "CTRL+F7"), (300_000_000, "CTRL+F7")])
    assert not runtime.errors
    assert states == ["Idle", "Active", "Idle"]
    assert [(item["time_ns"] // 1_000_000, item["action"], item["key"]) for item in backend.events] == [
        (0, "key_down", "scan:1E"), (10, "key_up", "scan:1E"),
        (200, "key_down", "scan:30"), (300, "key_up", "scan:30"),
        (300, "key_down", "scan:1E"), (310, "key_up", "scan:1E"),
    ]


def test_unknown_transition_target_names_the_block():
    data = project()
    data["workspace"]["blocks"]["blocks"][2]["fields"]["TO"] = "missing"
    with pytest.raises(ValidationError, match=r"\(detect\)"):
        compile_project(data)
