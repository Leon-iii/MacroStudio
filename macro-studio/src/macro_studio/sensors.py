from array import array
from collections import OrderedDict
from io import BytesIO
from multiprocessing import get_context
from os import environ, pathsep
from pathlib import Path
from queue import Empty, Full
import subprocess
from threading import Event, Thread
from time import perf_counter_ns
from unicodedata import normalize

from .ir import SCREEN_SENSOR_OPS, sensor_geometry, sensor_has_dynamic_geometry

SAMPLE_NS = 200_000_000
STALE_NS = 1_000_000_000
MAX_SENSORS = 16
MAX_DYNAMIC_SAMPLES = MAX_SENSORS * 4


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


def summed_rows(width, height, rows, *, signed=False):
    stride = width + 1
    totals = array("i" if signed else "I", [0]) * (stride * (height + 1))
    for y, values in enumerate(rows):
        row = (y + 1) * stride + 1
        running = 0
        for index, value in enumerate(values, start=row):
            running += value
            totals[index] = running + totals[index - stride]
    return totals


def channel_rows(channel):
    width = channel.width
    data = channel.tobytes()
    return (data[offset:offset + width] for offset in range(0, len(data), width))


def alternating_rows(image, x_weight, y_weight):
    for y, row in enumerate(zip(*(channel_rows(channel) for channel in image.split()))):
        yield (-(r + g + b) if (x * x_weight + y * y_weight) % 2 else r + g + b
               for x, (r, g, b) in enumerate(zip(*row)))


