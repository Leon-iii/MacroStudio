import json
from io import FileIO
from queue import Empty, Full, Queue
import sys
from threading import Thread
from time import perf_counter_ns, sleep

from .backend import FakeInputBackend
from .live import DeadlineClock, run_live
from .runtime import Runtime
from .sensors import ScreenSensors, sensor_specs


def emit(kind, **data):
    print(json.dumps({"kind": kind, **data}, ensure_ascii=True), flush=True)


class DryBackend(FakeInputBackend):
    omitted = 0

    def record(self, action, key):
        if len(self.events) < 24:
            self.events.append((action, key))
        else:
            self.omitted += 1

    def send_key_down(self, key):
        self.record("누름", key)

    def send_key_up(self, key):
        self.record("해제", key)

    def send_mouse_down(self, button):
        self.record("누름", f"마우스 {button}")

    def send_mouse_up(self, button):
        self.record("해제", f"마우스 {button}")

    def flush(self):
        if self.events or self.omitted:
            emit("inputs", events=self.events, omitted=self.omitted)
            self.events.clear()
            self.omitted = 0


class DebugClock(DeadlineClock):
    offset_ns = 0

    @property
    def now_ns(self):
        return perf_counter_ns() - self.offset_ns


def debugger(clock, command, flush, state):
    stepping = False

    def before_block(step):
        nonlocal stepping
        if not step.get("block_id") or not (stepping or step.get("breakpoint")):
            return
        started = perf_counter_ns()
        flush()
        emit("breakpoint", block_id=step["block_id"])
        try:
            while True:
                request = command()
                if request == "stop":
                    raise KeyboardInterrupt
                if request in ("step", "continue"):
                    stepping = request == "step"
                    state("RUNNING")
                    return
                sleep(0.01)
        finally:
            clock.offset_ns += perf_counter_ns() - started

    return before_block


def main():
    source = FileIO(sys.stdin.fileno(), closefd=False)
    setup = source.readline(4 * 1024 * 1024)
    commands = Queue(maxsize=128)
    last_seen = perf_counter_ns()
    closed = False
    runtime = None
    reported = 0
    counts = dict(pressed=0, released=0)
    next_update = last_seen
    sensors = None
    sensor_errors = Queue(maxsize=32)

    def report_sensor(key, message):
        try:
            sensor_errors.put_nowait((key, message))
        except Full:
            pass

    def attach(engine):
        nonlocal runtime
        runtime = engine
        engine.on_transition = lambda name: emit("transition", name=name)
        if sensors is not None:
            engine.read_sensor = sensors.read

        def observe(scheduled, actual, action, inserted):
            if inserted:
                counts["pressed" if action.endswith("_down") else "released"] += 1

        engine.observe_input = observe

    def publish():
        nonlocal reported
        for _ in range(32):
            try:
                key, message = sensor_errors.get_nowait()
            except Empty:
                break
            emit("error", block_id=key, message=f"화면 센서 · {message}")
        if runtime is None:
            return
        for error in runtime.errors[reported:]:
            emit("error", **error)
        reported = len(runtime.errors)
        emit("stats", **counts, errors=reported, missed=runtime.missed_ticks)

    def read_commands():
        nonlocal last_seen, closed
        try:
            for line in source:
                if line == b"heartbeat\n":
                    last_seen = perf_counter_ns()
                else:
                    commands.put_nowait(line.strip().decode("ascii"))
        finally:
            closed = True

    def command():
        nonlocal next_update
        now = perf_counter_ns()
        if now >= next_update:
            publish()
            next_update = now + 250_000_000
        if closed or now - last_seen > 1_000_000_000:
            emit("log", message="편집기 연결이 끊겨 실행을 정지합니다.")
            return "stop"
        try:
            return commands.get_nowait()
        except Empty:
            return None

    def state(value):
        emit("state", state=value)

    try:
        config = json.loads(setup)
        program = config["program"]
        Thread(target=read_commands, daemon=True).start()
        specs = sensor_specs(program)
        if specs:
            sensors = ScreenSensors(
                specs, config.get("project_directory", "."), report_sensor,
            )
            sensors.__enter__()
        duration = 86_400_000_000_000
        if config["target"]:
            from .win32 import WindowsDesktop

            result = run_live(program, WindowsDesktop(), config["target"], duration,
                              status=lambda message: emit("log", message=message),
                              control=command, on_state=state, on_runtime=attach)
            errors = result["errors"]
        else:
            debugging = config.get("debug") is True
            clock = DebugClock() if debugging else DeadlineClock()
            paused = False
            backend = DryBackend(clock)
            runtime = Runtime(program, clock, backend)
            if config.get("query_target"):
                from .win32 import WindowsDesktop

                desktop = WindowsDesktop()
                try:
                    target = desktop.target(config["query_target"])
                except ValueError:
                    emit("log", message="선택한 대상 창이 닫혔습니다.")
                else:
                    runtime.read_window = lambda state: desktop.window_state(target, state)
            attach(runtime)
            if debugging:
                runtime.before_block = debugger(clock, command, backend.flush, state)
            next_log = perf_counter_ns() + 250_000_000
            state("RUNNING")

            def poll():
                nonlocal paused, next_log
                if perf_counter_ns() >= next_log:
                    backend.flush()
                    next_log = perf_counter_ns() + 250_000_000
                item = command()
                if item in ("pause", "resume"):
                    paused = item == "pause"
                    state("PAUSED" if paused else "RUNNING")
                if item and item.startswith("hotkey:"):
                    return None, [] if paused else [item[7:]]
                return item, []

            runtime.run(duration, [], poll=poll)
            backend.flush()
            errors = runtime.errors
        publish()
        state("ERROR" if errors else "STOPPED")
    except (ValueError, OSError, RuntimeError, KeyError) as error:
        emit("error", message=str(error))
        state("ERROR")
    finally:
        if sensors is not None:
            sensors.__exit__()


if __name__ == "__main__":
    main()
