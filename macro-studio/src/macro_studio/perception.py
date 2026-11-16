from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from functools import lru_cache
from itertools import count
from multiprocessing import get_context
from pathlib import Path
from queue import Empty, Full
from time import perf_counter_ns
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from numpy import ndarray


NS_PER_MS = 1_000_000
MAX_REQUESTS = 32
MAX_SUBSCRIPTIONS = 32
MAX_OUTPUT = 2 * (MAX_REQUESTS + MAX_SUBSCRIPTIONS)
SOURCE_REFRESH_NS = 250 * NS_PER_MS


@dataclass(frozen=True)
class CaptureRegion:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def contains(self, region: CaptureRegion) -> bool:
        return (region.width > 0 and region.height > 0 and
                self.left <= region.left < region.right <= self.right and
                self.top <= region.top < region.bottom <= self.bottom)


@dataclass(frozen=True)
class CapturedFrame:
    frame_id: int
    image_bgr: ndarray
    origin_x: int
    origin_y: int
    captured_at_ns: int
    source_id: str
    width: int
    height: int
    source_region: CaptureRegion | None = None

    def __post_init__(self):
        import numpy as np

        if self.image_bgr.dtype != np.uint8 or self.image_bgr.shape != (self.height, self.width, 3):
            raise ValueError("a captured frame must contain a BGR uint8 image")
        pixels = np.frombuffer(self.image_bgr.tobytes(), dtype=np.uint8).reshape(self.image_bgr.shape)
        object.__setattr__(self, "image_bgr", pixels)

    @property
    def region(self) -> CaptureRegion:
        return CaptureRegion(self.origin_x, self.origin_y, self.width, self.height)

    @property
    def bgr(self) -> ndarray:
        return self.image_bgr


@dataclass(frozen=True)
class CaptureSourceInfo:
    source_id: str
    region: CaptureRegion
    device_id: str | None = None


class CaptureError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ScreenCaptureBackend(Protocol):
    def list_sources(self) -> list[CaptureSourceInfo]: ...
    def start(self, source_id: str, roi: CaptureRegion | None, target_fps: int) -> None: ...
    def read_latest(self) -> CapturedFrame | None: ...
    def stop(self) -> None: ...
    def close(self) -> None: ...


class MSSBackend:
    def __init__(self):
        self._mss = None
        self._source = None
        self._region = None
        self._sources = []
        self._sources_refreshed_ns = None
        self._ids = count(1)

    def list_sources(self) -> list[CaptureSourceInfo]:
        from mss import MSS

        now = perf_counter_ns()
        if self._sources_refreshed_ns is not None and now - self._sources_refreshed_ns < SOURCE_REFRESH_NS:
            return self._sources.copy()
        if self._mss is None:
            self._mss = MSS()
            monitors = self._mss.monitors
        else:
            # MSS caches enumeration for the lifetime of each instance.
            with MSS() as probe:
                monitors = probe.monitors
        self._sources = [
            CaptureSourceInfo(f"monitor.{index}" if index else "desktop", CaptureRegion(**{
                key: monitor[key] for key in ("left", "top", "width", "height")
            }), monitor.get("unique_id"))
            for index, monitor in enumerate(monitors)
            if monitor["width"] > 0 and monitor["height"] > 0
        ]
        self._sources_refreshed_ns = now
        return self._sources.copy()

    def start(self, source_id: str, roi: CaptureRegion | None, target_fps: int) -> None:
        source = _source_info(self.list_sources(), source_id)
        self._region = _screen_region(source.region, roi)
        self._source = source

    def read_latest(self) -> CapturedFrame:
        import cv2
        import numpy as np

        if self._source is None:
            raise CaptureError("CAPTURE_NOT_STARTED", "Select a capture source before reading a frame.")
        if _source_info(self.list_sources(), self._source.source_id) != self._source:
            raise CaptureError("SOURCE_CHANGED", "The selected monitor changed. Restart capture to select it again.")
        region = self._region
        screenshot = self._mss.grab(asdict(region))
        captured_at_ns = perf_counter_ns()
        bgr = cv2.cvtColor(np.asarray(screenshot), cv2.COLOR_BGRA2BGR)
        return CapturedFrame(next(self._ids), bgr, region.left, region.top, captured_at_ns,
                             self._source.source_id, region.width, region.height, self._source.region)

    def stop(self) -> None:
        self._source = None
        self._region = None

    def close(self) -> None:
        self.stop()
        if self._mss is not None:
            self._mss.close()
            self._mss = None
        self._sources = []
        self._sources_refreshed_ns = None


