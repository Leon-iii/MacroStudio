import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError
from macro_studio.runtime import Runtime


def project(*blocks):
    return {"project_id": "sample", "workspace": {"blocks": {"blocks": list(blocks)}}}


def test_compiles_hat_with_actions_and_reports_detached_blocks():
    step = {"type": "ms_key_tap", "id": "tap-a", "fields": {"KEY": "A", "MS": 2}}
    hat = {"type": "ms_start", "id": "startup", "inputs": {"BODY": {"block": step}}}
    ir, detached = compile_project(project(hat, {"type": "ms_wait", "id": "loose"}))
    assert detached == ["loose"]
    assert ir["handlers"][0]["event"] == {"type": "project_start"}
    assert ir["workflows"]["@startup"]["steps"] == [
        {"op": "key_tap", "block_id": "tap-a", "key": "scan:1E", "hold_ns": 2_000_000},
    ]


def test_invalid_hotkey_error_names_block():
    with pytest.raises(ValidationError, match=r"\(trigger\)"):
        compile_project(project({"type": "ms_hotkey", "id": "trigger", "fields": {"KEY": "broken"}}))


@pytest.mark.parametrize(("trigger_key", "action_key", "error_block"), [
    ("ctrl+f7", "ESC", None),
    ("F7", "A", "trigger"),
    ("CTRL+F7", "CTRL+A", "tap"),
])
def test_key_objects_validate_their_connection_and_drive_hotkey_trigger(trigger_key, action_key, error_block):
    def key(value):
        return {"shadow": {"type": "ms_key", "fields": {"VALUE": value}}}

    tap = {"type": "ms_key_tap", "id": "tap", "fields": {"MS": 2}, "inputs": {"KEY": key(action_key)}}
    trigger = {"type": "ms_hotkey", "id": "trigger", "inputs": {"KEY": key(trigger_key), "BODY": {"block": tap}}}
    if error_block:
        with pytest.raises(ValidationError, match=rf"\({error_block}\)"):
            compile_project(project(trigger))
        return
    code, _ = compile_project(project(trigger))
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(10_000_000, [(1_000_000, "CTRL+F7")])
    assert not runtime.errors
    assert [(edge["action"], edge["key"]) for edge in backend.events] == [
        ("key_down", "scan:01"), ("key_up", "scan:01"),
    ]


@pytest.mark.parametrize(("unit", "divisor"), [("ms", 2), ("sec", 2_000), ("min", 120_000)])
def test_wait_uses_live_numeric_expression_and_selected_unit(unit, divisor):
    def block(kind, fields=None, **inputs):
        return {"type": f"ms_{kind}", "id": kind, "fields": fields or {},
                "inputs": {name: {"block": value} for name, value in inputs.items()}}

    amount = block("binary", {"OP": "div"}, A=block("variable", {"NAME": "delay"}),
                   B=block("number", {"VALUE": divisor}))
    wait = block("wait_time", DURATION=block("duration", {"UNIT": unit}, VALUE=amount))
    wait["next"] = {"block": block("key_tap", {"KEY": "A", "MS": 2})}
    set_delay = block("set", {"NAME": "delay"}, VALUE=block("number", {"VALUE": 1000}))
    set_delay["next"] = {"block": wait}
    code, _ = compile_project({**project(block("start", BODY=set_delay)), "globals": {"delay": 0}})
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(600_000_000, [])
    assert not runtime.errors
    assert [(event["action"], event["time_ns"]) for event in backend.events] == [
        ("key_down", 500_000_000), ("key_up", 502_000_000),
    ]


def test_wait_uses_shadow_defaults_and_prefers_an_explicit_input():
    amount = {"shadow": {"type": "ms_number", "fields": {"VALUE": 1}}}
    duration = {"type": "ms_duration", "fields": {"UNIT": "sec"}, "inputs": {"VALUE": amount}}
    wait = {"type": "ms_wait_time", "id": "wait", "inputs": {"DURATION": {"block": duration}}}
    start = {"type": "ms_start", "id": "start", "inputs": {"BODY": {"block": wait}}}

    code, _ = compile_project(project(start))
    assert code["workflows"]["@start"]["steps"][0]["duration"] == {"value": 1, "unit": "sec"}

    amount["block"] = {"type": "ms_number", "id": "explicit", "fields": {"VALUE": 2}}
    code, _ = compile_project(project(start))
    assert code["workflows"]["@start"]["steps"][0]["duration"] == {"value": 2, "unit": "sec"}


@pytest.mark.parametrize("hold_ms", [2, 20])
def test_mouse_hold_and_keyboard_sequence_use_connected_values(hold_ms):
    def block(kind, identifier, fields=None, **inputs):
        return {"type": f"ms_{kind}", "id": identifier, "fields": fields or {},
                "inputs": {name: {"block": child} for name, child in inputs.items()}}

    def number(identifier, value):
        return block("number", identifier, {"VALUE": value})

    def arithmetic(identifier, op, left, right):
        return block("arithmetic", identifier, {"OP": op}, A=number(f"{identifier}-a", left),
                     B=number(f"{identifier}-b", right))

    def duration(identifier, unit, amount):
        return block("duration", identifier, {"UNIT": unit}, VALUE=amount)

    mouse = block("mouse_click", "mouse", {"BUTTON": "left"},
                  DURATION=duration("mouse-hold", "ms", arithmetic("mouse-time", "add", 1, 2)))
    key = block("key_tap", "key", {"KEY": "A"}, DURATION=duration("key-hold", "sec", number("seconds", 0.004)))
    sequence = block("tap_sequence", "sequence", {"KEY": "B"}, HZ=arithmetic("hz", "div", 150, 3),
                     DURATION=duration("seq-hold", "ms", arithmetic("hold", "add", hold_ms - 1, 1)),
                     COUNT=arithmetic("count", "add", 1, 2))
    mouse["next"] = {"block": key}
    key["next"] = {"block": sequence}
    code, _ = compile_project(project(block("start", "start", BODY=mouse)))
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(100_000_000, [])
    events = [(event["time_ns"] // 1_000_000, event["action"]) for event in backend.events]
    assert events[:4] == [(0, "mouse_down"), (3, "mouse_up"), (3, "key_down"), (7, "key_up")]
    if hold_ms == 2:
        assert not runtime.errors
        assert events[4:] == [(7, "key_down"), (9, "key_up"), (27, "key_down"), (29, "key_up"),
                              (47, "key_down"), (49, "key_up")]
    else:
        assert events[4:] == []
        assert [(error["code"], error["block_id"]) for error in runtime.errors] == [
            ("tap_sequence_duration", "sequence"),
        ]
