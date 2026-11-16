from dataclasses import asdict, replace
from functools import partial
import json
from multiprocessing import get_context
from time import monotonic, perf_counter_ns, sleep

import pytest

from macro_studio.perception import (
    MAX_REQUESTS,
    CaptureError,
    CaptureRegion,
    ColorDetector,
    FakeCaptureBackend,
    PerceptionService,
    RecognitionStatus,
    TemplateMatcher,
    capture_once,
    recognize,
)

np = pytest.importorskip("numpy")
cv2 = pytest.importorskip("cv2")


def _picture():
    template = np.random.default_rng(23).integers(0, 256, (5, 7, 3), dtype=np.uint8)
    absent = np.zeros((24, 32, 3), dtype=np.uint8)
    present = absent.copy()
    present[10:15, 13:20] = template
    return template, absent, present


def _saved_picture(directory):
    template, absent, present = _picture()
    ok, encoded = cv2.imencode(".png", template)
    assert ok
    name = "확인.png"
    (directory / name).write_bytes(encoded.tobytes())
    return {"detector": "template", "image": name, "template_id": "template.confirm"}, absent, present


def _wait_for(poll, *, timeout=5):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        value = poll()
        if value is not None:
            return value
        sleep(0.005)
    pytest.fail("Timed out waiting for the perception worker")


def test_capture_roi_uses_physical_origin_and_owns_pixels():
    _, _, pixels = _picture()
    frame = capture_once(roi=CaptureRegion(9, 6, 16, 12), backend_factory=partial(
        FakeCaptureBackend, (pixels,), origin=(-200, -90),
    ))
    assert frame.region == CaptureRegion(-191, -84, 16, 12)
    assert frame.source_region == CaptureRegion(-200, -90, 32, 24)
    expected = pixels[6:18, 9:25].copy()
    pixels[:] = 0
    assert np.array_equal(frame.bgr, expected)
    assert not frame.bgr.flags.writeable
    with pytest.raises(ValueError):
        frame.bgr.setflags(write=True)


def test_template_matches_at_global_roi_coordinates(tmp_path):
    spec, absent, present = _saved_picture(tmp_path)
    frame = capture_once(backend_factory=partial(FakeCaptureBackend, (present,), origin=(-200, -90)))
    spec["roi"] = {"left": 9, "top": 6, "width": 16, "height": 12}
    result = recognize(frame, spec, tmp_path)
    assert result.status is RecognitionStatus.FOUND
    assert result.template_id == "template.confirm"
    assert result.bbox == (-187, -80, 7, 5)
    assert (result.center_x, result.center_y) == (-183.5, -77.5)
    assert result.score == pytest.approx(1.0)
    payload = json.loads(json.dumps(asdict(result)))
    assert payload["status"] == "FOUND"
    assert payload["bbox"] == [-187, -80, 7, 5]
    assert payload["captured_at_ns"] <= payload["processed_at_ns"]

    negative = capture_once(backend_factory=partial(FakeCaptureBackend, (absent,)))
    result = recognize(negative, spec, tmp_path)
    assert result.status is RecognitionStatus.NOT_FOUND
    assert result.bbox is result.center_x is result.center_y is None


def test_invalid_roi_and_template_report_unknown(tmp_path):
    template, _, present = _picture()
    frame = capture_once(backend_factory=partial(FakeCaptureBackend, (present,)))
    assert TemplateMatcher.detect(frame, {}, np.zeros_like(template)).error_code == "INVALID_TEMPLATE"
    tiny_roi = {"left": 2, "top": 3, "width": 2, "height": 2}
    assert TemplateMatcher.detect(frame, {"roi": tiny_roi}, template).error_code == "INVALID_TEMPLATE"
    outside = {**tiny_roi, "left": -1}
    result = TemplateMatcher.detect(frame, {"roi": outside}, template)
    assert result.status is RecognitionStatus.UNKNOWN
    assert result.error_code == "INVALID_ROI"
    assert result.bbox is None
    assert recognize(frame, {"image": "missing.png"}, tmp_path).error_code == "INVALID_TEMPLATE"
    with pytest.raises(CaptureError, match="unavailable"):
        capture_once("bad-source", backend_factory=partial(FakeCaptureBackend, (present,)))


