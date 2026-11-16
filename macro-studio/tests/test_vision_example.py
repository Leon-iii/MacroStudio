from base64 import b64decode
from functools import partial
import json
from pathlib import Path

import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from macro_studio.backend import FakeInputBackend
from macro_studio.compiler import compile_project
from macro_studio.live import DeadlineClock
from macro_studio.perception import FakeCaptureBackend, PerceptionService
from macro_studio.runtime import Runtime


def test_packaged_example_finds_target_moves_and_clicks(tmp_path):
    example = Path(__file__).resolve().parents[1] / "examples" / "vision-find-click.macroproj"
    project = json.loads(example.read_text(encoding="utf-8"))
    program, _ = compile_project(project)
    handler, = program["handlers"]
    steps = program["workflows"][handler["workflow_id"]]["steps"]
    spec = steps[0]["spec"]
    asset = b64decode(project["image_assets"][spec["image"]]["png"])
    template_file = tmp_path / "target.png"
    template_file.write_bytes(asset)
    spec["image"] = template_file.name
    steps[0]["timeout_ms"] = 4_000
    spec["max_age_ms"] = 4_000

    template = cv2.imdecode(np.frombuffer(asset, np.uint8), cv2.IMREAD_COLOR)
    height, width = template.shape[:2]
    screen = np.zeros((170, 260, 3), dtype=np.uint8)
    screen[80:80 + height, 128:128 + width] = template
    source = partial(FakeCaptureBackend, (screen,), origin=(-50, 20))
    clock = DeadlineClock()
    output = FakeInputBackend(clock)
    with PerceptionService(tmp_path, backend_factory=source) as perception:
        engine = Runtime(program, clock, output, perception=perception)
        engine.run(5_000_000_000, [(0, handler["event"]["hotkey"])])

    assert engine.errors == []
    moves = [event for event in output.events if event["action"] == "mouse_move"]
    assert len(moves) > 1
    assert (moves[-1]["x"], moves[-1]["y"]) == (102, 114)
    assert [event["action"] for event in output.events[-2:]] == ["mouse_down", "mouse_up"]