class FakeCaptureBackend:
    def __init__(self, frames: tuple[ndarray | None, ...], *, origin: tuple[int, int] = (0, 0),
                 source_id: str = "monitor.1"):
        shape = next((pixels.shape for pixels in frames if pixels is not None), None)
        if shape is None:
            raise ValueError("fake capture requires at least one frame to describe the source")
        self._source = CaptureSourceInfo(source_id, CaptureRegion(*origin, shape[1], shape[0]))
        self._frames = frames
        self._index = 0
        self._region = None

    def list_sources(self) -> list[CaptureSourceInfo]:
        return [self._source]

    def start(self, source_id: str, roi: CaptureRegion | None, target_fps: int) -> None:
        source = _source_info(self.list_sources(), source_id)
        self._region = _screen_region(source.region, roi)

    def read_latest(self) -> CapturedFrame | None:
        if self._region is None:
            raise CaptureError("CAPTURE_NOT_STARTED", "Select a capture source before reading a frame.")
        pixels = self._frames[min(self._index, len(self._frames) - 1)]
        self._index += 1
        if pixels is None:
            return None
        region, source = self._region, self._source
        left, top = region.left - source.region.left, region.top - source.region.top
        pixels = pixels[top:top + region.height, left:left + region.width]
        return CapturedFrame(self._index, pixels, region.left, region.top, perf_counter_ns(),
                             source.source_id, region.width, region.height, source.region)

    def stop(self) -> None:
        self._region = None

    def close(self) -> None:
        self.stop()


def _source_info(sources: list[CaptureSourceInfo], source_id: str) -> CaptureSourceInfo:
    for source in sources:
        if source.source_id == source_id:
            return source
    raise CaptureError("SOURCE_UNAVAILABLE", f"Capture source {source_id!r} is unavailable.")


def _screen_region(source: CaptureRegion, roi: CaptureRegion | None) -> CaptureRegion:
    if roi is None:
        return source
    region = replace(roi, left=source.left + roi.left, top=source.top + roi.top)
    if not source.contains(region):
        raise CaptureError("INVALID_ROI", "The search region must fit inside the selected capture source.")
    return region


def list_capture_sources(*, backend_factory=MSSBackend) -> list[CaptureSourceInfo]:
    backend = backend_factory()
    try:
        return backend.list_sources()
    finally:
        backend.close()


def capture_once(source_id: str = "monitor.1", *, roi: CaptureRegion | None = None,
                 backend_factory=MSSBackend) -> CapturedFrame:
    backend = backend_factory()
    try:
        backend.start(source_id, roi, 30)
        frame = backend.read_latest()
        if frame is None:
            raise CaptureError("NO_NEW_FRAME", "The capture source did not produce a frame.")
        return frame
    finally:
        backend.close()


