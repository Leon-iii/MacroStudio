from pathlib import Path
from shutil import which

import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.runtime import Runtime
from macro_studio.sensors import sample, sensor_specs


def test_ocr_result_controls_workflow():
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    ImageFont = pytest.importorskip("PIL.ImageFont")
    if not which("tesseract.exe"):
        pytest.skip("Tesseract OCR is not installed")
    font_path = Path("C:/Windows/Fonts/arial.ttf")
    if not font_path.exists():
        pytest.skip("Windows Arial test font unavailable")

    text_block = {"id": "ocr", "type": "ms_region_text", "fields": {
        "X": 2, "Y": 3, "WIDTH": 420, "HEIGHT": 130, "TEXT": "  system   READY ", "LANGUAGE": "eng",
    }}
    tap = {"id": "action", "type": "ms_key_tap", "fields": {"KEY": "A", "MS": 20}}
    start = {"id": "start", "type": "ms_start", "inputs": {
        "GUARD": {"block": text_block}, "BODY": {"block": tap},
    }}
    program, detached = compile_project({"project_id": "ocr", "workspace": {"blocks": {"blocks": [start]}}})
    assert not detached
    spec, = sensor_specs(program)
    font = ImageFont.truetype(str(font_path), 42)
    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    runtime = Runtime(program, clock, backend)

    for expected, rendered in [(False, "SYSTEM BUSY"), (True, "SYSTEM READY")]:
        frame = Image.new("RGB", (420, 130), "white")
        ImageDraw.Draw(frame).text((12, 32), rendered, font=font, fill="black")

        def capture(bbox, all_screens):
            assert bbox == (2, 3, 422, 133) and all_screens
            return frame

        matched = sample(spec, capture)
        assert matched is expected
        runtime.read_sensor = {"ocr": matched}.get
        runtime.run(100_000_000, [])
        assert [event["action"] for event in backend.events] == (
            ["key_down", "key_up"] if expected else []
        )
