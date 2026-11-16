"""Exercise screen capture or template matching without sending input."""

import argparse
from functools import partial
import json
from pathlib import Path
from statistics import median
import sys
from tempfile import TemporaryDirectory
from time import perf_counter_ns, process_time_ns, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_studio.perception import (
    CapturedFrame, CaptureRegion, FakeCaptureBackend, MSSBackend, PerceptionService, TemplateMatcher,
)


def summary(samples):
    if not samples:
        return None
    return {
        "min_ms": min(samples) / 1e6,
        "median_ms": median(samples) / 1e6,
        "max_ms": max(samples) / 1e6,
    }


def capture(source, seconds, fps, roi):
    if not 5 <= fps <= 60:
        raise ValueError("capture rate must be between 5 and 60 FPS")
    samples = []
    frame_ids = set()
    errors = []
    backend = MSSBackend()
    started = perf_counter_ns()
    try:
        backend.start(source, roi, fps)
        while perf_counter_ns() - started < seconds * 1e9:
            before = perf_counter_ns()
            frame = backend.read_latest()
            if frame is None:
                sleep(0.005)
                continue
            if frame.frame_id in frame_ids:
                errors.append("duplicate frame ID")
            if frame.image_bgr.shape != (frame.height, frame.width, 3):
                errors.append("unexpected frame dimensions")
            frame_ids.add(frame.frame_id)
            elapsed = perf_counter_ns() - before
            samples.append(elapsed)
            sleep(max(0, 1 / fps - elapsed / 1e9))
    finally:
        backend.close()
    elapsed = (perf_counter_ns() - started) / 1e9
    return {"source": source, "frames": len(frame_ids), "seconds": elapsed,
            "fps": len(frame_ids) / elapsed, "capture": summary(samples), "errors": errors}


def recognize(count):
    import cv2
    import numpy as np

    rng = np.random.default_rng(286)
    screen = rng.integers(0, 256, (200, 200, 3), dtype=np.uint8)
    template = screen[25:49, 100:127].copy()
    matcher = TemplateMatcher()
    frame = CapturedFrame(1, screen, -200, 0, perf_counter_ns(), "static", 200, 200)
    spec = {"detector": "template", "enter_threshold": 0.90, "max_age_ms": 60_000}
    samples = []
    errors = []
    for _ in range(count):
        started = perf_counter_ns()
        result = matcher.detect(frame, spec, template)
        samples.append(perf_counter_ns() - started)
        if result.status != "FOUND" or result.bbox != (-100, 25, 27, 24):
            errors.append({"status": result.status, "bbox": result.bbox})
    return {"attempts": count, "matches": count - len(errors), "opencv": cv2.__version__,
            "detection": summary(samples), "errors": errors}


def schedule_trial(hz, count, perception, spec):
    from macro_studio.backend import FakeInputBackend
    from macro_studio.benchmark import make_program, summarize
    from macro_studio.live import DeadlineClock
    from macro_studio.runtime import Runtime

    clock = DeadlineClock()
    runner = Runtime(make_program(hz, count, "A"), clock, FakeInputBackend(clock))
    rows = []
    frames = {}
    next_scan = 0

    def observe(scheduled, actual, action, inserted):
        rows.append({"scheduled_ns": scheduled, "actual_ns": actual, "action": action, "inserted": inserted})

    def poll():
        nonlocal next_scan
        now = clock.now_ns
        if perception is not None and now >= next_scan:
            next_scan = now + 10_000_000
            result = perception.latest(spec)
            if result is not None and result.status == "FOUND":
                frames.setdefault(result.frame_id, (result.score, clock.now_ns - result.captured_at_ns))
        return None, []

    runner.observe_input = observe
    cpu_start = process_time_ns()
    started = clock.now_ns
    runner.run(round(count / hz * 1e9) + 500_000_000, [], poll=poll)
    elapsed = clock.now_ns - started
    report = summarize(hz, rows, runner.missed_ticks, runner.stop_latency_ns)
    jitter = [row["actual_ns"] - row["scheduled_ns"] for row in rows if row["scheduled_ns"] is not None]
    report["jitter_ns"]["p50"] = median(jitter) if jitter else None
    report["vision"] = perception is not None
    report["recognized_frames"] = len(frames)
    report["recognition_fps"] = len(frames) * 1e9 / elapsed
    report["frame_age"] = summary([age for _, age in frames.values()])
    report["scores"] = [min(score for score, _ in frames.values()),
                        max(score for score, _ in frames.values())] if frames else None
    report["runner_cpu_ms"] = (process_time_ns() - cpu_start) / 1e6
    report["errors"] = [row["message"] for row in runner.errors]
    if report["injected_pairs"] == 0 or (perception is not None and len(frames) < 2):
        report["errors"].append("the scheduler or perception did not complete work")
    return report


