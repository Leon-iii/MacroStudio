from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.runtime import Runtime


def program():
    click = {"type": "ms_mouse_click", "id": "click", "fields": {"BUTTON": "left", "MS": 50}}
    window = {"type": "ms_target_window", "id": "window", "fields": {"STATE": "foreground"}}
    block = {"type": "ms_start", "id": "start", "inputs": {
        "GUARD": {"block": window}, "BODY": {"block": click},
    }}
    project = {"project_id": "click", "workspace": {"blocks": {"blocks": [block]}}}
    return compile_project(project)[0]


def test_window_condition_gates_click_and_stop_releases_button():
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program(), clock, backend)
    runtime.run(10_000_000, [])
    assert backend.events == []

    runtime.read_window = lambda state: state == "foreground"
    runtime.run(10_000_000, [], poll=lambda: ("stop" if backend.events else None, []))
    assert not runtime.errors
    assert runtime.termination == "stopped"
    assert backend.events == [
        {"time_ns": 0, "action": "mouse_down", "button": "left"},
        {"time_ns": 0, "action": "mouse_up", "button": "left"},
    ]


def test_mouse_click_releases_after_hold():
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program(), clock, backend)
    runtime.read_window = lambda state: True
    runtime.run(100_000_000, [])
    assert [(event["time_ns"], event["action"]) for event in backend.events] == [
        (0, "mouse_down"), (50_000_000, "mouse_up"),
    ]
