from collections import deque

import pytest

from macro_studio.backend import VirtualClock
from macro_studio.ir import ValidationError
from macro_studio.perception import RecognitionResult, RecognitionStatus
from macro_studio.python_compiler import compile_python, dry_run_python
from macro_studio.python_dsl import AnalysisError


MS = 1_000_000
PROJECT = {"project_id": "python-test", "image_assets": {"asset:confirm": {"name": "Confirm"}}}


def run(source, **settings):
    return dry_run_python(compile_python(source, PROJECT), source, PROJECT, duration_ns=500 * MS, **settings)


def test_hotkey_repeat_and_stop_use_normal_ir_and_fake_output():
    source = """
@on_hotkey("Ctrl+F6")
async def rapid():
    await keyboard.repeat("A", 200, 2, 3)
    await macro.stop()
    await keyboard.tap("B")
"""
    snapshot = compile_python(source, PROJECT)
    assert snapshot.program_for(source, PROJECT)["ir_version"] == 1
    assert dict(snapshot.source_map)["py:rapid:1"].line == 4
    runtime, output = run(source, hotkeys=[(10 * MS, "CTRL+F6")])
    assert runtime.errors == []
    assert [(event["action"], event["time_ns"] // MS) for event in output.events] == [
        ("key_down", 10), ("key_up", 12), ("key_down", 15), ("key_up", 17),
        ("key_down", 20), ("key_up", 22),
    ]
    assert {event["key"] for event in output.events} == {"scan:1E"}


def test_workflow_locals_survive_waits_and_are_isolated_across_tasks():
    source = """
@on_start
async def launch():
    await workflow.call("loop", 3)

@on_hotkey("Ctrl+F6")
async def concurrent():
    await workflow.call("loop", 1)

@workflow("loop")
async def process(limit: int):
    total = 0
    for index in range(limit):
        total += index + 1
        index = 99
        await macro.sleep(10)
    if total == 6:
        await keyboard.tap("A", 1)
    else:
        await keyboard.tap("B", 1)
"""
    snapshot = compile_python(source, PROJECT)
    assert snapshot.program_for(source, PROJECT)["ir_version"] == 4
    runtime, output = run(source, hotkeys=[(5 * MS, "CTRL+F6")])
    assert runtime.errors == []
    assert runtime.globals == {}
    assert [(row["key"], row["time_ns"] // MS) for row in output.events if row["action"] == "key_down"] == [
        ("scan:30", 15), ("scan:1E", 30),
    ]


def test_while_condition_and_loop_control():
    source = """
@on_start()
async def start():
    count = 0
    while count < 5:
        count += 1
        if count == 1:
            continue
        if count == 3:
            break
        await keyboard.tap("A", 1)
"""
    runtime, output = run(source)
    assert runtime.errors == []
    assert [row["action"] for row in output.events] == ["key_down", "key_up"]


class Replay:
    def __init__(self, *results):
        self.results = deque(results)
        self.cancelled = []

    def request(self, spec):
        assert spec["image"] == "asset:confirm"
        return 1

    def take(self, request_id):
        return self.results.popleft() if self.results else None

    def cancel(self, request_id):
        self.cancelled.append(request_id)


def recognition(ms, status):
    found = status == RecognitionStatus.FOUND
    return RecognitionResult(status, "template", "asset:confirm", ms, "fake", ms * MS, ms * MS,
                             bbox=(8, 18, 4, 4) if found else None, center_x=10 if found else None,
                             center_y=20 if found else None)


@pytest.mark.parametrize("status", [RecognitionStatus.FOUND, RecognitionStatus.NOT_FOUND, RecognitionStatus.UNKNOWN])
def test_vision_branch_coordinate_motion_and_click(status):
    source = """
@on_start
async def confirm():
    target = await vision.find("asset:confirm")
    if target.status == "FOUND":
        x = target.center_x
        await mouse.move_to(x, target.center_y, profile_id="builtin.direct", duration_ms=10)
        await mouse.click()
"""
    replay = Replay(recognition(0, status), recognition(10, status))
    runtime, output = run(source, perception=replay)
    assert runtime.errors == []
    if status == RecognitionStatus.FOUND:
        assert output.cursor == (10, 20)
        assert [row["action"] for row in output.events[-2:]] == ["mouse_down", "mouse_up"]
    else:
        assert output.events == []


def test_stop_cancels_pending_recognition_and_releases_held_key():
    source = """
@on_start
async def pending():
    target = await vision.find("asset:confirm")

@on_hotkey("Ctrl+F6")
async def tap():
    await keyboard.tap("A", 100)
"""
    clock = VirtualClock()
    replay = Replay()

    def poll():
        return ("stop", []) if clock.now_ns >= 10 * MS else (None, [])

    runtime, output = run(source, hotkeys=[(0, "CTRL+F6")], clock=clock, perception=replay, poll=poll)
    assert runtime.termination == "stopped"
    assert replay.cancelled == [1]
    assert [row["action"] for row in output.events] == ["key_down", "key_up"]
    assert output.events[-1]["time_ns"] == 10 * MS


def test_snapshot_refuses_stale_source_and_project_and_returns_isolated_program():
    source = "@on_start\nasync def main(): await mouse.move_to(10, 20)"
    snapshot = compile_python(source, PROJECT)
    snapshot.program_for(source, PROJECT)["workflows"].clear()
    assert snapshot.program_for(source, PROJECT)["workflows"]
    with pytest.raises(ValidationError, match="STALE_SNAPSHOT"):
        dry_run_python(snapshot, source + "\n", PROJECT, duration_ns=100 * MS)
    with pytest.raises(ValidationError, match="STALE_SNAPSHOT"):
        snapshot.program_for(source, dict(PROJECT, settings={"default_mouse_motion_profile_id": "builtin.direct"}))


def test_stale_recognition_coordinates_remain_tracked_through_a_local():
    source = """
@on_start
async def main():
    target = await vision.find("asset:confirm")
    x = target.center_x
    y = target.center_y
    await macro.sleep(300)
    await mouse.move_to(x, y)
"""
    runtime, output = run(source, perception=Replay(recognition(0, RecognitionStatus.FOUND)))
    assert output.events == []
    assert runtime.errors[0]["code"] == "vision_stale_target"


def test_local_arithmetic_stops_on_oversized_value():
    source = """
@on_start
async def main():
    x = 65536
    while x > 0:
        x *= x
    await keyboard.tap("A")
"""
    runtime, output = run(source)
    assert output.events == []
    assert runtime.errors[0]["code"] == "local_number_range"


@pytest.mark.parametrize(("body", "code"), [
    ("while True: pass", "UNBOUNDED_LOOP"),
    ("if 1: pass", "TYPE_ERROR"),
    ("if False: x = 1\n    await macro.sleep(x)", "UNKNOWN_NAME"),
    ('target = await vision.find("asset:missing")', "UNKNOWN_ASSET"),
    ('await workflow.call("missing")', "UNKNOWN_NAME"),
    ('await navigation.move_to("x")', "UNAVAILABLE_SERVICE"),
])
def test_lowering_errors_are_structured(body, code):
    with pytest.raises(AnalysisError) as raised:
        compile_python(f"@on_start\nasync def main():\n    {body}", PROJECT)
    assert raised.value.diagnostic.code == code
    assert raised.value.diagnostic.source.line >= 3


def test_recursive_workflow_is_rejected():
    with pytest.raises(AnalysisError) as raised:
        compile_python('@workflow\nasync def one(): await workflow.call("one")', PROJECT)
    assert raised.value.diagnostic.code == "RECURSIVE_WORKFLOW"
