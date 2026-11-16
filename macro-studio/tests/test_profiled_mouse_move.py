from dataclasses import replace
import json
from pathlib import Path

import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError, validate_program
from macro_studio.mouse_motion import BUILTIN_PROFILES
from macro_studio.mouse_motion.registry import MotionProfileRegistry, capture_motion_profiles, set_project_motion_profile
from macro_studio.output import BackendCapabilities, HidMode, OutputKind, OutputProfile, OutputRoute, OutputRouter, RunConfig
from macro_studio.runtime import Runtime
from macro_studio.win32 import normalize_absolute


def move(block_id, x, y, *, kind="mouse_move_timed", profile="inherit", duration=None):
    inputs = {"POSITION": {"block": {"type": "ms_position", "fields": {"X": x, "Y": y}}}}
    if duration is not None:
        inputs["DURATION"] = {"block": {"type": "ms_duration", "fields": {"UNIT": "ms"},
                                       "inputs": {"VALUE": {"shadow": {"type": "ms_number",
                                                                      "fields": {"VALUE": duration}}}}}}
    return {"type": "ms_" + kind, "id": block_id, "fields": {"PROFILE": profile}, "inputs": inputs}


def project(*steps):
    block = {"type": "ms_start", "id": "start"}
    root = block
    for step in steps:
        block["next"] = {"block": step}
        block = step
    return {"project_id": "profiled", "workspace": {"blocks": {"blocks": [root]}}}


def test_dry_run_resolves_project_profile_then_block_override_and_relative_origin():
    default = replace(BUILTIN_PROFILES["builtin.precision"], duration_ms=60)
    app_profile = BUILTIN_PROFILES["builtin.smooth"]
    data = project(move("absolute", -12, 46), move("relative", 22, -11,
                   kind="mouse_move_relative_timed", profile="builtin.direct", duration=80))
    user = MotionProfileRegistry.draft(default, name="Saved")
    set_project_motion_profile(data, user)
    code, detached = compile_project(data, application_profile=app_profile)
    assert code["ir_version"] == 2 and not detached
    steps = code["workflows"]["@start"]["steps"]
    assert steps[0]["motion"]["profile"] == user.to_dict()
    assert steps[1]["motion"]["profile"]["id"] == "builtin.direct"
    with pytest.raises(ValidationError, match="absolute.*IR version 2"):
        validate_program({**code, "ir_version": 1})

    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    backend.cursor = (-40, 30)
    runtime = Runtime(code, clock, backend)
    runtime.run(300_000_000, [])
    assert not runtime.errors
    at = {event["time_ns"]: (event["x"], event["y"]) for event in backend.events}
    assert at[60_000_000] == (-12, 46)
    assert at[140_000_000] == (10, 35)
    assert len(at) > 2


def test_stop_leaves_pointer_at_an_intermediate_sample_and_skips_remaining_actions():
    data = project(move("moving", 120, 90, profile="builtin.direct", duration=150),
                   move("next", 0, 0, duration=25))
    code, _ = compile_project(data)
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(400_000_000, [], poll=lambda: ("stop" if clock.now_ns >= 60_000_000 else None, []))
    assert runtime.termination == "stopped" and not runtime.errors
    assert (0, 0) != backend.cursor != (120, 90)
    assert max(event["time_ns"] for event in backend.events) < 60_000_000


def test_missing_profile_rejected_with_block_id_and_explicit_user_snapshot_survives_reload():
    user = MotionProfileRegistry.draft()
    data = project(move("missing-profile", 14, 25, profile=user.id))
    with pytest.raises(ValidationError, match="missing-profile"):
        compile_project(data)
    capture_motion_profiles(data, {user.id: user})
    code, _ = compile_project(data)
    assert code["workflows"]["@start"]["steps"][0]["motion"]["profile"] == user.to_dict()
    router = OutputRouter({OutputKind.EXTERNAL_HID: OutputRoute(BackendCapabilities(mouse_absolute=True),
                                                              lambda: pytest.fail("opened HID backend"))})
    with pytest.raises(ValueError, match="missing-profile.*profiled_mouse"):
        router.bind(code, RunConfig(OutputProfile("test", OutputKind.EXTERNAL_HID, HidMode.STREAMING),
                                    dry_run=False, armed=True))


