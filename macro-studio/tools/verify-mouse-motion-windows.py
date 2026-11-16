"""Opt-in Windows hardware checks. Run with the project virtual environment."""

import argparse
import ctypes as c
from ctypes import wintypes as w
import json
from pathlib import Path
from statistics import median
from struct import unpack
from threading import Thread
from time import monotonic, perf_counter_ns, sleep
import tkinter as tk
import traceback

from macro_studio import live
from macro_studio.external_hid import Opcode
from macro_studio.external_hid.serial_link import list_ports
from macro_studio.external_hid.transport import TransportError
from macro_studio.ir import validate_program
from macro_studio.mouse_motion import BUILTIN_PROFILES, MotionGenerator
from macro_studio.win32 import WindowsDesktop


def percentiles(values):
    values = sorted(values)
    return {f"p{p}": values[(len(values) - 1) * p // 100] for p in (50, 95, 99)}


def screens():
    items = []
    cb_type = c.WINFUNCTYPE(w.BOOL, w.HANDLE, w.HDC, c.POINTER(w.RECT), w.LPARAM)

    @cb_type
    def collect(handle, dc, rect, data):
        x, y = c.c_uint(), c.c_uint()
        if c.windll.shcore.GetDpiForMonitor(w.HANDLE(handle), 0, c.byref(x), c.byref(y)):
            raise OSError("GetDpiForMonitor failed")
        r = rect.contents
        items.append((r.left, r.top, r.right, r.bottom, x.value, y.value))
        return True

    if not c.windll.user32.EnumDisplayMonitors(None, None, collect, 0):
        raise OSError("EnumDisplayMonitors failed")
    return items


def program(kind, position, ms):
    profile = BUILTIN_PROFILES["builtin.direct"]
    step = {"op": "mouse_move_timed", "block_id": "acceptance-move",
            "target": {"kind": kind, "x": position[0], "y": position[1]},
            "motion": {"profile_ref": profile.id, "profile": profile.to_dict(), "seed_override": 7,
                       "duration": {"value": ms, "unit": "ms"}}}
    return validate_program({"ir_version": 2, "project_id": "motion-acceptance",
                             "workflows": {"move": {"steps": [step]}},
                             "handlers": [{"id": "start", "workflow_id": "move",
                                           "event": {"type": "project_start"}}]})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["monitors", "focus", "usb", "metrics"])
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--confirm-real-input", action="store_true", required=True)
    args = parser.parse_args()
    user32 = c.windll.user32
    user32.SetProcessDpiAwarenessContext(c.c_void_p(-4))
    monitors = screens()
    root = tk.Tk()
    root.title("Macro Studio Mouse Motion Acceptance")
    root.attributes("-topmost", True)
    label = tk.Label(root, text="Macro Studio mouse motion acceptance", font=("Malgun Gothic", 19))
    label.pack(expand=True)
    root.update()
    desktop = WindowsDesktop()
    hwnd = next(item["hwnd"] for item in desktop.windows() if item["title"] == root.title())
    target = desktop.target(hwnd)
    if args.mode == "monitors":
        bounds = (min(x[0] for x in monitors), min(x[1] for x in monitors),
                  max(x[2] for x in monitors), max(x[3] for x in monitors))
    else:
        x, y = desktop.mouse_position()
        bounds = next(m[:4] for m in monitors if m[0] <= x < m[2] and m[1] <= y < m[3])
    user32.SetWindowPos.argtypes = (w.HWND, w.HWND, c.c_int, c.c_int, c.c_int, c.c_int, w.UINT)
    left, top, right, bottom = bounds
    if not user32.SetWindowPos(hwnd, c.c_void_p(-1), left, top, right - left, bottom - top, 0x0040):
        raise OSError("could not position the test window")
    root.update()
    root.focus_force()
    report = {"mode": args.mode, "monitors": monitors}
    reports = []
    original_open = live.open_port

    def traced_open(*positional, **keyword):
        link = original_open(*positional, **keyword)
        exchange = link.exchange

        def trace(opcode, payload=b""):
            if opcode is not Opcode.INPUT_EVENT:
                return exchange(opcode, payload)
            _, dx, dy = unpack("<Bhh", payload)
            started = perf_counter_ns()
            try:
                result = exchange(opcode, payload)
            except (ValueError, OSError, RuntimeError):
                reports.append({"dx": dx, "dy": dy, "ack": False})
                raise
            reports.append({"dx": dx, "dy": dy, "ack": True, "ack_ms": (perf_counter_ns() - started) / 1e6})
            return result

        link.exchange = trace
        return link

    live.open_port = traced_open

    def run(kind, position, duration, *, hid=False, **kwargs):
        return live.run_live(program(kind, position, duration), desktop, hwnd, 50_000_000_000,
                             hid_port=args.port if hid else None, status=lambda _: None, **kwargs)

    def direction(distance=220):
        x, y = desktop.mouse_position()
        return next((dx, dy) for dx in (distance, -distance) for dy in (45, -45)
                    if desktop.point_over(target, (x + dx, y + dy)))

    def cross():
        first, second = next((a, b) for a in monitors for b in monitors
                             if a[2] == b[0] and max(a[1], b[1]) + 100 < min(a[3], b[3]))
        y = (max(first[1], second[1]) + min(first[3], second[3])) // 2
        targets = [(first[2] - 180, y), (second[0] + 180, y), (first[2] - 180, y)]
        for xy in targets:
            result = run("absolute", xy, 350)
            assert result["termination"] == "completed" and not result["errors"], result
            assert desktop.mouse_position() == xy, (xy, desktop.mouse_position())
        report["targets"] = targets
        report["mixed_dpi"] = first[4:] != second[4:]

    def take_focus():
        other = tk.Toplevel(root)
        other.title("Focus transfer verification")
        other.attributes("-topmost", True)
        other.geometry("400x180+150+150")
        other.update()
        other.focus_force()

    def interrupt():
        paused_at = None
        requested = False
        external_error = None
        states = []

        def state(value):
            nonlocal paused_at
            states.append(value)
            if value == "PAUSED":
                paused_at = len(reports)

        def control():
            nonlocal requested
            if len(reports) >= 6 and not requested:
                requested = True
                if args.mode == "focus":
                    root.after(0, take_focus)
                else:
                    print("UNPLUG NOW", flush=True)
                    root.after(0, lambda: label.config(text="지금 Leonardo USB 케이블을 뽑으세요"))
            if paused_at is not None:
                return "stop"

        try:
            result = run("relative", direction(500), 1800 if args.mode == "focus" else 40000,
                         hid=True, control=control, on_state=state)
        except (OSError, TransportError) as error:
            if args.mode != "usb":
                raise
            external_error = str(error)
            result = {"termination": "input_error"}
        report["runner"], report["reports"] = result, reports
        report["external_error"] = external_error
        report["states"] = states
        position = desktop.mouse_position()
        sleep(0.6)
        assert requested and desktop.mouse_position() == position
        if args.mode == "focus":
            assert paused_at is not None and len(reports) == paused_at and result["termination"] == "stopped"
        else:
            assert result["termination"] == "input_error" and any(row["ack"] for row in reports)
            assert external_error or any(row["ack"] is False for row in reports)
            failures = [index for index, row in enumerate(reports) if not row["ack"]]
            if failures:
                assert not any(row["ack"] for row in reports[failures[0]:])
            report["visible_ports_after"] = [entry["port"] for entry in list_ports()]
            assert args.port not in report["visible_ports_after"]
        report["stationary_after"] = position

    def measure():
        report["runs"] = runs = []
        profile = BUILTIN_PROFILES["builtin.direct"]
        center = ((left + right) // 2, (top + bottom) // 2)
        for _ in range(5):
            for hid in (False, True):
                reports.clear()
                setup = run("absolute", center, 120)
                assert not setup["errors"] and desktop.mouse_position() == center, setup
                delta = direction()
                trajectory = MotionGenerator.generate((0, 0), delta, profile, 7)
                rounded = [(round(p.x), round(p.y)) for p in trajectory.points]
                planned = sum(a != b for a, b in zip(rounded, rounded[1:]))
                expected_times = {point.time_offset_ns for a, b, point in
                                  zip(rounded, rounded[1:], trajectory.points[1:]) if a != b}
                starts = []
                def attach(runtime):
                    stream = runtime._stream_profiled_mouse
                    def observe(trajectory, start, send):
                        starts.append(start)
                        return stream(trajectory, start, send)
                    runtime._stream_profiled_mouse = observe

                before = desktop.mouse_position()
                started = perf_counter_ns()
                result = run("relative", delta, 350, hid=hid, metrics=True, on_runtime=attach)
                elapsed = (perf_counter_ns() - started) / 1e6
                assert result["termination"] == "completed" and not result["errors"], result
                events = result["events"]
                assert events and all(row["inserted"] for row in events)
                origin = starts[0] if hid else events[-1]["scheduled_ns"] - trajectory.duration_ns
                observed_times = {row["scheduled_ns"] - origin for row in events}
                if hid:
                    assert tuple(map(sum, zip(*((row["dx"], row["dy"]) for row in reports)))) == delta
                    assert desktop.mouse_position() != before
                else:
                    assert desktop.mouse_position() == (before[0] + delta[0], before[1] + delta[1])
                runs.append({"channel": "HID" if hid else "SendInput", "elapsed_ms": elapsed,
                             "generated_points": len(trajectory.points), "nonzero_steps": planned,
                             "missed_sample_deadlines": len(expected_times - observed_times),
                             "reports": len(events),
                             "emit_error_ms": percentiles([(row["actual_ns"] - row["scheduled_ns"]) / 1e6
                                                            for row in events]),
                             "ack_ms": percentiles([row["ack_ms"] for row in reports]) if reports else None})
        report["median_run_ms"] = {channel: median(run["elapsed_ms"] for run in runs if run["channel"] == channel)
                                   for channel in ("SendInput", "HID")}

    def exercise():
        try:
            deadline = monotonic() + 15
            while not (desktop.focused(target) and desktop.cursor_over(target)):
                if monotonic() >= deadline:
                    raise RuntimeError("test window requires foreground focus and the cursor inside")
                sleep(0.05)
            {"monitors": cross, "focus": interrupt, "usb": interrupt, "metrics": measure}[args.mode]()
            report["pass"] = True
        except Exception:
            report["error"] = traceback.format_exc()
            traceback.print_exc()
        finally:
            args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
            root.after(0, root.quit)

    root.after(250, lambda: Thread(target=exercise, daemon=True).start())
    root.mainloop()
    root.destroy()
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
