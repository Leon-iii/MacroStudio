from collections import deque
from dataclasses import replace

import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.editor_runner import open_perception
from macro_studio.ir import ValidationError, validate_program, vision_nodes
from macro_studio.live import run_live
from macro_studio.perception import RecognitionResult, RecognitionStatus
from macro_studio.runtime import Runtime


MS = 1_000_000
SPEC = {"detector": "template", "image": "target.png", "max_age_ms": 50}


class Replay:
    def __init__(self, clock, *results):
        self.clock = clock
        self.results = deque(results)
        self.requests = {}
        self.subscriptions = {}
        self.cancelled = []
        self.unsubscribed = []
        self.events = []
        self.latest_result = None
        self.sequence = 0

    def request(self, spec):
        self.sequence += 1
        self.requests[self.sequence] = self.results.popleft() if self.results else None
        return self.sequence

    def take(self, identifier):
        item = self.requests[identifier]
        if item is None or self.clock.now_ns < item[0] * MS:
            return None
        del self.requests[identifier]
        self.latest_result = item[1]
        return self.latest_result

    def latest(self, _spec):
        return self.latest_result

    def cancel(self, identifier):
        self.cancelled.append(identifier)
        del self.requests[identifier]

    def subscribe(self, spec):
        self.sequence += 1
        self.subscriptions[self.sequence] = spec
        return self.sequence

    def unsubscribe(self, identifier):
        self.unsubscribed.append(identifier)
        del self.subscriptions[identifier]

    def drain_events(self):
        events, self.events = self.events, []
        return events


def recognition(captured_ms, *, status=RecognitionStatus.FOUND, center=(10, 20), error=None):
    data = {"bbox": (center[0] - 2, center[1] - 2, 4, 4), "center_x": center[0], "center_y": center[1]}
    return RecognitionResult(status, "template", None, captured_ms, "monitor.1", captured_ms * MS,
                             captured_ms * MS, score=0.95, error_code=error,
                             **(data if status == RecognitionStatus.FOUND else {}))


def command(op="vision_find", **fields):
    return {"op": op, "block_id": op, "spec": SPEC, "result_var": "target", **fields}


def result_field(name):
    return {"op": "vision_result", "block_id": name, "result_var": "target", "field": name}


def handler(name, kind="project_start", *, event_fields=None, **fields):
    return {"id": name, "event": {"type": kind, **(event_fields or {})}, "workflow_id": name, **fields}


def program(steps, *, extra_workflows=None, handlers=None, globals=None):
    return validate_program({"ir_version": 3, "project_id": "vision", "globals": globals or {},
                             "workflows": {"start": {"steps": steps}, **(extra_workflows or {})},
                             "handlers": [handler("start")] if handlers is None else handlers})


def recorded(code, clock, replay, *, hotkeys=(), poll=None, duration_ms=150):
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend, perception=replay)
    runtime.run(duration_ms * MS, list(hotkeys), poll=poll)
    return runtime, backend.events


def test_pending_find_preserves_hotkey_dispatch_and_exposes_task_local_result():
    clock = VirtualClock()
    replay = Replay(clock, (30, recognition(30)))
    tap = {"op": "key_tap", "key": "A", "hold_ns": MS}
    visible = {"op": "vision_visible", "block_id": "visible", "spec": SPEC}
    code = program([command(), {"op": "if", "condition": visible, "then": [
        {"op": "set", "var": "status", "value": result_field("status")},
        {"op": "set", "var": "score", "value": result_field("score")},
    ]}], extra_workflows={"key": {"steps": [tap, {"op": "set", "var": "other", "value": result_field("status")}]}},
        handlers=[handler("start"), handler("key", "hotkey_pressed", event_fields={"hotkey": "F6"})],
        globals={"status": "", "other": "", "score": 0})
    runtime, inputs = recorded(code, clock, replay, hotkeys=[(4 * MS, "F6")])
    assert runtime.globals == {"status": "FOUND", "other": "UNKNOWN", "score": 0.95}
    assert runtime.errors == []
    assert inputs[0]["time_ns"] == 4 * MS


def test_wait_timeout_and_capture_failure_stay_unknown():
    for results, error, warnings in (
        ([(0, recognition(0, status=RecognitionStatus.NOT_FOUND))], "TIMEOUT", []),
        ([(0, recognition(0, status=RecognitionStatus.UNKNOWN, error="CAPTURE_FAILED"))],
         "CAPTURE_FAILED", ["vision_unknown"]),
    ):
        clock = VirtualClock()
        replay = Replay(clock, *results)
        code = program([command("vision_wait", condition="appear", timeout_ms=50),
                        {"op": "set", "var": "status", "value": result_field("status")},
                        {"op": "set", "var": "error", "value": result_field("error_code")}],
                       globals={"status": "", "error": ""})
        runtime, inputs = recorded(code, clock, replay)
        assert runtime.globals == {"status": "UNKNOWN", "error": error}
        assert runtime.errors == []
        assert [warning["code"] for warning in runtime.warnings] == warnings
        assert inputs == []
        assert not replay.requests