def contains_similar_image(frame, template, threshold):
    from PIL import ImageChops, ImageStat

    fw, fh = frame.size
    tw, th = template.size
    if tw > fw or th > fh:
        return False
    if threshold == 0:
        return True

    max_error = tw * th * 3 * 255
    use_histogram = tw * th * 3 > 4096

    def matches(x, y):
        patch = frame.crop((x, y, x + tw, y + th))
        difference = ImageChops.difference(patch, template)
        error = sum(ImageStat.Stat(difference).sum) if use_histogram else sum(difference.tobytes())
        return (max_error - error) / max_error >= threshold

    if matches(0, 0):
        return True
    if (fw, fh) == (tw, th):
        return False

    # If the frame lacks a template color, its distance to the available range is unavoidable.
    histogram = template.histogram()
    range_error = sum(
        histogram[channel * 256 + value] * max(low - value, value - high, 0)
        for channel, (low, high) in enumerate(frame.getextrema())
        for value in range(256)
    )
    if (max_error - range_error) / max_error < threshold:
        return False

    stride = fw + 1
    channels = tuple(summed_rows(fw, fh, channel_rows(channel)) for channel in frame.split())
    # Alternating signed sums can also reject stripes or checker patterns with identical RGB sums.
    signatures = (
        (sum(map(sum, alternating_rows(template, x_weight, y_weight))), x_weight, y_weight)
        for x_weight, y_weight in ((1, 0), (0, 1), (1, 1))
    )
    signature, x_weight, y_weight = max(signatures, key=lambda pattern: abs(pattern[0]))
    alternating = (summed_rows(fw, fh, alternating_rows(frame, x_weight, y_weight), signed=True)
                   if (max_error - abs(signature)) / max_error < threshold else None)

    def region(x0, y0, x1, y1):
        expected = ImageStat.Stat(template.crop((x0, y0, x1, y1))).sum
        return (y0 * stride + x0, y0 * stride + x1, y1 * stride + x0, y1 * stride + x1, expected)

    whole = (region(0, 0, tw, th),)
    columns, rows = min(4, tw), min(4, th)
    tiles = [region(x * tw // columns, y * th // rows, (x + 1) * tw // columns, (y + 1) * th // rows)
             for y in range(rows) for x in range(columns)]

    def could_match(origin, regions):
        # The difference between RGB sums is a lower bound on the sum of pixel differences.
        error = 0
        for tl, tr, bl, br, expected in regions:
            for totals, target in zip(channels, expected):
                total = totals[origin + br] - totals[origin + bl] - totals[origin + tr] + totals[origin + tl]
                error += abs(total - target)
            if (max_error - error) / max_error < threshold:
                return False
        return True

    for y in range(fh - th + 1):
        for x in range(fw - tw + 1):
            origin = y * stride + x
            if not origin or not could_match(origin, whole):
                continue
            if alternating is not None:
                bottom = origin + th * stride
                signed_sum = alternating[bottom + tw] - alternating[bottom] - alternating[origin + tw] + alternating[origin]
                if (x * x_weight + y * y_weight) % 2:
                    signed_sum = -signed_sum
                if (max_error - abs(signed_sum - signature)) / max_error < threshold:
                    continue
            if could_match(origin, tiles) and matches(x, y):
                return True
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
    threshold = spec.get("min_similarity", 1.0)
    if threshold == 1.0:
        return contains_image(frame, template)
    return contains_similar_image(frame, template, threshold)


def worker(specs, base, requests, output, stopped):
    from PIL import Image, ImageGrab

    templates = {}
    disabled = set()
    for spec in specs:
        if spec["op"] != "region_image":
            continue
        key = spec["block_id"]
        try:
            with Image.open(Path(base) / spec["image"], formats=("PNG", "BMP", "JPEG")) as image:
                if (image.width > (spec["width"] if type(spec["width"]) is int else 1024) or
                        image.height > (spec["height"] if type(spec["height"]) is int else 1024)):
                    raise ValueError("template is larger than the search region")
                templates[key] = image.convert("RGB")
        except (OSError, ValueError, Image.DecompressionBombError) as error:
            disabled.add(key)
            try:
                output.put_nowait(("error", key, str(error)))
            except Full:
                pass
    static = [(spec["block_id"], spec) for spec in specs if not sensor_has_dynamic_geometry(spec)]
    dynamic = {spec["block_id"]: spec for spec in specs if sensor_has_dynamic_geometry(spec)}
    reported = set()
    while not stopped.is_set():
        started = perf_counter_ns()
        batch = dict(static)
        for _ in range(MAX_DYNAMIC_SAMPLES):
            try:
                key, bounds = requests.get_nowait()
            except Empty:
                break
            if key in dynamic:
                spec = dynamic[key]
                batch[key, bounds] = {**spec, **dict(zip(sensor_geometry(spec), bounds))}
        for cache_key, spec in batch.items():
            if stopped.is_set():
                return
            key = spec["block_id"]
            if key in disabled:
                continue
            captured_at = perf_counter_ns()
            try:
                result = sample(spec, ImageGrab.grab, templates.get(key))
                output.put_nowait(("value", cache_key, (captured_at, result)))
                reported.discard(key)
            except Full:
                continue
            except (OSError, ValueError) as error:
                try:
                    output.put_nowait(("value", cache_key, (captured_at, False)))
                    if key not in reported:
                        output.put_nowait(("error", key, str(error)))
                        reported.add(key)
                except Full:
                    pass
        stopped.wait(max(0.02, (SAMPLE_NS - (perf_counter_ns() - started)) / 1e9))


class ScreenSensors:
    def __init__(self, specs, base, report, *, sampler=worker):
        context = get_context("spawn")
        self.requests = context.Queue(maxsize=MAX_SENSORS)
        self.output = context.Queue(maxsize=MAX_SENSORS * 2)
        self.stopped = context.Event()
        self.done = Event()
        self.values = {}
        self.dynamic_values = OrderedDict()
        self.requested = OrderedDict()
        self.report = report
        self.process = context.Process(target=sampler, args=(specs, base, self.requests, self.output, self.stopped),
                                       daemon=True)
        self.reader = Thread(target=self._collect, daemon=True)

    def _collect(self):
        while not self.done.is_set():
            try:
                kind, key, value = self.output.get(timeout=0.1)
            except Empty:
                if not self.process.is_alive() and not self.stopped.is_set():
                    self.values.clear()
                    self.dynamic_values.clear()
                    self.report("", "화면 센서 프로세스가 종료됐습니다.")
                    return
                continue
            if kind == "value":
                if isinstance(key, tuple):
                    self.dynamic_values[key] = value
                    self.dynamic_values.move_to_end(key)
                    if len(self.dynamic_values) > MAX_DYNAMIC_SAMPLES:
                        self.dynamic_values.popitem(last=False)
                else:
                    self.values[key] = value
            else:
                self.report(key, value)

    def read(self, key):
        timestamp, result = self.values.get(key, (0, False))
        return result and perf_counter_ns() - timestamp <= STALE_NS

    def read_at(self, key, bounds):
        cache_key = (key, bounds)
        now = perf_counter_ns()
        timestamp, result = self.dynamic_values.get(cache_key, (0, False))
        if now - max(timestamp, self.requested.get(cache_key, 0)) >= SAMPLE_NS:
            try:
                self.requests.put_nowait(cache_key)
            except Full:
                pass
            else:
                self.requested[cache_key] = now
                self.requested.move_to_end(cache_key)
                if len(self.requested) > MAX_DYNAMIC_SAMPLES:
                    self.requested.popitem(last=False)
        return result and now - timestamp <= STALE_NS

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
        self.requests.cancel_join_thread()
        self.requests.close()
        self.output.close()