class RecognitionStatus(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RecognitionResult:
    status: RecognitionStatus
    detector_id: str
    template_id: str | None
    frame_id: int | None
    source_id: str
    captured_at_ns: int | None
    processed_at_ns: int
    bbox: tuple[int, int, int, int] | None = None
    center_x: float | None = None
    center_y: float | None = None
    score: float | None = None
    error_code: str | None = None
    error_message: str | None = None
    pixel_count: int | None = None
    fraction: float | None = None
    roi: CaptureRegion | None = None


@dataclass(frozen=True)
class _DetectorSpec:
    detector: str
    image: str | None
    template_id: str | None
    roi: CaptureRegion | None
    enter_threshold: float
    exit_threshold: float
    appearance_frames: int
    disappearance_frames: int
    cooldown_ms: int
    max_age_ms: int
    scan_interval_ms: int
    hsv_lower: tuple[int, int, int]
    hsv_upper: tuple[int, int, int]
    min_pixel_count: int
    min_fraction: float
    fire_on_initial_present: bool


def _parse_spec(spec: dict) -> _DetectorSpec:
    detector = spec.get("detector", "template")
    if detector not in ("template", "hsv"):
        raise ValueError("unknown perception detector")
    raw_roi = spec.get("roi")
    roi = (raw_roi if isinstance(raw_roi, CaptureRegion) else CaptureRegion(**raw_roi)) if raw_roi is not None else None
    if roi and any(type(value) is not int for value in asdict(roi).values()):
        raise ValueError("ROI coordinates and dimensions must be integers")
    enter = spec.get("enter_threshold", spec.get("min_threshold", 0.90))
    exit_ = spec.get("exit_threshold", min(0.82, enter))
    if not -1 <= exit_ <= enter <= 1:
        raise ValueError("exit threshold must not exceed the enter threshold")

    def integer(key, default, minimum):
        value = spec.get(key, default)
        if type(value) is not int or value < minimum:
            raise ValueError(f"{key} must be an integer of at least {minimum}")
        return value

    lower = tuple(spec.get("hsv_lower", (0, 0, 0)))
    upper = tuple(spec.get("hsv_upper", (179, 255, 255)))
    for bound in (lower, upper):
        if len(bound) != 3 or any(type(value) is not int or not 0 <= value <= limit
                                  for value, limit in zip(bound, (179, 255, 255))):
            raise ValueError("HSV limits must use the OpenCV 8-bit ranges")
    if lower[1] > upper[1] or lower[2] > upper[2]:
        raise ValueError("HSV saturation and value minimums must not exceed their maximums")
    fraction = spec.get("min_fraction", 0.01)
    if not 0 <= fraction <= 1:
        raise ValueError("min_fraction must be between 0 and 1")
    fire_on_initial = spec.get("fire_on_initial_present", False)
    if not isinstance(fire_on_initial, bool):
        raise ValueError("fire_on_initial_present must be boolean")
    image, template_id = spec.get("image"), spec.get("template_id")
    if any(value is not None and not isinstance(value, str) for value in (image, template_id)):
        raise ValueError("image path and template ID must be strings")
    return _DetectorSpec(
        detector, image, template_id, roi, enter, exit_,
        integer("appearance_frames", 2, 1), integer("disappearance_frames", 2, 1),
        integer("cooldown_ms", 500, 0), integer("max_age_ms", 250, 1),
        integer("scan_interval_ms", 33, 0), lower, upper,
        integer("min_pixel_count", 2, 1), fraction, fire_on_initial,
    )


def _unknown(source_id, spec, code, message, frame=None):
    return RecognitionResult(RecognitionStatus.UNKNOWN, spec.detector, spec.template_id,
                             frame.frame_id if frame else None, source_id,
                             frame.captured_at_ns if frame else None, perf_counter_ns(),
                             error_code=code, error_message=message)


def _fresh(result: RecognitionResult, max_age_ms: int) -> RecognitionResult:
    if (result.status != RecognitionStatus.UNKNOWN and result.captured_at_ns is not None and
            perf_counter_ns() - result.captured_at_ns > max_age_ms * NS_PER_MS):
        return replace(result, status=RecognitionStatus.UNKNOWN, bbox=None, center_x=None, center_y=None,
                       score=None, pixel_count=None, fraction=None, error_code="STALE_RESULT",
                       error_message="The capture frame expired.")
    return result


def _load_template(base: Path, image: str | None) -> ndarray:
    import cv2
    import numpy as np

    if not image:
        raise CaptureError("INVALID_TEMPLATE", "No template image was selected.")
    path = (base / image).resolve()
    if not path.is_relative_to(base):
        raise CaptureError("INVALID_TEMPLATE", "The template must be inside the project.")
    try:
        encoded = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        decoded = cv2.imdecode(encoded, cv2.IMREAD_UNCHANGED) if encoded.size else None
    except (OSError, cv2.error) as error:
        raise CaptureError("INVALID_TEMPLATE", "The template image could not be read.") from error
    if decoded is None or decoded.dtype != np.uint8:
        raise CaptureError("INVALID_TEMPLATE", "The template must be a readable 8-bit image.")
    return _prepare_template(decoded)


def _prepare_template(decoded: ndarray) -> ndarray:
    import cv2
    import numpy as np

    if decoded.dtype != np.uint8 or not decoded.size:
        raise CaptureError("INVALID_TEMPLATE", "The template must contain 8-bit pixels.")
    if decoded.ndim == 2:
        decoded = cv2.cvtColor(decoded, cv2.COLOR_GRAY2BGR)
    elif decoded.ndim == 3 and decoded.shape[2] == 4:
        if not np.any(decoded[:, :, 3]):
            raise CaptureError("INVALID_TEMPLATE", "The template has no visible pixels.")
        decoded = cv2.cvtColor(decoded, cv2.COLOR_BGRA2BGR)
    elif decoded.ndim != 3 or decoded.shape[2] != 3:
        raise CaptureError("INVALID_TEMPLATE", "The template must be grayscale, BGR or BGRA.")
    _, deviation = cv2.meanStdDev(decoded)
    if deviation.max() < 0.5:
        raise CaptureError("INVALID_TEMPLATE", "The template has too little contrast. Use HSV for a solid color.")
    return decoded


def _match(pixels, template):
    import cv2
    import numpy as np

    height, width = template.shape[:2]
    if height > pixels.shape[0] or width > pixels.shape[1]:
        raise CaptureError("INVALID_TEMPLATE", "The template is larger than the search region.")
    scores = cv2.matchTemplate(pixels, template, cv2.TM_CCOEFF_NORMED)
    if not np.isfinite(scores).all():
        raise CaptureError("INVALID_SCORE", "Template matching returned a non-finite score.")
    _, score, _, (left, top) = cv2.minMaxLoc(scores)
    return score, (left, top, width, height), (left + width / 2, top + height / 2)


def _color(pixels, lower, upper):
    import cv2

    hsv = cv2.cvtColor(pixels, cv2.COLOR_BGR2HSV)
    if lower[0] <= upper[0]:
        mask = cv2.inRange(hsv, lower, upper)
    else:
        high = cv2.inRange(hsv, lower, (179, *upper[1:]))
        low = cv2.inRange(hsv, (0, *lower[1:]), upper)
        mask = cv2.bitwise_or(high, low)
    moments = cv2.moments(mask, binaryImage=True)
    count = int(moments["m00"])
    if count:
        return count, count / mask.size, cv2.boundingRect(mask), (
            moments["m10"] / count, moments["m01"] / count,
        )
    return 0, 0.0, None, None


def _detect(frame, spec, load_template, threshold, *, source_region=None):
    try:
        roi = _screen_region(source_region or frame.source_region or frame.region, spec.roi)
        if not frame.region.contains(roi):
            raise CaptureError("INVALID_ROI", "The captured frame does not contain the search region.")
        left, top = roi.left - frame.origin_x, roi.top - frame.origin_y
        pixels = frame.bgr[top:top + roi.height, left:left + roi.width]
        pixel_count, fraction = None, None
        if spec.detector == "template":
            score, bbox, center = _match(pixels, load_template(spec.image))
            found = score >= threshold
        else:
            pixel_count, fraction, bbox, center = _color(pixels, spec.hsv_lower, spec.hsv_upper)
            score = fraction
            found = pixel_count >= spec.min_pixel_count and fraction >= spec.min_fraction
        if found:
            x, y, width, height = bbox
            bbox = (x + roi.left, y + roi.top, width, height)
            center_x, center_y = center[0] + roi.left, center[1] + roi.top
        else:
            bbox, center_x, center_y = None, None, None
        result = RecognitionResult(
            RecognitionStatus.FOUND if found else RecognitionStatus.NOT_FOUND,
            spec.detector, spec.template_id, frame.frame_id, frame.source_id, frame.captured_at_ns,
            perf_counter_ns(), bbox, center_x, center_y, score, pixel_count=pixel_count,
            fraction=fraction, roi=roi,
        )
        return _fresh(result, spec.max_age_ms)
    except CaptureError as error:
        return _unknown(frame.source_id, spec, error.code, str(error), frame)
    except Exception as error:
        return _unknown(frame.source_id, spec, "RECOGNITION_FAILED", str(error), frame)


def recognize(frame: CapturedFrame, spec: dict, base: str | Path = ".") -> RecognitionResult:
    config = _parse_spec(spec)
    root = Path(base).resolve()
    return _detect(frame, config, lambda image: _load_template(root, image), config.enter_threshold)


class TemplateMatcher:
    @staticmethod
    def detect(frame: CapturedFrame, spec: dict, template: ndarray) -> RecognitionResult:
        config = _parse_spec({**spec, "detector": "template"})
        return _detect(frame, config, lambda _: _prepare_template(template), config.enter_threshold)


class ColorDetector:
    @staticmethod
    def detect(frame: CapturedFrame, spec: dict) -> RecognitionResult:
        return recognize(frame, {**spec, "detector": "hsv"})


@dataclass
class _Monitor:
    spec: _DetectorSpec
    state: RecognitionStatus | None = None
    candidate: RecognitionStatus | None = None
    consecutive: int = 0
    last_frame: int | None = None
    last_event_ns: int | None = None
    next_scan_ns: int = 0

    @property
    def threshold(self):
        if self.state == RecognitionStatus.FOUND:
            return self.spec.exit_threshold
        return self.spec.enter_threshold

    def observe(self, result: RecognitionResult) -> str | None:
        status = result.status
        if status == RecognitionStatus.UNKNOWN:
            self.candidate, self.consecutive = None, 0
            return None
        if result.frame_id == self.last_frame:
            return None
        self.last_frame = result.frame_id
        if status == self.state:
            self.candidate, self.consecutive = None, 0
            return None
        self.consecutive = self.consecutive + 1 if status == self.candidate else 1
        self.candidate = status
        needed = self.spec.appearance_frames if status == RecognitionStatus.FOUND else self.spec.disappearance_frames
        if self.consecutive < needed:
            return None
        previous, self.state = self.state, status
        self.candidate, self.consecutive = None, 0
        if previous is None and (status == RecognitionStatus.NOT_FOUND or not self.spec.fire_on_initial_present):
            return None
        now = result.processed_at_ns
        if self.last_event_ns is not None and now - self.last_event_ns < self.spec.cooldown_ms * NS_PER_MS:
            return None
        self.last_event_ns = now
        return "image_appeared" if status == RecognitionStatus.FOUND else "image_disappeared"


def _capture_area(source: CaptureRegion, areas: list[CaptureRegion]) -> CaptureRegion | None:
    left = min(area.left for area in areas)
    top = min(area.top for area in areas)
    region = CaptureRegion(left, top, max(area.right for area in areas) - left,
                           max(area.bottom for area in areas) - top)
    if region == source:
        return None
    return replace(region, left=left - source.left, top=top - source.top)


def _capture_error(error):
    if isinstance(error, CaptureError):
        return error.code, str(error)
    if isinstance(error, ImportError):
        return "CAPTURE_UNAVAILABLE", "Screen capture and OpenCV require the vision dependencies."
    return "CAPTURE_FAILED", str(error)


@dataclass(frozen=True)
class _Message:
    kind: str
    request_id: int
    session_id: str
    payload: object
    schema_version: int = 1


def _perception_worker(base, source_id, target_fps, backend_factory, session_id, commands, output, stopped):
    backend = None
    active_roi = object()
    selected_source = None
    previous_frame_id = None
    last_health = None
    retry_after = 0
    backoff_ns = 100 * NS_PER_MS
    capture_error = ("CAPTURE_UNAVAILABLE", "The capture source is unavailable.")
    frame_ids, event_ids = count(1), count(1)
    monitors = {}
    pending = deque()
    period_ns = round(1e9 / target_fps)
    next_capture = 0

    @lru_cache(maxsize=16)
    def load_template(image):
        return _load_template(base, image)

    try:
        while not stopped.is_set():
            while pending:
                try:
                    output.put_nowait(pending[0])
                except Full:
                    break
                pending.popleft()
            if pending:
                stopped.wait(0.005)
                continue

            now = perf_counter_ns()
            if now < next_capture:
                stopped.wait(min(0.01, (next_capture - now) / 1e9))
                continue
            requests = {}
            for _ in range(MAX_REQUESTS + 2 * MAX_SUBSCRIPTIONS):
                try:
                    message = commands.get_nowait()
                except Empty:
                    break
                if message.schema_version != 1 or message.session_id != session_id:
                    continue
                if message.kind == "request":
                    requests[message.request_id] = message.payload
                elif message.kind == "subscribe":
                    monitors[message.request_id] = _Monitor(message.payload)
                elif message.kind == "unsubscribe":
                    monitors.pop(message.request_id, None)

            due = {key: monitor for key, monitor in monitors.items() if monitor.next_scan_ns <= now}
            if not requests and not due:
                stopped.wait(0.005)
                continue

            next_capture = now + period_ns
            specs = set(requests.values()) | {monitor.spec for monitor in due.values()}
            frame, source, session_frame_id, invalid_regions = None, None, None, {}
            if now >= retry_after:
                try:
                    if backend is None:
                        backend = backend_factory()
                        active_roi = object()
                        previous_frame_id = None
                    source = _source_info(backend.list_sources(), source_id)
                    if selected_source is not None and source != selected_source:
                        raise CaptureError("SOURCE_CHANGED", "The selected monitor changed. Restart the session to use it.")
                    selected_source = source
                    areas = []
                    for spec in specs:
                        try:
                            areas.append(_screen_region(source.region, spec.roi))
                        except CaptureError as error:
                            invalid_regions[spec] = _capture_error(error)
                    if areas:
                        roi = _capture_area(source.region, areas)
                        if active_roi != roi:
                            backend.start(source_id, roi, target_fps)
                            active_roi = roi
                        frame = backend.read_latest()
                        if frame is not None and frame.frame_id != previous_frame_id:
                            previous_frame_id = frame.frame_id
                            session_frame_id = next(frame_ids)
                        else:
                            frame = None
                            capture_error = ("NO_NEW_FRAME", "The capture source did not produce a new frame.")
                    backoff_ns = 100 * NS_PER_MS
                except Exception as error:
                    frame = None
                    capture_error = _capture_error(error)
                    retry_after = perf_counter_ns() + backoff_ns
                    backoff_ns = min(backoff_ns * 2, 2_000 * NS_PER_MS)
                    if backend is not None:
                        try:
                            backend.close()
                        except Exception:
                            pass
                    backend = None

            if frame is not None:
                health = None, None
            elif len(invalid_regions) == len(specs):
                health = next(iter(invalid_regions.values()))
            else:
                health = capture_error
            if health != last_health:
                invalidated_at = perf_counter_ns() if (
                    frame is None and len(invalid_regions) != len(specs) and health[0] != "NO_NEW_FRAME"
                ) else None
                pending.append(_Message("health", 0, session_id, (*health, invalidated_at)))
                last_health = health
            results = {}

            def evaluate(spec, threshold):
                key = (spec, threshold)
                if key not in results:
                    if spec in invalid_regions:
                        results[key] = _unknown(source_id, spec, *invalid_regions[spec])
                    elif frame is None:
                        results[key] = _unknown(source_id, spec, *capture_error)
                    else:
                        result = _detect(frame, spec, load_template, threshold, source_region=source.region)
                        results[key] = replace(result, frame_id=session_frame_id)
                return _fresh(results[key], spec.max_age_ms)

            for identifier, spec in requests.items():
                if stopped.is_set():
                    return
                pending.append(_Message("result", identifier, session_id, evaluate(spec, spec.enter_threshold)))
            for identifier, monitor in due.items():
                if stopped.is_set():
                    return
                spec = monitor.spec
                monitor.next_scan_ns = now + spec.scan_interval_ms * NS_PER_MS
                result = evaluate(spec, monitor.threshold)
                event_type = monitor.observe(result)
                if event_type:
                    pending.append(_Message("event", identifier, session_id, {
                        "type": event_type, "subscription_id": identifier, "event_seq": next(event_ids),
                        "image_id": spec.template_id or spec.image, "result": result,
                        "occurred_at_ns": result.processed_at_ns, "schema_version": 1,
                        "request_id": identifier, "session_id": session_id,
                    }))
    finally:
        try:
            if backend is not None:
                backend.close()
        finally:
            output.cancel_join_thread()


class PerceptionService:
    def __init__(self, base: str | Path, *, source_id: str = "monitor.1", target_fps: int = 30,
                 backend_factory=MSSBackend):
        from uuid import uuid4

        if type(target_fps) is not int or not 5 <= target_fps <= 60:
            raise ValueError("target_fps must be an integer between 5 and 60")
        self.source_id = source_id
        self.session_id = uuid4().hex
        context = get_context("spawn")
        self._commands = context.Queue(maxsize=MAX_REQUESTS + 2 * MAX_SUBSCRIPTIONS)
        self._output = context.Queue(maxsize=MAX_OUTPUT)
        self._stopped = context.Event()
        self._process = context.Process(
            target=_perception_worker,
            args=(Path(base).resolve(), source_id, target_fps, backend_factory, self.session_id,
                  self._commands, self._output, self._stopped),
            daemon=True,
        )
        self._ids = count(1)
        self._requests = {}
        self._completed = {}
        self._subscriptions = {}
        self._removing = set()
        self._events = deque(maxlen=MAX_OUTPUT)
        self._dropped_events = 0
        self._latest_pending = {}
        self._latest_results = OrderedDict()
        self._capture_health = None, None
        self._invalidated_at_ns = -1
        self._started = False
        self._closed = False

    def __enter__(self):
        if self._started or self._closed:
            raise RuntimeError("The perception session can only be opened once.")
        try:
            self._process.start()
        except BaseException:
            self.close()
            raise
        self._started = True
        return self

    def __exit__(self, *exc):
        self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stopped.set()
        if self._started:
            self._process.join(0.2)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(0.5)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(0.5)
        self._commands.cancel_join_thread()
        self._commands.close()
        self._output.close()
        self._requests.clear()
        self._completed.clear()
        self._subscriptions.clear()
        self._events.clear()
        self._latest_pending.clear()
        self._latest_results.clear()
        self._removing.clear()

    def _alive(self):
        return self._started and not self._closed and self._process.is_alive()

    def _send(self, action, identifier, spec=None):
        try:
            self._commands.put_nowait(_Message(action, identifier, self.session_id, spec))
        except Full:
            return False
        return True

    def _poll(self):
        if not self._started or self._closed:
            return
        for _ in range(MAX_OUTPUT):
            try:
                message = self._output.get_nowait()
            except Empty:
                break
            if message.session_id != self.session_id or message.schema_version != 1:
                continue
            kind, identifier, value = message.kind, message.request_id, message.payload
            if kind == "result" and identifier in self._requests:
                spec = self._requests[identifier]
                self._latest_results[spec] = value
                self._latest_results.move_to_end(spec)
                if len(self._latest_results) > MAX_REQUESTS:
                    self._latest_results.popitem(last=False)
                if self._latest_pending.get(spec) == identifier:
                    del self._requests[identifier], self._latest_pending[spec]
                else:
                    self._completed[identifier] = value
            elif kind == "event" and identifier in self._subscriptions:
                if len(self._events) == MAX_OUTPUT:
                    self._dropped_events += 1
                self._events.append(value)
            elif kind == "health":
                code, detail, invalidated_at = value
                self._capture_health = code, detail
                if invalidated_at is not None:
                    self._invalidated_at_ns = invalidated_at
        for identifier in tuple(self._removing):
            if not self._send("unsubscribe", identifier):
                break
            self._removing.remove(identifier)

    def _request(self, spec):
        if not self._alive() or len(self._requests) >= MAX_REQUESTS:
            return None
        identifier = next(self._ids)
        if not self._send("request", identifier, spec):
            return None
        self._requests[identifier] = spec
        return identifier

    def request(self, spec: dict) -> int | None:
        self._poll()
        return self._request(_parse_spec(spec))

    def take(self, request_id: int) -> RecognitionResult | None:
        self._poll()
        spec = self._requests.get(request_id)
        if spec is None:
            return None
        result = self._completed.pop(request_id, None)
        if not self._alive():
            result = self._worker_unavailable(spec)
        if result is None:
            return None
        del self._requests[request_id]
        return self._current_result(result, spec)

    def cancel(self, request_id: int) -> None:
        self._requests.pop(request_id, None)
        self._completed.pop(request_id, None)

    def subscribe(self, spec: dict) -> int | None:
        self._poll()
        if not self._alive() or len(self._subscriptions) + len(self._removing) >= MAX_SUBSCRIPTIONS:
            return None
        identifier = next(self._ids)
        config = _parse_spec(spec)
        if not self._send("subscribe", identifier, config):
            return None
        self._subscriptions[identifier] = config
        return identifier

    def unsubscribe(self, identifier: int) -> None:
        self._poll()
        if self._subscriptions.pop(identifier, None) is not None and self._alive():
            if not self._send("unsubscribe", identifier):
                self._removing.add(identifier)

    def drain_events(self) -> list[dict]:
        self._poll()
        events = []
        while self._events:
            event = self._events.popleft()
            spec = self._subscriptions.get(event["subscription_id"])
            if spec and self._current_result(event["result"], spec).status != RecognitionStatus.UNKNOWN:
                events.append(event)
        return events

    def _is_current(self, result):
        return (self._alive() and result.source_id == self.source_id and result.captured_at_ns is not None
                and result.captured_at_ns > self._invalidated_at_ns)

    def is_current(self, result):
        self._poll()
        return self._is_current(result)

    def _current_result(self, result, spec):
        if result.status != RecognitionStatus.UNKNOWN and not self._is_current(result):
            code, detail = self._capture_health
            return _unknown(self.source_id, spec, code or "SOURCE_INVALIDATED",
                            detail or "The observation predates a capture failure.")
        return _fresh(result, spec.max_age_ms)

    def _worker_unavailable(self, spec):
        return _unknown(self.source_id, spec, "PERCEPTION_UNAVAILABLE", "The perception worker is not running.")

    def health(self) -> dict:
        self._poll()
        running = self._alive()
        code, message = self._capture_health
        if not running:
            code, message = "PERCEPTION_UNAVAILABLE", "The perception worker is not running."
        return {"session_id": self.session_id, "schema_version": 1, "source_id": self.source_id,
                "running": running, "error_code": code, "error_message": message,
                "dropped_events": self._dropped_events}

    def latest(self, spec: dict) -> RecognitionResult | None:
        self._poll()
        config = _parse_spec(spec)
        if not self._alive():
            return self._worker_unavailable(config)
        result = self._latest_results.get(config)
        fresh = self._current_result(result, config) if result is not None else None
        if config in self._latest_pending:
            return fresh
        if (result is None or fresh.error_code == "STALE_RESULT" or
                (result.status != RecognitionStatus.UNKNOWN and fresh.status == RecognitionStatus.UNKNOWN) or
                perf_counter_ns() - result.processed_at_ns >= config.scan_interval_ms * NS_PER_MS):
            identifier = self._request(config)
            if identifier is not None:
                self._latest_pending[config] = identifier
            elif fresh is None:
                return _unknown(self.source_id, config, "IPC_BUSY", "The perception request queue is full.")
        return fresh
