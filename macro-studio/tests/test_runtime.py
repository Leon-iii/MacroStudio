from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.ir import MAX_CALL_DEPTH, validate_program
from macro_studio.runtime import Runtime


def test_nested_blocks_reach_workflow_call_depth_without_python_recursion():
    workflows = {}
    for index in range(MAX_CALL_DEPTH):
        if index + 1 == MAX_CALL_DEPTH:
            steps = [{"op": "key_tap", "key": "A", "hold_ns": 1}]
        else:
            steps = [{"op": "call", "workflow_id": str(index + 1)}]
        for _ in range(32):
            steps = [{"op": "if", "condition": True, "then": steps}]
        workflows[str(index)] = {"steps": steps}
    program = validate_program({
        "ir_version": 1,
        "project_id": "nested-workflows",
        "workflows": workflows,
        "handlers": [{"id": "start", "event": {"type": "project_start"}, "workflow_id": "0"}],
    })
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)
    runtime.run(20_000_000, [])

    assert runtime.errors == []
    assert [(event["action"], event["key"]) for event in backend.events] == [
        ("key_down", "scan:1E"),
        ("key_up", "scan:1E"),
    ]


def test_already_scheduled_release_precedes_reactive_task_at_same_time():
    steps = [{"op": "key_tap", "key": "A", "hold_ns": 5_000_000}]
    program = validate_program({
        "ir_version": 1,
        "project_id": "same-time-release",
        "workflows": {"tap": {"steps": steps}},
        "handlers": [
            {"id": "start", "event": {"type": "project_start"}, "workflow_id": "tap"},
            {"id": "hotkey", "event": {"type": "hotkey_pressed", "hotkey": "F7"}, "workflow_id": "tap"},
        ],
    })
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)
    runtime.run(15_000_000, [(5_000_000, "F7")])

    assert runtime.errors == []
    assert [(event["time_ns"], event["action"]) for event in backend.events] == [
        (0, "key_down"),
        (5_000_000, "key_up"),
        (5_000_000, "key_down"),
        (10_000_000, "key_up"),
    ]