def schedule(hz, pairs, repeats):
    import cv2
    import numpy as np

    template = np.random.default_rng(286).integers(0, 256, (24, 27, 3), dtype=np.uint8)
    frame = np.zeros((300, 400, 3), dtype=np.uint8)
    frame[25:49, 100:127] = template
    source = partial(FakeCaptureBackend, (frame,))
    results = []
    with TemporaryDirectory() as directory:
        saved, png = cv2.imencode(".png", template)
        if not saved:
            raise RuntimeError("cannot encode the test template")
        Path(directory, "template.png").write_bytes(png.tobytes())
        spec = {"detector": "template", "image": "template.png", "enter_threshold": 0.90}
        for _ in range(repeats):
            results.append(schedule_trial(hz, pairs, None, spec))
            with PerceptionService(directory, backend_factory=source) as perception:
                probe = perception.request(spec)
                result = None
                deadline = perf_counter_ns() + 5_000_000_000
                while perf_counter_ns() < deadline and (result := perception.take(probe)) is None:
                    sleep(0.01)
                if result is None or result.status != "FOUND" or result.bbox != (100, 25, 27, 24):
                    raise RuntimeError("perception warm-up did not find the test template")
                results.append(schedule_trial(hz, pairs, perception, spec))
    return {"runs": results, "errors": [error for report in results for error in report["errors"]]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    capture_parser = modes.add_parser("capture")
    capture_parser.add_argument("--source", default="monitor.1")
    capture_parser.add_argument("--seconds", type=float, default=10)
    capture_parser.add_argument("--fps", type=int, default=30)
    capture_parser.add_argument("--roi", nargs=4, metavar=("LEFT", "TOP", "WIDTH", "HEIGHT"), type=int)
    detect_parser = modes.add_parser("recognize")
    detect_parser.add_argument("--count", type=int, default=30)
    schedule_parser = modes.add_parser("schedule")
    schedule_parser.add_argument("--hz", choices=(100, 200), type=int, default=200)
    schedule_parser.add_argument("--pairs", type=int, default=150)
    schedule_parser.add_argument("--repeats", type=int, default=5)
    args = parser.parse_args()
    try:
        if args.mode == "capture":
            if not 0 < args.seconds <= 120:
                parser.error("--seconds must be greater than 0 and at most 120")
            result = capture(args.source, args.seconds, args.fps, CaptureRegion(*args.roi) if args.roi else None)
            if not result["frames"]:
                result["errors"].append("no frames captured")
        elif args.mode == "recognize":
            if not 1 <= args.count <= 10_000:
                parser.error("--count must be between 1 and 10000")
            result = recognize(args.count)
        else:
            if not 50 <= args.pairs <= 3000 or not 1 <= args.repeats <= 20:
                parser.error("--pairs must be 50–3000 and --repeats must be 1–20")
            result = schedule(args.hz, args.pairs, args.repeats)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        result = {"errors": [str(error)]}
    print(json.dumps(result, ensure_ascii=False))
    return bool(result["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
