import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import perf_counter_ns, sleep

from .ir import DEFAULT_STOP_HOTKEY
from .output import DESKTOP_CAPABILITIES, SENDINPUT_PROFILE, OutputKind, OutputRoute, OutputRouter, RunConfig
from .runtime import Runtime, TaskFault

FOCUS_TIMEOUT_NS = 5_000_000_000
POLL_NS = 1_000_000
RESUME_HOTKEY = "CTRL+ALT+SHIFT+F10"
PAUSE_HOTKEY = "CTRL+ALT+SHIFT+F9"
PROJECT_HOTKEY_START = 4


class DeadlineClock:
    @property
    def now_ns(self) -> int:
        return perf_counter_ns()

    def advance_to(self, deadline_ns: int) -> None:
        remaining = deadline_ns - self.now_ns
        if remaining > 0:
            sleep(min(remaining, POLL_NS) / 1_000_000_000)


class ArmStore:
    def __init__(self, file: Path) -> None:
        self.file = file

    @classmethod
    def local(cls) -> "ArmStore":
        return cls(Path(os.environ["LOCALAPPDATA"]) / "MacroStudio" / "armed.json")

    def _load(self) -> dict:
        try:
            values = json.loads(self.file.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        if not isinstance(values, dict):
            raise ValueError("invalid local ARM settings")
        return values

    def armed(self, path: Path, program: dict) -> bool:
        return self._load().get(os.path.normcase(str(path.resolve()))) == program["project_id"]

    def set(self, path: Path, program: dict, armed: bool) -> None:
        values = self._load()
        key = os.path.normcase(str(path.resolve()))
        if armed:
            values[key] = program["project_id"]
        elif values.get(key) == program["project_id"]:
            del values[key]
        self.file.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=self.file.parent, delete=False, mode="w", encoding="utf-8") as file:
            temporary = Path(file.name)
            json.dump(values, file)
        try:
            temporary.replace(self.file)
        finally:
            temporary.unlink(missing_ok=True)


def _keys(steps):
    for step in steps:
        if step["op"] in ("key_tap", "tap_sequence"):
            yield step["key"]
        for branch in ("steps", "then", "else"):
            yield from _keys(step.get(branch, ()))


