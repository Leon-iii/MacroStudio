"""Measure the editor worker's parse/lower/validate path with checked output."""

import argparse
import cProfile
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import platform
import pstats
from statistics import median
from time import perf_counter_ns, sleep

from macro_studio.python_compiler import compile_python


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    project = {"project_id": "compile-measurement", "globals": {}}
    report = {"python": platform.python_version(), "platform": platform.platform(), "logical_cpus": os.cpu_count(),
              "scope": "warm parse, lower, IR validation and fingerprint recheck; excludes UI, file I/O and input execution",
              "failures": 0, "runs": []}
    if os.name == "nt":
        def system_times():
            values = [wintypes.FILETIME() for _ in range(3)]
            if not ctypes.windll.kernel32.GetSystemTimes(*(ctypes.byref(value) for value in values)):
                raise ctypes.WinError()
            return [value.dwHighDateTime << 32 | value.dwLowDateTime for value in values]

        before = system_times()
        sleep(0.25)
        after = system_times()
        idle, kernel, user = [end - start for start, end in zip(before, after)]
        report["cpu_busy_percent_before"] = 100 * (1 - idle / max(1, kernel + user))
    for count in (10, 100, 300):
        source = "@on_start()\nasync def work():\n" + '    await keyboard.tap("A", hold_ms=10)\n' * count

        def compile_and_check():
            snapshot = compile_python(source, project)
            program = snapshot.program_for(source, project)
            steps = program["workflows"]["work"]["steps"]
            assert len(steps) == count and all(step["op"] == "key_tap" for step in steps)

        compile_and_check()
        values = []
        for _ in range(7):
            start = perf_counter_ns()
            compile_and_check()
            values.append((perf_counter_ns() - start) / 1e6)
        report["runs"].append({"input_commands": count, "source_characters": len(source), "repetitions": len(values),
                               "median_ms": median(values), "min_ms": min(values), "max_ms": max(values), "samples_ms": values})
    profile = cProfile.Profile()
    profile.runcall(compile_and_check)
    stats = pstats.Stats(profile)
    top = sorted(stats.stats.items(), key=lambda item: item[1][2], reverse=True)[:6]
    report["profile_hotspots"] = [{"function": f"{Path(key[0]).name}:{key[2]}", "self_seconds": value[2]}
                                   for key, value in top]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