def test_legacy_move_stays_in_version_one_and_keeps_linear_timing():
    code, _ = compile_project(project(move("legacy", 80, 40, kind="mouse_move", duration=32)))
    assert code["ir_version"] == 1
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    Runtime(code, clock, backend).run(100_000_000, [])
    assert [(event["time_ns"], event["x"], event["y"]) for event in backend.events] == [
        (16_000_000, 40, 20), (32_000_000, 80, 40),
    ]


def test_sendinput_virtual_desktop_pixels_cover_negative_origin_and_last_pixel():
    desktop = (-1920, -240, 4480, 1680)
    for x, y in ((-1920, -240), (-1, -1), (0, 0), (2559, 1439)):
        dx, dy = normalize_absolute((x, y), desktop)
        assert ((dx * desktop[2]) // 65536 + desktop[0],
                (dy * desktop[3]) // 65536 + desktop[1]) == (x, y)
    with pytest.raises(OSError, match="outside"):
        normalize_absolute((2560, 1439), desktop)


@pytest.mark.parametrize("policy", ["parallel", "override"])
def test_pointer_contention_and_override_restart_at_the_observed_position(policy):
    data = project(move("original", 160, 0, profile="builtin.direct", duration=100))
    hotkey = {"type": "ms_hotkey", "id": "takeover",
              "fields": {"KEY": "CTRL+F7", "POLICY": policy, "PRIORITY": 10},
              "next": {"block": move("relative", 0, 80, kind="mouse_move_relative_timed",
                                     profile="builtin.direct", duration=80)}}
    data["workspace"]["blocks"]["blocks"].append(hotkey)
    code, _ = compile_project(data)
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(250_000_000, [(50_000_000, "CTRL+F7")])
    if policy == "parallel":
        assert runtime.errors[0]["code"] == "input_conflict"
        assert runtime.errors[0]["block_id"] == "relative"
        assert backend.cursor == (160, 0)
    else:
        assert not runtime.errors
        before = [event for event in backend.events if event["time_ns"] < 50_000_000]
        after = [event for event in backend.events if event["time_ns"] >= 50_000_000]
        assert before and after and 0 < before[-1]["x"] < 160
        assert all(event["x"] == before[-1]["x"] for event in after)
        assert backend.cursor == (before[-1]["x"], 80)


def test_active_run_uses_compiled_profile_snapshot_during_project_edit():
    original = MotionProfileRegistry.draft(replace(BUILTIN_PROFILES["builtin.direct"], duration_ms=120))
    changed = replace(original, duration_ms=30, revision=2)
    data = project(move("snapshot", 160, 20))
    set_project_motion_profile(data, original)
    code, _ = compile_project(data)
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    edited = False

    def change_profile():
        nonlocal edited
        if not edited and clock.now_ns >= 30_000_000:
            set_project_motion_profile(data, changed)
            edited = True
        return None, []

    Runtime(code, clock, backend).run(250_000_000, [], poll=change_profile)
    assert edited and backend.events[-1]["time_ns"] == 120_000_000
    new_code, _ = compile_project(data)
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    Runtime(new_code, clock, backend).run(250_000_000, [])
    assert backend.events[-1]["time_ns"] == 30_000_000


def test_showcase_runs_from_disk_with_project_default_and_block_override():
    path = Path(__file__).resolve().parents[1] / "examples" / "mouse_motion_showcase.macroproj"
    code, _ = compile_project(json.loads(path.read_text(encoding="utf-8")))
    steps = code["workflows"]["@example-start"]["steps"]
    assert [step["motion"]["profile"]["id"] for step in steps] == ["builtin.precision", "builtin.direct"]
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(code, clock, backend)
    runtime.run(1_000_000_000, [])
    assert not runtime.errors and backend.cursor == (360, 140)