def test_hsv_result_exposes_pixel_count_fraction_and_expiry():
    clock = VirtualClock()
    result = replace(recognition(0), detector_id="hsv", pixel_count=80, fraction=0.25)
    replay = Replay(clock, (0, result))
    spec = {"detector": "hsv", "hsv_lower": [0, 50, 50], "hsv_upper": [10, 255, 255], "max_age_ms": 50}
    code = program([command(spec=spec),
                    {"op": "set", "var": "count", "value": result_field("pixel_count")},
                    {"op": "set", "var": "fraction", "value": result_field("fraction")},
                    {"op": "set", "var": "initial_error", "value": result_field("error_code")},
                    {"op": "wait", "duration_ns": 51 * MS},
                    {"op": "set", "var": "expired_error", "value": result_field("error_code")}],
                   globals={"count": 0, "fraction": 0, "initial_error": "unset", "expired_error": "unset"})
    runtime, _ = recorded(code, clock, replay)
    assert runtime.errors == []
    assert runtime.globals == {"count": 80, "fraction": 0.25, "initial_error": "", "expired_error": "STALE_RESULT"}


def test_pause_and_stop_cancel_waits_and_reset_image_subscriptions():
    class PollClock(VirtualClock):
        def advance_to(self, deadline_ns):
            super().advance_to(min(deadline_ns, self.now_ns + MS))

    clock = PollClock()
    replay = Replay(clock)
    watched = {"spec": SPEC}
    code = program([command("vision_wait", condition="appear", timeout_ms=None)],
                   extra_workflows={"appear": {"steps": []}, "resume_find": {"steps": [command()]}},
                   handlers=[handler("start"), handler("appear", "on_image_appeared", event_fields=watched),
                             handler("resume_find", "hotkey_pressed", event_fields={"hotkey": "F6"})])
    requests = iter([(10, "pause", []), (20, "resume", ["F6"]), (30, "stop", [])])
    scheduled = next(requests)

    def poll():
        nonlocal scheduled
        at, action, keys = scheduled
        if clock.now_ns < at * MS:
            return None, []
        scheduled = next(requests, scheduled)
        return action, keys

    runtime, inputs = recorded(code, clock, replay, poll=poll)
    assert runtime.termination == "stopped"
    assert len(replay.cancelled) == 2
    assert len(set(replay.unsubscribed)) == 2
    assert not replay.subscriptions and not replay.requests
    assert inputs == []


def test_image_edges_use_priority_and_drop_duplicate_sequence():
    clock = VirtualClock()
    replay = Replay(clock)
    watched = {"spec": SPEC}
    key = lambda name: {"steps": [{"op": "key_tap", "key": name, "hold_ns": MS}]}
    code = program([], extra_workflows={"appear": key("A"), "disappear": key("B")}, handlers=[
        handler("appear", "on_image_appeared", event_fields=watched, policy="queue"),
        handler("disappear", "on_image_disappeared", event_fields=watched, priority=10),
    ])
    sent = False

    def poll():
        nonlocal sent
        if not sent:
            appear, disappear = replay.subscriptions
            first = {"subscription_id": appear, "event_seq": 3, "type": "image_appeared"}
            replay.events.extend([first, first, {"subscription_id": disappear, "event_seq": 4,
                                                 "type": "image_disappeared"}])
            sent = True
        return None, []

    runtime, inputs = recorded(code, clock, replay, poll=poll)
    assert runtime.errors == []
    assert [item["key"] for item in inputs if item["action"] == "key_down"] == ["scan:30", "scan:1E"]


def test_cached_unknown_is_false_and_reports_warning():
    clock = VirtualClock()
    replay = Replay(clock)
    code = program([{"op": "set", "var": "visible",
                     "value": {"op": "vision_visible", "block_id": "visible", "spec": SPEC}}],
                   globals={"visible": True})
    runtime, _ = recorded(code, clock, replay)
    assert runtime.globals["visible"] is False
    assert runtime.errors == []
    assert [warning["block_id"] for warning in runtime.warnings] == ["visible"]


def test_stale_result_blocks_motion_through_scalar_global_and_workflow_parameter():
    clock = VirtualClock()
    replay = Replay(clock, (0, recognition(0)))
    code = program([command(), {"op": "set", "var": "x", "value": result_field("x")},
                    {"op": "wait", "duration_ns": 51 * MS},
                    {"op": "call", "workflow_id": "move", "args": [{"var": "x"}]}],
                   extra_workflows={"move": {"params": [{"name": "x", "type": "number"}], "steps": [
                       {"op": "mouse_move", "block_id": "move", "duration": {"value": 0, "unit": "ms"},
                        "position": {"x": {"var": "@arg:x"}, "y": 20}},
                   ]}}, globals={"x": 0})
    runtime, inputs = recorded(code, clock, replay)
    assert [error["code"] for error in runtime.errors] == ["vision_stale_target"]
    assert inputs == []


