"""Measure trajectory generation and synchronous Qt redraw without real input."""

import argparse
from dataclasses import replace
import json
from pathlib import Path
from statistics import median
from time import perf_counter_ns

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from macro_studio.gui import STYLE
from macro_studio.mouse_motion import DEFAULT_PROFILE, MotionGenerator
from macro_studio.mouse_motion_preview import MotionPreviewDialog


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    app = QApplication(["motion-performance"])
    app.setStyleSheet(STYLE)
    dialog = MotionPreviewDialog(None, DEFAULT_PROFILE)
    dialog.show()
    rows = []
    for _ in range(5):
        for duration, hz in ((350, 200), (2000, 1000), (60000, 1000)):
            profile = replace(DEFAULT_PROFILE, sampling_hz=hz)
            started = perf_counter_ns()
            expected = MotionGenerator.generate((100, 100), (800, 500), profile, 7, duration)
            generator_ms = (perf_counter_ns() - started) / 1e6

            previous = dialog.trajectory
            dialog.duration.setText(str(duration))
            dialog.sampling.setText(str(hz))
            dialog.request()
            elapsed = [perf_counter_ns()]
            gaps_ms = []
            loop = QEventLoop()
            timer = QTimer(interval=10)

            def check():
                now = perf_counter_ns()
                gaps_ms.append((now - elapsed[0]) / 1e6)
                elapsed[0] = now
                if dialog.trajectory is not previous:
                    loop.quit()

            timer.timeout.connect(check)
            timer.start()
            deadline = QTimer(singleShot=True, interval=4000)
            deadline.timeout.connect(loop.quit)
            deadline.start()
            loop.exec()
            timer.stop()
            deadline.stop()
            assert dialog.trajectory is not previous and dialog.trajectory == expected
            dialog.path_plot.cache = dialog.speed_plot.cache = None
            started = perf_counter_ns()
            shot = dialog.grab()
            render_ms = (perf_counter_ns() - started) / 1e6
            assert not shot.isNull() and dialog.path_plot.cache and dialog.speed_plot.cache
            rows.append({"points": len(expected.points), "generation_ms": generator_ms,
                         "render_ms": render_ms, "maximum_timer_gap_ms": max(gaps_ms)})
    summaries = {}
    for points in sorted({row["points"] for row in rows}):
        matching = [row for row in rows if row["points"] == points]
        summaries[points] = {key: {"median": median(row[key] for row in matching),
                                  "min": min(row[key] for row in matching),
                                  "max": max(row[key] for row in matching)}
                            for key in ("generation_ms", "render_ms", "maximum_timer_gap_ms")}
    args.report.write_text(json.dumps({"runs": rows, "summary": summaries,
                                      "qt_platform": app.platformName()}, indent=2), encoding="utf-8")
    dialog.close()


if __name__ == "__main__":
    main()
