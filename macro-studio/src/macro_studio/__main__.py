import argparse
import json
from pathlib import Path
import os
import sys

from .backend import FakeInputBackend, VirtualClock
from .ir import DEFAULT_STOP_HOTKEY, MAX_DURATION_NS, ValidationError, normalize_hotkey, validate_program
from .output import DESKTOP_CAPABILITIES, FAKE_PROFILE, OutputKind, OutputRoute, OutputRouter, RunConfig
from .runtime import Runtime

NS_PER_MS = 1_000_000
MAX_IR_BYTES = 4 * 1024 * 1024


def _ms(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected whole milliseconds") from exc
    if not 0 <= value <= MAX_DURATION_NS // NS_PER_MS:
        raise argparse.ArgumentTypeError("time must be between 0 and 86400000 ms")
    return value * NS_PER_MS


def _hotkey(text: str) -> tuple[int, str]:
    delay, separator, key = text.partition(":")
    if not separator:
        raise argparse.ArgumentTypeError("expected MS:HOTKEY")
    try:
        return _ms(delay), normalize_hotkey(key)
    except ValidationError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _load(path: Path) -> dict:
    with path.open("rb") as source:
        data = source.read(MAX_IR_BYTES + 1)
    if len(data) > MAX_IR_BYTES:
        raise ValidationError("IR file exceeds 4 MiB")
    return validate_program(json.loads(data.decode("utf-8-sig")))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run Macro Studio IR in virtual time")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("validate").add_argument("path", type=Path)
    run = commands.add_parser("run")
    run.add_argument("path", type=Path)
    run.add_argument("--duration-ms", required=True, type=_ms)
    run.add_argument("--hotkey", metavar="MS:HOTKEY", action="append", type=_hotkey, default=[])
    commands.add_parser("windows")
    arm = commands.add_parser("arm")
    arm.add_argument("path", type=Path)
    arm.add_argument("--confirm-real-input", action="store_true", required=True)
    commands.add_parser("disarm").add_argument("path", type=Path)
    live = commands.add_parser("start")
    live.add_argument("path", type=Path)
    bench = commands.add_parser("benchmark")
    bench.add_argument("--frequency", type=int, choices=(100, 200), required=True)
    bench.add_argument("--count", type=int, default=100)
    bench.add_argument("--confirm-real-input", action="store_true", required=True)
    for command in (live, bench):
        command.add_argument("--target", type=lambda value: int(value, 0), required=True)
        command.add_argument("--duration-ms", type=_ms, required=True)
        command.add_argument("--stop-hotkey", default=DEFAULT_STOP_HOTKEY)
    args = parser.parse_args(argv)

    if args.command not in ("run", "validate"):
        try:
            return _windows_command(args)
        except (OSError, ValueError, RuntimeError, RecursionError) as exc:
            print(f"Windows session: {exc}", file=sys.stderr)
            return 2
        except KeyboardInterrupt:
            return 130

    try:
        program = _load(args.path)
    except (OSError, ValueError, RecursionError) as exc:
        print(f"Invalid IR: {exc}", file=sys.stderr)
        return 2
    if args.command == "validate":
        print("Valid IR v1")
        return 0

    clock = VirtualClock()
    backend = FakeInputBackend(clock)
    router = OutputRouter({OutputKind.FAKE: OutputRoute(DESKTOP_CAPABILITIES, lambda: backend)})
    session = router.bind(program, RunConfig(FAKE_PROFILE))
    runtime = Runtime(program, clock, session.backend)
    runtime.run(args.duration_ms, args.hotkey)
    result = {
        "events": backend.events,
        "globals": runtime.globals,
        "errors": runtime.errors,
        "missed_ticks": runtime.missed_ticks,
    }
    print(json.dumps(result, indent=2, allow_nan=False))
    return int(bool(runtime.errors))


def _windows_command(args) -> int:
    from .benchmark import make_program, summarize
    from .live import ArmStore, run_live
    from .win32 import WindowsDesktop

    if os.name != "nt":
        raise ValueError("this command requires Windows")
    if args.command == "windows":
        print(json.dumps(WindowsDesktop().windows(), indent=2))
        return 0
    if args.command == "benchmark":
        program = make_program(args.frequency, args.count, "A")
    else:
        program = _load(args.path)
        store = ArmStore.local()
        if args.command in ("arm", "disarm"):
            store.set(args.path, program, args.command == "arm")
            print("ARM saved" if args.command == "arm" else "ARM cleared")
            return 0
        if not store.armed(args.path, program):
            raise ValueError("project requires explicit ARM before real input")
    if args.duration_ms == 0:
        raise ValueError("real-input duration must be positive")
    result = run_live(
        program, WindowsDesktop(), args.target, args.duration_ms,
        stop_hotkey=normalize_hotkey(args.stop_hotkey), metrics=args.command == "benchmark",
        status=lambda message: print(message, file=sys.stderr),
    )
    if args.command == "benchmark":
        report = summarize(args.frequency, result.pop("events"), result["missed_ticks"],
                           result.get("stop_latency_ns"), elapsed_ns=result.get("elapsed_ns"))
        result["benchmark"] = report
    else:
        del result["events"]
    print(json.dumps(result, indent=2))
    return int(bool(result["errors"]))


if __name__ == "__main__":
    raise SystemExit(main())
