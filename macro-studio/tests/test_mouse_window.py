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


def coordinate_project():
    def block(kind, identifier, fields=None, **inputs):
        return {"type": f"ms_{kind}", "id": identifier, "fields": fields or {},
                "inputs": {name: {"block": child} for name, child in inputs.items()}}

    x = block("arithmetic", "x", {"OP": "add"}, A=block("variable", "base", {"NAME": "base_x"}),
              B=block("number", "offset", {"VALUE": 20}))
    y = block("arithmetic", "y", {"OP": "div"}, A=block("number", "top", {"VALUE": -149}),
              B=block("number", "divisor", {"VALUE": 3}))
    move = block("mouse_move", "move", POSITION=block("position", "destination", X=x, Y=y),
                 DURATION=block("duration", "time", {"UNIT": "ms"}, VALUE=block("number", "value", {"VALUE": 40})))
    click = block("mouse_click_at", "click", {"BUTTON": "left"},
                  POSITION=block("position", "click-position", {"X": 130, "Y": 70}))
    move["next"] = {"block": click}
    hotkey = block("hotkey", "hotkey", {"KEY": "CTRL+F7"},
                   BODY=block("key_tap", "tap", {"KEY": "A", "MS": 2}))
    return compile_project({"project_id": "coordinates", "globals": {"base_x": 80}, "workspace": {
        "blocks": {"blocks": [block("start", "start", BODY=move), hotkey]},
    }})[0]


def test_coordinate_move_reaches_destination_on_time_without_blocking_hotkeys():
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(coordinate_project(), clock, backend)
    runtime.run(100_000_000, [(20_000_000, "CTRL+F7")])
    assert not runtime.errors
    assert [(event["time_ns"] // 1_000_000, event["action"]) for event in backend.events] == [
        (16, "mouse_move"), (20, "key_down"), (22, "key_up"), (32, "mouse_move"),
        (40, "mouse_move"), (40, "mouse_move"), (40, "mouse_down"), (90, "mouse_up"),
    ]
    assert [(event["x"], event["y"]) for event in backend.events if event["action"] == "mouse_move"] == [
        (40, -20), (80, -40), (100, -50), (130, 70),
    ]


def test_coordinate_click_is_released_on_stop_and_cancelled_move_can_restart():
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(coordinate_project(), clock, backend)
    runtime.run(25_000_000, [])
    assert backend.cursor == (40, -20)
    assert not runtime.errors
    backend.events.clear()

    def stop_after_press():
        return ("stop" if any(event["action"] == "mouse_down" for event in backend.events) else None, [])

    runtime.run(100_000_000, [], poll=stop_after_press)
    assert not runtime.errors
    assert runtime.termination == "stopped"
    assert [event["action"] for event in backend.events[-2:]] == ["mouse_down", "mouse_up"]
    assert backend.cursor == (130, 70)
