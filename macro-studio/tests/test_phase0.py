import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def step(op, **fields):
    return {"op": op, "block_id": op, **fields}


def tap(key, hold_ns, *, block_id=None):
    return step("key_tap", key=key, hold_ns=hold_ns, block_id=block_id or f"tap-{key}")


def handler(workflow, *, hotkey=None, policy="ignore_if_running"):
    event = {"type": "project_start"}
    if hotkey is not None:
        event = {"type": "hotkey_pressed", "hotkey": hotkey}
    return {
        "id": workflow,
        "event": event,
        "workflow_id": workflow,
        "policy": policy,
        "priority": 0,
    }


def program(workflows, handlers, **globals_):
    return {
        "ir_version": 1,
        "project_id": "phase0-test",
        "globals": globals_,
        "workflows": {name: {"steps": steps} for name, steps in workflows.items()},
        "handlers": handlers,
    }


@pytest.fixture
def invoke(tmp_path):
    def cli(command, ir, *arguments):
        path = tmp_path / "program.json"
        path.write_text(json.dumps(ir), encoding="utf-8")
        source = str(Path(__file__).resolve().parents[1] / "src")
        environment = os.environ.copy()
        environment["PYTHONPATH"] = source
        return subprocess.run(
            [sys.executable, "-m", "macro_studio", command, str(path), *arguments],
            capture_output=True,
            text=True,
            env=environment,
            timeout=15,
        )

    return cli


def output(result, exit_code=0):
    assert result.returncode == exit_code, result.stderr or result.stdout
    return json.loads(result.stdout)


def timing(log):
    return [(event["time_ns"], event["action"]) for event in log["events"]]


def test_project_start_executes_condition_repeat_and_state_updates(invoke):
    ir = program(
        {
            "automatic": [
                step(
                    "if",
                    condition={"var": "enabled"},
                    then=[step("repeat", count=2, steps=[tap("A", 2_000_000)])],
                    **{"else": [], "block_id": "if-enabled"},
                ),
                step("set", var="enabled", value=False),
                step("if", condition={"var": "enabled"}, then=[tap("B", 1_000_000)], block_id="if-disabled"),
            ],
        },
        [handler("automatic")],
        enabled=True,
    )

    assert invoke("validate", ir).returncode == 0
    log = output(invoke("run", ir, "--duration-ms", "10"))

    assert log["errors"] == []
    assert log["globals"] == {"enabled": False}
    assert timing(log) == [
        (0, "key_down"),
        (2_000_000, "key_up"),
        (2_000_000, "key_down"),
        (4_000_000, "key_up"),
    ]


def test_hotkey_dispatch_respects_virtual_time_and_wait(invoke):
    ir = program(
        {"reactive": [tap("B", 5_000_000), step("wait", duration_ns=10_000_000), tap("C", 5_000_000)]},
        [handler("reactive", hotkey="F7")],
    )

    assert output(invoke("run", ir, "--duration-ms", "250"))["events"] == []
    log = output(invoke("run", ir, "--duration-ms", "250", "--hotkey", "100:F7"))

    assert log["errors"] == []
    assert timing(log) == [
        (100_000_000, "key_down"),
        (105_000_000, "key_up"),
        (115_000_000, "key_down"),
        (120_000_000, "key_up"),
    ]
    assert [event["key"] for event in log["events"]] == ["scan:30", "scan:30", "scan:2E", "scan:2E"]


def test_start_and_hotkey_call_shared_workflow_in_same_session(invoke):
    ir = program(
        {
            "automatic": [step("call", workflow_id="shared"), step("wait", duration_ns=200_000_000)],
            "reactive": [step("call", workflow_id="shared")],
            "shared": [
                step("set", var="calls", value={"op": "add", "args": [{"var": "calls"}, 1]}),
                tap("A", 1_000_000),
            ],
        },
        [handler("automatic"), handler("reactive", hotkey="F7")],
        calls=0,
    )

    arguments = ("--duration-ms", "250", "--hotkey", "100:F7")
    first = invoke("run", ir, *arguments)
    second = invoke("run", ir, *arguments)
    log = output(first)

    assert output(second) == log
    assert log["errors"] == []
    assert log["globals"] == {"calls": 2}
    assert timing(log) == [
        (0, "key_down"),
        (1_000_000, "key_up"),
        (100_000_000, "key_down"),
        (101_000_000, "key_up"),
    ]
    assert {event["key"] for event in log["events"]} == {"scan:1E"}


