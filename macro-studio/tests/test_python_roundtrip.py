from copy import deepcopy
import json
from pathlib import Path

import pytest

from macro_studio.compiler import compile_project
from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.perception import RecognitionResult, RecognitionStatus
from macro_studio.python_compiler import compile_python, dry_run_python
from macro_studio.python_roundtrip import ConversionError, generate_python, render_blocks, semantic_ir
from macro_studio.runtime import Runtime


PROJECT = {"project_id": "roundtrip", "globals": {"total": 0}}
EXAMPLE = Path(__file__).resolve().parents[1] / "examples/vision-find-click.macroproj"


def from_python(source, project=PROJECT):
    return compile_python(source, project).program_for(source, project)


@pytest.mark.parametrize("status", [RecognitionStatus.FOUND, RecognitionStatus.NOT_FOUND])
def test_existing_block_project_preserves_vision_motion_and_diagnostic_ids(status):
    project = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    original, _ = compile_project(project)
    artifact = generate_python(original, project)
    assert artifact == generate_python(original, project)
    snapshot = artifact.compile(project)
    rebuilt = snapshot.program_for(artifact.source, project)
    workspace = render_blocks(rebuilt, project)
    final, detached = compile_project({**project, "workspace": workspace})
    assert detached == []
    assert semantic_ir(original) == semantic_ir(rebuilt) == semantic_ir(final)
    assert final["handlers"][0]["id"] == rebuilt["handlers"][0]["id"] == "vision-hotkey"
    steps = final["workflows"][final["handlers"][0]["workflow_id"]]["steps"]
    assert steps[0]["block_id"] == "vision-find"
    assert dict(snapshot.source_map)["vision-find"].line > 1
    assert steps[1]["then"][0]["motion"]["profile"]["id"] == "builtin.natural"

    def run(program):
        clock = VirtualClock()
        output = FakeInputBackend(clock)

        class Replay:
            def request(self, spec):
                assert spec["template_id"] == "asset:vision-demo-target"
                return 1

            def take(self, _request_id):
                found = status == RecognitionStatus.FOUND
                return RecognitionResult(status, "template", "asset:vision-demo-target", clock.now_ns, "fake",
                                         clock.now_ns, clock.now_ns, bbox=(99, 99, 2, 2) if found else None,
                                         center_x=100 if found else None, center_y=100 if found else None)

        runtime = Runtime(program, clock, output, perception=Replay())
        runtime.run(2_000_000_000, [(0, "CTRL+F6")])
        return runtime.errors, output.cursor, [event for event in output.events if event["action"] != "mouse_move"]

    base = run(original)
    assert base == run(rebuilt) == run(final)
    assert base[0] == []
    if status == RecognitionStatus.FOUND:
        assert base[1] == (100, 100)
        assert [event["action"] for event in base[2]] == ["mouse_down", "mouse_up"]
    else:
        assert base[2] == []


def test_python_blocks_python_roundtrip_keeps_locals_params_globals_and_fake_output():
    source = """
@on_hotkey("Ctrl+F6", priority=9, reentrancy="restart")
async def run():
    await workflow.call("tap series", 3)

@workflow("tap series")
async def tap_series(n: int):
    global total
    x = 0
    for i in range(n):
        x += i
        await macro.sleep(1)
    total = x
    if total == 3:
        await keyboard.repeat("A", 200, 2, 2)
    else:
        await keyboard.tap("B", 2)
"""
    before = from_python(source)
    workspace = render_blocks(before, PROJECT)
    blocks, _ = compile_project({**PROJECT, "workspace": workspace})
    generated = generate_python(blocks, PROJECT)
    after = generated.compile(PROJECT).program_for(generated.source, PROJECT)
    assert semantic_ir(before) == semantic_ir(blocks) == semantic_ir(after)
    first = dry_run_python(compile_python(source, PROJECT), source, PROJECT, duration_ns=100_000_000,
                           hotkeys=[(2_000_000, "CTRL+F6")])
    last = dry_run_python(generated.compile(PROJECT), generated.source, PROJECT, duration_ns=100_000_000,
                          hotkeys=[(2_000_000, "CTRL+F6")])
    assert first[0].globals == last[0].globals == {"total": 3}
    assert first[0].errors == last[0].errors == []
    assert first[1].events == last[1].events
    assert [event["action"] for event in last[1].events] == ["key_down", "key_up"] * 2


def test_conversion_refuses_unsupported_steps_with_original_block_id():
    source = "@on_start\nasync def start(): await macro.sleep(10)"
    program = from_python(source)
    step = program["workflows"]["start"]["steps"][0]
    step.update(op="emit_event", name="manual")
    saved = deepcopy(program)
    for convert in (generate_python, render_blocks):
        with pytest.raises(ConversionError) as raised:
            convert(program, PROJECT)
        assert raised.value.block_id == step["block_id"]
        assert program == saved


def test_conversion_refuses_changed_motion_snapshot():
    source = "@on_start\nasync def move(): await mouse.move_to(2, 3)"
    original = from_python(source)
    changed = {**PROJECT, "settings": {"default_mouse_motion_profile_id": "builtin.direct"}}
    for convert in (generate_python, render_blocks):
        with pytest.raises(ConversionError, match="실행 의미"):
            convert(original, changed)


def test_equivalence_distinguishes_boolean_and_numeric_state():
    source = "@on_start\nasync def idle(): pass"
    original = from_python(source)
    changed = {**PROJECT, "globals": {"total": False}}
    for convert in (generate_python, render_blocks):
        with pytest.raises(ConversionError, match="실행 의미"):
            convert(original, changed)


def test_profile_and_roi_overrides_preserve_semantics():
    project = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    source = """
@on_start
async def detect():
    target = await vision.wait_for(
        "asset:vision-demo-target", "disappear", timeout_ms=500,
        profile_id="builtin.flexible", enter_threshold=0.8, exit_threshold=0.7,
        roi_left=10, roi_top=20, roi_width=200, roi_height=100, max_age_ms=300,
    )
"""
    original = from_python(source, project)
    for convert in (generate_python, render_blocks):
        assert convert(original, project)