def test_hsv_wraparound_measures_mask_in_physical_coordinates():
    hsv = np.zeros((10, 10, 3), dtype=np.uint8)
    hsv[4, 3] = (178, 255, 255)
    hsv[5, 3] = (1, 255, 255)
    hsv[5, 4] = (67, 255, 255)
    bgr = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
    frame = capture_once(backend_factory=partial(FakeCaptureBackend, (bgr,), origin=(300, -70)))
    spec = {"hsv_lower": [170, 200, 200], "hsv_upper": [5, 255, 255], "min_pixel_count": 2,
            "min_fraction": 0.07, "roi": {"left": 2, "top": 2, "width": 5, "height": 5}}
    result = ColorDetector.detect(frame, spec)
    assert result.status is RecognitionStatus.FOUND
    assert result.bbox == (303, -66, 1, 2)
    assert (result.center_x, result.center_y) == (303, -65.5)
    assert result.roi == CaptureRegion(302, -68, 5, 5)
    assert result.pixel_count == 2
    assert result.score == result.fraction == pytest.approx(0.08)
    rejected = ColorDetector.detect(frame, {**spec, "min_pixel_count": 3})
    assert rejected.status is RecognitionStatus.NOT_FOUND
    assert rejected.bbox is None and rejected.pixel_count == 2
    assert ColorDetector.detect(frame, {**spec, "min_fraction": 0.09}).status is RecognitionStatus.NOT_FOUND


def test_worker_shares_frame_and_returns_fresh_coordinates(tmp_path):
    spec, _, present = _saved_picture(tmp_path)
    with PerceptionService(tmp_path, backend_factory=partial(
        FakeCaptureBackend, (present,), origin=(-100, -200),
    )) as service:
        first = service.request(spec)
        second = service.request({**spec, "roi": {"left": 7, "top": 8, "width": 16, "height": 10}})
        assert first is not None and second is not None
        a = _wait_for(lambda: service.take(first))
        b = _wait_for(lambda: service.take(second))
        assert a.frame_id == b.frame_id
        assert a.captured_at_ns == b.captured_at_ns
        assert a.bbox == b.bbox == (-87, -190, 7, 5)
        assert service.take(first) is None
        assert service.latest(spec) == a


class _AgedBackend(FakeCaptureBackend):
    def read_latest(self):
        return replace(super().read_latest(), captured_at_ns=perf_counter_ns() - 1_000_000_000)


def test_stale_results_never_expose_click_coordinates(tmp_path):
    spec, _, present = _saved_picture(tmp_path)
    with PerceptionService(tmp_path, backend_factory=partial(_AgedBackend, (present,))) as service:
        request = service.request({**spec, "max_age_ms": 100})
        result = _wait_for(lambda: service.take(request))
        assert result.status is RecognitionStatus.UNKNOWN
        assert result.error_code == "STALE_RESULT"
        assert result.bbox is result.center_x is result.center_y is None


def test_monitor_debounces_ignores_unknown_and_keeps_subscriptions_independent(tmp_path):
    spec, absent, present = _saved_picture(tmp_path)
    frames = (present, present, None, absent, None, absent, absent, present, present)
    spec.update(cooldown_ms=0, max_age_ms=2000, scan_interval_ms=0)
    with PerceptionService(tmp_path, target_fps=60, backend_factory=partial(FakeCaptureBackend, frames)) as service:
        quiet = service.subscribe(spec)
        initial = service.subscribe({**spec, "fire_on_initial_present": True})
        cooldown = service.subscribe({**spec, "fire_on_initial_present": True, "cooldown_ms": 5000})
        assert None not in (quiet, initial, cooldown)
        events = []

        def poll():
            events.extend(service.drain_events())
            return events if len(events) >= 6 else None

        _wait_for(poll)
        by_id = {identifier: [e for e in events if e["subscription_id"] == identifier]
                 for identifier in (quiet, initial, cooldown)}
        assert [e["type"] for e in by_id[quiet]] == ["image_disappeared", "image_appeared"]
        assert [e["type"] for e in by_id[initial]] == [
            "image_appeared", "image_disappeared", "image_appeared",
        ]
        assert [e["type"] for e in by_id[cooldown]] == ["image_appeared"]
        disappeared, appeared = by_id[quiet]
        assert disappeared["result"].frame_id == 5
        assert disappeared["result"].status is RecognitionStatus.NOT_FOUND
        assert appeared["result"].status is RecognitionStatus.FOUND
        assert appeared["image_id"] == "template.confirm"
        assert all(e["session_id"] == service.session_id for e in events)
        sequences = [e["event_seq"] for e in events]
        assert sequences == sorted(set(sequences))
        sleep(0.06)
        assert service.drain_events() == []


