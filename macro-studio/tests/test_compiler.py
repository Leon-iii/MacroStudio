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
