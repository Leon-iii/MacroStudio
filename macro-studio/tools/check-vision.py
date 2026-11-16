"""Exercise screen capture or template matching without sending input."""

import argparse
import json
from pathlib import Path
from statistics import median
import sys
from time import perf_counter_ns, sleep

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from macro_studio.perception import CapturedFrame, CaptureRegion, MSSBackend, TemplateMatcher


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
    import numpy as np

    rng = np.random.default_rng(286)
    screen = rng.integers(0, 256, (200, 200, 3), dtype=np.uint8)
    template = screen[25:49, 100:127].copy()
    matcher = TemplateMatcher()
    frame = CapturedFrame(1, screen, -200, 0, perf_counter_ns(), "static", 200, 200)
    spec = {"detector": "template", "enter_threshold": 0.90}
    samples = []
    errors = []
    for _ in range(count):
        started = perf_counter_ns()
        result = matcher.detect(frame, spec, template)
        samples.append(perf_counter_ns() - started)
        if result.status != "FOUND" or result.bbox != (-100, 25, 27, 24):
            errors.append({"status": result.status, "bbox": result.bbox})
    return {"attempts": count, "matches": count - len(errors), "detection": summary(samples), "errors": errors}


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
    args = parser.parse_args()
    try:
        if args.mode == "capture":
            if not 0 < args.seconds <= 120:
                parser.error("--seconds must be greater than 0 and at most 120")
            result = capture(args.source, args.seconds, args.fps, CaptureRegion(*args.roi) if args.roi else None)
            if not result["frames"]:
                result["errors"].append("no frames captured")
        else:
            if not 1 <= args.count <= 10_000:
                parser.error("--count must be between 1 and 10000")
            result = recognize(args.count)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        result = {"errors": [str(error)]}
    print(json.dumps(result, ensure_ascii=False))
    return bool(result["errors"])


if __name__ == "__main__":
    raise SystemExit(main())