def run_live(program: dict, desktop, hwnd: int, duration_ns: int, *,
             stop_hotkey: str = DEFAULT_STOP_HOTKEY, metrics: bool = False,
             status=print, clock=None, control=None, on_state=None, on_runtime=None) -> dict:
    clock = DeadlineClock() if clock is None else clock
    router = OutputRouter({OutputKind.SENDINPUT: OutputRoute(DESKTOP_CAPABILITIES, lambda: desktop)})
    session = router.bind(program, RunConfig(SENDINPUT_PROFILE, dry_run=False, armed=True))
    target = desktop.target(hwnd)
    if stop_hotkey in (RESUME_HOTKEY, PAUSE_HOTKEY):
        raise ValueError("emergency stop conflicts with a session control hotkey")
    mapping = {1: stop_hotkey, 2: RESUME_HOTKEY, 3: PAUSE_HOTKEY}
    handlers = [handler for handler in program["handlers"] if handler["enabled"]]
    keys = [handler["event"]["hotkey"] for handler in handlers
            if handler["event"]["type"] == "hotkey_pressed"]
    machine = program.get("state_machine", {})
    keys.extend(edge["hotkey"] for edge in machine.get("transitions", []) if "hotkey" in edge)
    for index, hotkey in enumerate(dict.fromkeys(keys), start=PROJECT_HOTKEY_START):
        if hotkey in (stop_hotkey, RESUME_HOTKEY, PAUSE_HOTKEY):
            raise ValueError(f"project hotkey conflicts with session controls: {hotkey}")
        if "+" not in hotkey:
            raise ValueError(f"Windows project hotkeys require a modifier: {hotkey}")
        mapping[index] = hotkey
    if "+" not in stop_hotkey:
        raise ValueError("emergency stop hotkey requires a modifier")

    rows = []
    paused = False
    resume_until = None
    pause_pending = False

    with desktop.hotkeys(mapping) as failed:
        if 1 in failed:
            raise ValueError(f"could not register emergency stop hotkey {stop_hotkey}")
        for index in failed:
            status(f"Hotkey unavailable: {mapping[index]}")
        registered = {index: key for index, key in mapping.items() if index not in failed}
        registered_keys = tuple(registered.values())
        for workflow in program["workflows"].values():
            for key in set(_keys(workflow["steps"])):
                if desktop.unmodified_hotkey_conflict(key, registered_keys):
                    raise ValueError(f"synthetic input conflicts with a registered hotkey: {key}")
        available = {key for index, key in registered.items() if index >= PROJECT_HOTKEY_START}
        live_program = {**program, "handlers": [
            handler for handler in handlers
            if handler["event"]["type"] != "hotkey_pressed" or handler["event"]["hotkey"] in available
        ]}

        deadline = clock.now_ns + FOCUS_TIMEOUT_NS
        status(f"Switch to window {hwnd} within 5 seconds. Emergency stop: {stop_hotkey}")
        while True:
            if (control and control() == "stop") or 1 in desktop.poll_hotkeys():
                return {"termination": "stopped", "events": [], "errors": [], "missed_ticks": 0,
                        "stop_latency_ns": None, "elapsed_ns": None}
            if not desktop.alive(target):
                raise ValueError("selected window closed before the session started")
            if desktop.focused(target):
                break
            if clock.now_ns >= deadline:
                raise ValueError("target did not become foreground within 5 seconds")
            clock.advance_to(deadline)

        if on_state:
            on_state("RUNNING")

        def poll() -> tuple[str | None, list[str]]:
            nonlocal paused, resume_until, pause_pending
            request = control() if control else None
            signals = desktop.poll_hotkeys()
            if request == "stop" or 1 in signals or not desktop.alive(target):
                return "stop", []
            if not paused and (request == "pause" or 3 in signals or pause_pending or not desktop.focused(target)):
                pause_pending = False
                paused = True
                if on_state:
                    on_state("PAUSED")
                status(f"Paused. Resume: {RESUME_HOTKEY}. Emergency stop: {stop_hotkey}")
                return "pause", []
            if paused:
                if request == "resume" or 2 in signals:
                    resume_until = clock.now_ns + FOCUS_TIMEOUT_NS
                if resume_until is not None:
                    if clock.now_ns >= resume_until:
                        resume_until = None
                    elif desktop.focused(target):
                        paused = False
                        resume_until = None
                        if on_state:
                            on_state("RUNNING")
                        return "resume", []
                return None, []
            return None, [
                registered[index] for index in signals
                if index >= PROJECT_HOTKEY_START and index in registered
            ]

        def before_press(key: str) -> None:
            nonlocal pause_pending
            mouse = key.startswith("mouse:")
            if not mouse and desktop.hotkey_conflict(key, registered_keys):
                raise TaskFault("hotkey_conflict", "synthetic input would trigger a registered hotkey")
            if not desktop.focused(target):
                pause_pending = True
                raise TaskFault("focus_lost", "target is no longer in the foreground")
            if not mouse and desktop.modifiers_pressed():
                raise TaskFault("keyboard_modifiers_held", "Ctrl, Alt, Shift 또는 Win 키를 놓아 주세요.")
            if mouse and not desktop.cursor_over(target):
                raise TaskFault("pointer_outside_target", "mouse pointer must be over the target window")

        def before_move(position: tuple[int, int]) -> None:
            nonlocal pause_pending
            if not desktop.focused(target):
                pause_pending = True
                raise TaskFault("focus_lost", "target is no longer in the foreground")
            if not desktop.point_over(target, position):
                raise TaskFault("pointer_outside_target", "destination must be over the target window")

        runtime = Runtime(live_program, clock, session.backend)
        runtime.before_press = before_press
        runtime.modifiers_pressed = desktop.modifiers_pressed
        runtime.before_move = before_move
        runtime.read_window = lambda state: desktop.window_state(target, state)
        if metrics:
            def record(scheduled_ns, actual_ns, action, inserted):
                rows.append(dict(scheduled_ns=scheduled_ns, actual_ns=actual_ns,
                                 action=action, inserted=inserted))
            runtime.observe_input = record
        if on_runtime:
            on_runtime(runtime)
        start = clock.now_ns
        runtime.run(duration_ns, [], poll=poll)
        return {"termination": runtime.termination, "errors": runtime.errors,
                "events": rows, "missed_ticks": runtime.missed_ticks,
                "stop_latency_ns": runtime.stop_latency_ns, "elapsed_ns": clock.now_ns - start}
