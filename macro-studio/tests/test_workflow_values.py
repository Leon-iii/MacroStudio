import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError
from macro_studio.runtime import Runtime


def project():
    def block(kind, identifier, fields=None, **inputs):
        return {"type": "ms_" + kind, "id": identifier, "fields": fields or {},
                "inputs": {name: {"block": child} for name, child in inputs.items()}}

    def argument(identifier, value):
        return block("argument", identifier, VALUE=block("number", f"{identifier}-value", {"VALUE": value}))

    save = block("call_store", "save", {"NAME": "positive", "RESULT": "answer"}, ARGS=argument("one", 1))
    branch = block("call_if", "branch", {"NAME": "positive"}, ARGS=argument("zero", 0),
                   THEN=block("key_tap", "bad", {"KEY": "B", "MS": 2}),
                   ELSE=block("key_tap", "good", {"KEY": "A", "MS": 2}))
    save["next"] = {"block": branch}
    wait = block("wait", "wait", {"MS": 10})
    comparison = block("binary", "gt", {"OP": "gt"},
                       A=block("parameter", "param-read", {"NAME": "n"}),
                       B=block("number", "zero-literal", {"VALUE": 0}))
    wait["next"] = {"block": block("return", "return", VALUE=comparison)}
    parameter = block("param_def", "n", {"NAME": "n", "TYPE": "number"})
    workflow = block("workflow", "positive", {"NAME": "positive", "RETURNS": "boolean"},
                     PARAMS=parameter, BODY=wait)
    start = block("start", "start", BODY=save)
    return {"project_id": "values", "globals": {"answer": False},
            "workspace": {"blocks": {"blocks": [workflow, start]}}}


def test_delayed_return_is_stored_and_drives_direct_branch():
    code, detached = compile_project(project())
    assert not detached
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(100_000_000, [])
    assert not runtime.errors and runtime.globals == {"answer": True}
    assert [(edge["time_ns"], edge["action"], edge["key"]) for edge in backend.events] == [
        (20_000_000, "key_down", "scan:1E"), (22_000_000, "key_up", "scan:1E"),
    ]


def test_missing_return_fails_before_overwriting_result():
    data = project()
    del data["workspace"]["blocks"]["blocks"][0]["inputs"]["BODY"]["block"]["next"]
    code, _ = compile_project(data)
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(100_000_000, [])
    assert runtime.globals == {"answer": False} and not backend.events
    assert [(error["code"], error["block_id"]) for error in runtime.errors] == [("missing_return", "save")]


def test_argument_type_mismatch_identifies_call():
    data = project()
    workflow = data["workspace"]["blocks"]["blocks"][0]
    workflow["inputs"]["PARAMS"]["block"]["fields"]["TYPE"] = "boolean"
    workflow["inputs"]["BODY"]["block"]["next"]["block"]["inputs"]["VALUE"]["block"] = {
        "type": "ms_parameter", "id": "read", "fields": {"NAME": "n"},
    }
    with pytest.raises(ValidationError, match=r"\(save\)"):
        compile_project(data)
