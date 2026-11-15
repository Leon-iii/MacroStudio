from io import BytesIO
from multiprocessing import get_context
from os import environ, pathsep
from pathlib import Path
from queue import Empty, Full
import subprocess
from threading import Event, Thread
from time import perf_counter_ns
from unicodedata import normalize

from .ir import SCREEN_SENSOR_OPS

SAMPLE_SECONDS = 0.2
STALE_NS = 1_000_000_000
MAX_SENSORS = 16


def sensor_specs(program):
    specs = {}

    def visit(value):
        if isinstance(value, dict):
            if value.get("op") in SCREEN_SENSOR_OPS:
                identifier = value["block_id"]
                if identifier in specs and specs[identifier] != value:
                    raise ValueError(f"conflicting screen sensor ID: {identifier}")
                specs[identifier] = value
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(program["workflows"])
    visit(program["handlers"])
    visit(program.get("state_machine"))
    if len(specs) > MAX_SENSORS:
        raise ValueError(f"at most {MAX_SENSORS} screen sensors are supported")
    return list(specs.values())


def contains_image(frame, template):
    fw, fh = frame.size
    tw, th = template.size
    if tw > fw or th > fh:
        return False
    data = frame.tobytes()
    pixels = template.tobytes()
    stride, width = fw * 3, tw * 3
    rows = [pixels[i:i + width] for i in range(0, len(pixels), width)]
    for y in range(fh - th + 1):
        base = y * stride
        at = data.find(rows[0], base, base + stride)
        while at != -1:
            if (at - base) % 3 == 0 and all(
                data.startswith(row, at + offset * stride)
                for offset, row in enumerate(rows[1:], 1)
            ):
                return True
            at = data.find(rows[0], at + 1, base + stride)
    return False


def read_text(frame, language):
    executable = next((directory / "tesseract.exe"
                       for directory in map(Path, environ.get("PATH", "").split(pathsep))
                       if directory.is_absolute() and (directory / "tesseract.exe").is_file()), None)
    if executable is None:
        raise ValueError("Tesseract OCR을 설치하고 PATH에 추가하세요.")
    encoded = BytesIO()
    frame.save(encoded, format="PNG")
    try:
        result = subprocess.run([executable, "stdin", "stdout", "-l", language, "--psm", "11"],
                                input=encoded.getvalue(), capture_output=True, timeout=0.8,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired as error:
        raise ValueError("OCR 인식 시간이 초과됐습니다.") from error
    if result.returncode:
        raise ValueError(f"OCR 실행 실패. {language} 언어 데이터와 설치를 확인하세요.")
    return result.stdout.decode("utf-8", errors="replace")


def normalize_text(value):
    return " ".join(normalize("NFC", value).casefold().split())


def sample(spec, grab, template=None):
    x, y = spec["x"], spec["y"]
    w, h = spec.get("width", 1), spec.get("height", 1)
    frame = grab(bbox=(x, y, x + w, y + h), all_screens=True).convert("RGB")
    if spec["op"] == "pixel_color":
        return bytes(frame.getpixel((0, 0))) == bytes.fromhex(spec["color"][1:])
    if spec["op"] == "region_text":
        return normalize_text(spec["text"]) in normalize_text(read_text(frame, spec["language"]))
    return contains_image(frame, template)


def worker(specs, base, output, stopped):
    from PIL import Image, ImageGrab

    templates = {}
    disabled = set()
    for spec in specs:
        if spec["op"] != "region_image":
            continue
        key = spec["block_id"]
        try:
            with Image.open(Path(base) / spec["image"], formats=("PNG", "BMP", "JPEG")) as image:
                if image.width > spec["width"] or image.height > spec["height"]:
                    raise ValueError("template is larger than the search region")
                templates[key] = image.convert("RGB")
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            disabled.add(key)
            try:
                output.put_nowait(("error", key, str(error)))
            except Full:
                pass
    reported = set()
    while not stopped.is_set():
        started = perf_counter_ns()
        for spec in specs:
            if stopped.is_set():
                return
            key = spec["block_id"]
            if key in disabled:
                continue
            captured_at = perf_counter_ns()
            try:
                result = sample(spec, ImageGrab.grab, templates.get(key))
                output.put_nowait(("value", key, (captured_at, result)))
                reported.discard(key)
            except Full:
                continue
            except (OSError, ValueError) as error:
                try:
                    output.put_nowait(("value", key, (captured_at, False)))
                    if key not in reported:
                        output.put_nowait(("error", key, str(error)))
                        reported.add(key)
                except Full:
                    pass
        stopped.wait(max(0.02, SAMPLE_SECONDS - (perf_counter_ns() - started) / 1e9))


class ScreenSensors:
    def __init__(self, specs, base, report, *, sampler=worker):
        context = get_context("spawn")
        self.output = context.Queue(maxsize=MAX_SENSORS * 2)
        self.stopped = context.Event()
        self.done = Event()
        self.values = {}
        self.report = report
        self.process = context.Process(target=sampler, args=(specs, base, self.output, self.stopped),
                                       daemon=True)
        self.reader = Thread(target=self._collect, daemon=True)

    def _collect(self):
        while not self.done.is_set():
            try:
                kind, key, value = self.output.get(timeout=0.1)
            except Empty:
                if not self.process.is_alive() and not self.stopped.is_set():
                    self.values.clear()
                    self.report("", "화면 센서 프로세스가 종료됐습니다.")
                    return
                continue
            if kind == "value":
                self.values[key] = value
            else:
                self.report(key, value)

    def read(self, key):
        timestamp, result = self.values.get(key, (0, False))
        return result and perf_counter_ns() - timestamp <= STALE_NS

    def __enter__(self):
        self.process.start()
        self.reader.start()
        return self

    def __exit__(self, *exc):
        self.stopped.set()
        if self.process.pid is not None:
            self.process.join(0.25)
            if self.process.is_alive():
                self.process.terminate()
                self.process.join(0.5)
        self.done.set()
        if self.reader.ident is not None:
            self.reader.join(0.2)
        self.output.close()
