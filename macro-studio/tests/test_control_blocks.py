import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError
from macro_studio.runtime import Runtime


def block(kind, identifier, fields=None, **inputs):
    return {"type": f"ms_{kind}", "id": identifier, "fields": fields or {},
            "inputs": {name: {"block": child} for name, child in inputs.items()}}


def chain(*blocks):
    for current, following in zip(blocks, blocks[1:]):
        current["next"] = {"block": following}
    return blocks[0]


def project(body, *workflows):
    return {"project_id": "control", "globals": {"n": 0, "hits": 0},
            "workspace": {"blocks": {"blocks": [block("start", "start", BODY=body), *workflows]}}}


def expression(identifier, op, var, value):
    return block("binary", identifier, {"OP": op},
                 A=block("variable", f"{identifier}-var", {"NAME": var}),
                 B=block("number", f"{identifier}-value", {"VALUE": value}))


def test_while_rechecks_condition_and_continue_skips_only_current_iteration():
    body = chain(
        block("set", "increment", {"NAME": "n"}, VALUE=expression("inc", "add", "n", 1)),
        block("if_only", "skip-two", CONDITION=expression("eq", "eq", "n", 2),
              THEN=block("continue", "continue")),
        block("set", "count-hit", {"NAME": "hits"}, VALUE=expression("hit", "add", "hits", 1)),
    )
    loop = block("while", "while", CONDITION=expression("limit", "lt", "n", 3), BODY=body)
    initially_false = block("while", "false", CONDITION=expression("false-test", "lt", "n", 0),
                            BODY=block("set", "unreachable", {"NAME": "hits"},
                                       VALUE=block("number", "sentinel", {"VALUE": 99})))
    code, _ = compile_project(project(chain(initially_false, loop)))
    clock = VirtualClock()
    runtime = Runtime(code, clock, FakeInputBackend(clock))
    runtime.run(10_000_000, [])
    assert not runtime.errors
    assert runtime.globals == {"n": 3, "hits": 2}


def test_break_exits_only_the_innermost_loop():
    inner = block("forever", "inner", BODY=block("break", "break"))
    outer = block("repeat", "outer", COUNT=block("number", "two", {"VALUE": 2}),
                  BODY=chain(inner, block("set", "hit", {"NAME": "hits"},
                                          VALUE=expression("inc", "add", "hits", 1))))
    code, _ = compile_project(project(outer))
    clock = VirtualClock()
    runtime = Runtime(code, clock, FakeInputBackend(clock))
    runtime.run(10_000_000, [])
    assert not runtime.errors
    assert runtime.globals["hits"] == 2


@pytest.mark.parametrize("kind", ["break", "continue"])
def test_workflow_loop_control_cannot_target_a_callers_loop(kind):
    workflow = block("workflow", "definition", {"NAME": "sub"}, BODY=block(kind, "invalid"))
    loop = block("forever", "loop", BODY=block("call", "call", {"NAME": "sub"}))
    with pytest.raises(ValidationError, match=r"\(invalid\).*requires an enclosing loop"):
        compile_project(project(loop, workflow))
