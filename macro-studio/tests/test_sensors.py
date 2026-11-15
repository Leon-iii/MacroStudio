from threading import Event
from time import perf_counter_ns

import pytest

from macro_studio.backend import FakeInputBackend, VirtualClock
from macro_studio.compiler import compile_project
from macro_studio.ir import ValidationError, evaluate, validate_program
from macro_studio.runtime import Runtime
from macro_studio.sensors import ScreenSensors, sample, sensor_specs, worker

PIXEL = {"op": "pixel_color", "block_id": "pixel", "x": -2, "y": 3, "color": "#ff1133"}


def stalled_sampler(specs, base, requests, output, stopped):
    output.put(("error", "", "sampling started"))
    stopped.wait(10)


def geometry_sampler(specs, base, requests, output, stopped):
    from PIL import Image, ImageGrab
    from macro_studio import sensors

    def grab(bbox, all_screens):
        assert all_screens
        left, top, right, bottom = bbox
        colour = (255, 0, 0) if bbox == (5, -3, 21, 5) else (0, 0, 0)
        return Image.new("RGB", (right - left, bottom - top), colour)

    ImageGrab.grab = grab
    sensors.read_text = lambda frame, language: "READY" if frame.getpixel((0, 0)) == (255, 0, 0) else ""
    worker(specs, base, requests, output, stopped)


def test_dynamic_region_evaluates_coordinates_and_uses_only_matching_samples(tmp_path):
    pytest.importorskip("PIL.Image")
    spec = {"op": "region_text", "block_id": "ocr", "x": {"var": "left"}, "y": -3,
            "width": {"op": "mul", "args": [{"var": "scale"}, 8]}, "height": 8,
            "text": "READY", "language": "eng"}
    errors = []
    with ScreenSensors([spec], tmp_path, lambda *args: errors.append(args), sampler=geometry_sampler) as sensors:
        variables = {"left": 5, "scale": 2}
        assert not evaluate(spec, variables, sensors_at=sensors.read_at)
        done = Event()
        deadline = perf_counter_ns() + 5_000_000_000
        while not evaluate(spec, variables, sensors_at=sensors.read_at):
            assert perf_counter_ns() < deadline, errors
            done.wait(0.02)
        assert not evaluate(spec, {**variables, "left": 6}, sensors_at=sensors.read_at)
        assert not evaluate(spec, {**variables, "scale": 1}, sensors_at=sensors.read_at)
        assert not evaluate(spec, {**variables, "scale": 0}, sensors_at=sensors.read_at)
        assert evaluate(spec, variables, sensors_at=sensors.read_at)
    assert not errors


def test_screen_colors_and_image_positions():
    Image = pytest.importorskip("PIL.Image")
    template = Image.new("RGB", (2, 2), (255, 17, 51))
    frame = Image.new("RGB", (5, 4))
    frame.paste(template, (3, 2))
    expected = (-2, 3, -1, 4)

    def grab_pixel(bbox, all_screens):
        assert bbox == expected and all_screens
        return template.crop((0, 0, 1, 1))

    assert sample(PIXEL, grab_pixel)
    spec = dict(PIXEL, op="region_image", width=5, height=4)
    assert sample(spec, lambda **kwargs: frame, template)
    frame.putpixel((4, 3), (0, 0, 0))
    assert not sample(spec, lambda **kwargs: frame, template)


def test_blockly_pixel_guard_compiles_to_sensor_reference():
    workspace = {"blocks": {"blocks": [{
        "type": "ms_start", "id": "startup", "inputs": {"GUARD": {"block": {
            "type": "ms_pixel_color", "id": "pixel", "fields": {"X": -2, "Y": 3, "COLOR": "#ff1133"},
        }}},
    }]}}
    program, _ = compile_project({"project_id": "sensors", "workspace": workspace})
    assert sensor_specs(program) == [PIXEL]
    assert evaluate(program["handlers"][0]["guard"], {}, {"pixel": True}.get)


def test_image_sensor_requires_referenced_project_asset():
    asset_id = "asset:reference"
    region = {"type": "ms_region_image", "id": "image-check",
              "fields": {"X": 0, "Y": 0, "WIDTH": 20, "HEIGHT": 10, "IMAGE": asset_id}}
    start = {"type": "ms_start", "id": "start", "inputs": {"GUARD": {"block": region}}}
    project = {"project_id": "images", "workspace": {"blocks": {"blocks": [start]}}}
    with pytest.raises(ValidationError, match=r"\(image-check\)"):
        compile_project(project)
    program, _ = compile_project({**project, "image_assets": {asset_id: {"name": "버튼"}}})
    assert sensor_specs(program)[0]["image"] == asset_id


def test_stalled_sampler_cannot_stall_live_deadlines(tmp_path):
    from macro_studio.live import DeadlineClock

    program = validate_program({
        "ir_version": 1, "project_id": "sensor-deadlines",
        "handlers": [{"id": "start", "event": {"type": "project_start"}, "workflow_id": "work"}],
        "workflows": {"work": {"steps": [
            {"op": "if", "condition": PIXEL, "then": [{"op": "end"}]},
            {"op": "tap_sequence", "frequency_hz": 100, "hold_ns": 4_000_000, "count": 4, "key": "A"},
        ]}},
    })
    started = Event()
    with ScreenSensors(sensor_specs(program), tmp_path, lambda *_: started.set(), sampler=stalled_sampler) as sensors:
        assert started.wait(5)
        clock = DeadlineClock()
        backend = FakeInputBackend(clock)
        runtime = Runtime(program, clock, backend)
        runtime.read_sensor = sensors.read
        start = perf_counter_ns()
        runtime.run(350_000_000, [], poll=lambda: (None, []))
        assert perf_counter_ns() - start < 1_000_000_000
        assert sensors.process.is_alive()
        assert [row["action"] for row in backend.events] == ["key_down", "key_up"] * 4