def test_stop_releases_held_key_and_cancels_pending_work(invoke):
    ir = program(
        {
            "automatic": [tap("A", 100_000_000), tap("B", 1_000_000)],
            "reactive": [tap("C", 1_000_000)],
        },
        [handler("automatic"), handler("reactive", hotkey="F7")],
    )

    log = output(invoke("run", ir, "--duration-ms", "20", "--hotkey", "20:F7"))

    assert log["errors"] == []
    assert timing(log) == [(0, "key_down"), (20_000_000, "key_up")]
    assert log["events"][0]["key"] == log["events"][1]["key"]


def test_tap_sequence_uses_absolute_periods(invoke):
    ir = program(
        {
            "automatic": [
                step("tap_sequence", key="A", frequency_hz=200, hold_ns=2_000_000, count=3),
            ],
        },
        [handler("automatic")],
    )

    log = output(invoke("run", ir, "--duration-ms", "20"))

    assert log["errors"] == []
    assert timing(log) == [
        (0, "key_down"),
        (2_000_000, "key_up"),
        (5_000_000, "key_down"),
        (7_000_000, "key_up"),
        (10_000_000, "key_down"),
        (12_000_000, "key_up"),
    ]


def test_hotkey_dispatch_progresses_while_automatic_loop_has_no_wait(invoke):
    ir = program(
        {
            "automatic": [step("forever", steps=[step("set", var="running", value=True)])],
            "reactive": [tap("B", 1_000_000)],
        },
        [handler("automatic"), handler("reactive", hotkey="F7")],
        running=False,
    )

    log = output(invoke("run", ir, "--duration-ms", "3", "--hotkey", "1:F7"))

    assert log["errors"] == []
    assert log["globals"] == {"running": True}
    assert timing(log) == [(1_000_000, "key_down"), (2_000_000, "key_up")]


def test_later_task_cannot_release_another_tasks_key(invoke):
    ir = program(
        {
            "automatic": [tap("A", 100_000_000)],
            "reactive": [
                tap("B", 1_000_000),
                tap({"kind": "scan_code", "code": 30, "extended": False}, 1_000_000, block_id="collision"),
                tap("C", 1_000_000),
            ],
        },
        [handler("automatic"), handler("reactive", hotkey="F7")],
    )

    log = output(invoke("run", ir, "--duration-ms", "150", "--hotkey", "20:F7"), exit_code=1)

    assert len(log["errors"]) == 1
    assert log["errors"][0]["handler_id"] == "reactive"
    assert log["errors"][0]["block_id"] == "collision"
    assert timing(log) == [
        (0, "key_down"),
        (20_000_000, "key_down"),
        (21_000_000, "key_up"),
        (100_000_000, "key_up"),
    ]
    assert [event["key"] for event in log["events"]] == ["scan:1E", "scan:30", "scan:30", "scan:1E"]


@pytest.mark.parametrize(
    ("policy", "expected"),
    [
        ("ignore_if_running", [(10_000_000, "key_down"), (60_000_000, "key_up")]),
        (
            "restart",
            [(10_000_000, "key_down"), (20_000_000, "key_up"), (20_000_000, "key_down"), (70_000_000, "key_up")],
        ),
    ],
)
def test_hotkey_reentry_policy(invoke, policy, expected):
    ir = program({"reactive": [tap("A", 50_000_000)]}, [handler("reactive", hotkey="F7", policy=policy)])

    log = output(invoke("run", ir, "--duration-ms", "100", "--hotkey", "10:F7", "--hotkey", "20:F7"))

    assert log["errors"] == []
    assert timing(log) == expected


@pytest.mark.parametrize(
    "invalid_step",
    [
        step("unknown"),
        step("wait", duration_ns=-1),
        step("call", workflow_id="missing"),
        step("if", condition=123, then=[], **{"else": []}),
        step("set", var="missing", value=True),
        step("repeat", count=True, steps=[]),
        step("tap_sequence", key="A", frequency_hz=200, hold_ns=5_000_000, count=3),
    ],
)
def test_invalid_ir_is_rejected_before_run(invoke, invalid_step):
    ir = program({"automatic": [tap("A", 1_000_000), invalid_step]}, [handler("automatic")])

    for command, arguments in (("validate", ()), ("run", ("--duration-ms", "10"))):
        result = invoke(command, ir, *arguments)
        assert result.returncode == 2
        assert result.stderr
        assert "key_down" not in result.stdout


def test_unsupported_ir_version_is_rejected(invoke):
    ir = program({"automatic": []}, [handler("automatic")])
    ir["ir_version"] = 4

    assert invoke("validate", ir).returncode == 2