@pytest.mark.parametrize("revalidate,new_center,error", [
    (True, (10, 20), None),
    (True, (15, 20), "vision_target_moved"),
    (False, (10, 20), "vision_stale_target"),
])
def test_click_after_move_uses_fresh_bbox_or_skips(revalidate, new_center, error):
    clock = VirtualClock()
    replay = Replay(clock, (0, recognition(0)), (70, recognition(70, center=new_center)))
    code = program([command(), {"op": "mouse_move", "duration": {"value": 60, "unit": "ms"},
                                "position": {axis: result_field(axis) for axis in ("x", "y")}},
                    {"op": "mouse_click", "block_id": "click", "button": "left", "hold_ns": MS,
                     "revalidate_before_click": revalidate}])
    runtime, inputs = recorded(code, clock, replay)
    click = [item for item in inputs if item["action"] == "mouse_down"]
    if error:
        assert [(fault["block_id"], fault["code"]) for fault in runtime.errors] == [("click", error)]
        assert click == []
    else:
        assert runtime.errors == []
        assert [item["time_ns"] for item in click] == [70 * MS]


def test_health_invalidation_blocks_motion_even_before_max_age():
    clock = VirtualClock()
    replay = Replay(clock, (0, recognition(0)))
    replay.is_current = lambda _result: clock.now_ns < 20 * MS
    code = program([command(), {"op": "set", "var": "x", "value": result_field("x")},
                    {"op": "wait", "duration_ns": 20 * MS},
                    {"op": "set", "var": "status", "value": result_field("status")},
                    {"op": "mouse_move", "duration": {"value": 0, "unit": "ms"},
                     "position": {"x": {"var": "x"}, "y": 20}}], globals={"status": "", "x": 0})
    runtime, inputs = recorded(code, clock, replay)
    assert runtime.globals["status"] == "UNKNOWN"
    assert [error["code"] for error in runtime.errors] == ["vision_stale_target"]
    assert inputs == []


def test_timed_batch_cannot_click_a_vision_derived_pointer_target():
    clock = VirtualClock()
    replay = Replay(clock, (0, recognition(0)))
    code = program([command(), {"op": "mouse_move", "duration": {"value": 0, "unit": "ms"},
                                "position": {axis: result_field(axis) for axis in ("x", "y")}},
                    {"op": "timed_batch", "block_id": "batch", "events": [
                        {"offset_us": 0, "action": "mouse_down", "button": "left"},
                        {"offset_us": 1000, "action": "mouse_up", "button": "left"},
                    ]}])
    runtime, inputs = recorded(code, clock, replay)
    assert [(error["block_id"], error["code"]) for error in runtime.errors] == [("batch", "vision_batch_click")]
    assert [item["action"] for item in inputs] == ["mouse_move"]


def test_compiler_preserves_vision_identity_and_validates_hsv_and_image():
    fields = {"RESULT": "target", "DETECTOR": "hsv", "HSV_LOWER": "165, 60, 60", "HSV_UPPER": "15, 255, 255",
              "ROI_LEFT": 5, "ROI_TOP": 6, "ROI_WIDTH": 10, "ROI_HEIGHT": 10}
    find = {"type": "ms_vision_find", "id": "find", "fields": fields}
    start = {"type": "ms_on_image_appeared", "id": "appear", "fields": {"IMAGE": "asset:target"},
             "next": {"block": find}}
    project = {"project_id": "vision", "workspace": {"blocks": {"blocks": [start]}},
               "image_assets": {"asset:target": {"recognition_profile_id": "builtin.strict"}}}
    compiled, detached = compile_project(project)
    assert detached == []
    assert compiled["ir_version"] == 3
    nodes = {node["block_id"]: node for node in vision_nodes(compiled)}
    assert nodes["find"]["spec"]["hsv_lower"] == [165, 60, 60]
    assert nodes["find"]["spec"]["roi"] == {"left": 5, "top": 6, "width": 10, "height": 10}
    assert nodes["appear"]["spec"]["image"] == "asset:target"
    assert nodes["appear"]["spec"]["enter_threshold"] == 0.95
    fields["HSV_UPPER"] = "180, 255, 255"
    with pytest.raises(ValidationError, match=r"\(find\).*hsv_upper"):
        compile_project(project)
    del project["image_assets"]["asset:target"]
    with pytest.raises(ValidationError, match=r"\(appear\)"):
        compile_project(project)


def test_runner_requires_explicit_consent_before_materializing_assets(tmp_path):
    code = program([command(spec={"detector": "template", "image": "asset:target"})])
    spec = next(vision_nodes(code))["spec"]
    config = {"image_assets": {"asset:target": str(tmp_path / "image-0.png")}}
    assert open_perception(code, config) is None
    assert spec["image"] == "asset:target"
    with pytest.raises(ValueError, match="vision_find"):
        open_perception(code, {**config, "vision_capture": True})


def test_live_without_perception_fails_before_accessing_desktop():
    with pytest.raises(ValueError, match="screen capture enabled"):
        run_live(program([command()]), None, 1, MS)