class _BlockedBackend(FakeCaptureBackend):
    def __init__(self, frames, release):
        super().__init__(frames)
        self.release = release

    def read_latest(self):
        if not self.release.wait(3):
            raise CaptureError("CAPTURE_TIMEOUT", "Test capture did not receive permission to finish.")
        return super().read_latest()


def test_oneshots_are_bounded_and_do_not_wait_for_capture(tmp_path):
    spec, _, present = _saved_picture(tmp_path)
    release = get_context("spawn").Event()
    with PerceptionService(tmp_path, backend_factory=partial(_BlockedBackend, (present,), release)) as service:
        active = [service.request(spec) for _ in range(MAX_REQUESTS)]
        assert None not in active
        assert service.request(spec) is None
        assert service.take(active[-1]) is None
        service.cancel(active[0])
        assert service.take(active[0]) is None
        replacement = service.request(spec)
        assert replacement is not None
        release.set()
        result = _wait_for(lambda: service.take(replacement))
        assert result.status is RecognitionStatus.FOUND


class _LostCaptureBackend(FakeCaptureBackend):
    def __init__(self, frames, attempts):
        super().__init__(frames)
        self.attempts = attempts

    def read_latest(self):
        self.attempts.value += 1
        if self.attempts.value == 1:
            raise OSError("Test capture temporarily disconnected")
        return super().read_latest()


def test_worker_reports_capture_loss_and_recovers_with_fresh_frames(tmp_path):
    spec, _, present = _saved_picture(tmp_path)
    spec.update(scan_interval_ms=0, max_age_ms=2000)
    attempts = get_context("spawn").Value("i", 0)
    with PerceptionService(tmp_path, backend_factory=partial(_LostCaptureBackend, (present,), attempts)) as service:
        request = service.request(spec)
        lost = _wait_for(lambda: service.take(request))
        assert lost.status is RecognitionStatus.UNKNOWN
        assert lost.error_code == service.health()["error_code"] == "CAPTURE_FAILED"
        assert lost.bbox is None

        def recovered():
            result = service.latest(spec)
            return result if result is not None and result.status is RecognitionStatus.FOUND else None

        assert _wait_for(recovered).bbox == (13, 10, 7, 5)
        assert service.health()["error_code"] is None
    assert not service.health()["running"]
    assert service.latest(spec).error_code == "PERCEPTION_UNAVAILABLE"


class _RepeatedFrameBackend(FakeCaptureBackend):
    cached = None

    def read_latest(self):
        if self.cached is None:
            self.cached = super().read_latest()
        return self.cached


def test_repeated_frame_cannot_complete_debounce(tmp_path):
    spec, _, present = _saved_picture(tmp_path)
    spec.update(fire_on_initial_present=True, scan_interval_ms=0, max_age_ms=2000)
    with PerceptionService(tmp_path, backend_factory=partial(_RepeatedFrameBackend, (present,))) as service:
        identifier = service.subscribe(spec)
        assert identifier is not None

        def no_new_frame():
            code = service.health()["error_code"]
            return code if code is not None else None

        assert _wait_for(no_new_frame) == "NO_NEW_FRAME"
        request = service.request(spec)
        assert _wait_for(lambda: service.take(request)).error_code == "NO_NEW_FRAME"
        assert service.drain_events() == []
        service.unsubscribe(identifier)
