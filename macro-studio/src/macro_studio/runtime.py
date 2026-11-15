from collections import ChainMap, deque
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from heapq import heapify, heappop, heappush
from itertools import count, takewhile

from .backend import Clock, InputBackend
from .ir import DURATION_UNIT_NS, MAX_CALL_DEPTH, MAX_DURATION_NS, MAX_REPEAT_COUNT, PARAM_PREFIX, evaluate


MAX_ACTIVE_TASKS = 256
MAX_PENDING_TASKS = 256
MAX_CUSTOM_EVENTS = 256
INSTRUCTION_BUDGET = 256
YIELD_NS = 1_000_000
SECOND_NS = 1_000_000_000
STATE_CHECK_NS = 200_000_000


class TaskFault(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InputFault(RuntimeError):
    pass


@dataclass(slots=True, eq=False)
class Task:
    handler_id: str
    priority: int = 0
    policy: str = "ignore_if_running"
    execution: Iterator[int] | None = None
    block_id: str | None = None
    held_key: str | None = None
    remaining_instructions: int = INSTRUCTION_BUDGET


@dataclass(slots=True)
class Frame:
    steps: list[dict]
    iterations: Iterator[int]
    workflow: bool = False
    loop_block_id: str | None = None
    commands: Iterator[dict] = field(default_factory=lambda: iter(()))
    caller: dict | None = None
    is_loop: bool = False

    @classmethod
    def once(cls, steps: list[dict], *, workflow: bool = False) -> "Frame":
        return cls(steps, iter(()), workflow, commands=iter(steps))


class Runtime:
    def __init__(self, program: dict, clock: Clock, backend: InputBackend) -> None:
        self.program = program
        self.clock = clock
        self.backend = backend
        self.globals = dict(program["globals"])
        self.errors: list[dict] = []
        self.missed_ticks = 0
        self._active: dict[Task, None] = {}
        self._pending: dict[str, deque[Task]] = {}
        self._owners: dict[str, Task] = {}
        self._ready: list[tuple[int, int, Task]] = []
        self._custom_events: deque[tuple[int, str]] = deque()
        self._order = count()
        self._input_failed = False
        self.termination = "completed"
        self.stop_latency_ns: int | None = None
        self.before_press: Callable[[str], None] | None = None
        self.before_block: Callable[[dict], None] | None = None
        self.read_sensor: Callable[[str], bool] | None = None
        self.read_window: Callable[[str], bool] | None = None
        self.on_transition: Callable[[str], None] | None = None
        self.machine_state: str | None = None
        self.observe_input: Callable[[int | None, int, str, bool], None] | None = None

    def run(
        self, duration_ns: int, hotkeys: list[tuple[int, str]], *,
        poll: Callable[[], tuple[str | None, list[str]]] | None = None,
    ) -> None:
        if type(duration_ns) is not int or duration_ns < 0:
            raise ValueError("duration_ns must be a nonnegative integer")
        if any(type(at) is not int or at < 0 for at, _ in hotkeys):
            raise ValueError("hotkey offsets must be nonnegative integers")

        self.globals = dict(self.program["globals"])
        self.errors.clear()
        self.missed_ticks = 0
        self._input_failed = False
        self.termination = "completed"
        self.stop_latency_ns = None
        self.machine_state = None
        if duration_ns == 0:
            return

        start = self.clock.now_ns
        cutoff = start + duration_ns
        handlers: dict[tuple[str, str | None], list[dict]] = {}
        intervals: dict[int, list[tuple[int, dict]]] = {}
        for order, handler in enumerate(self.program["handlers"]):
            if handler.get("enabled", True):
                event = handler["event"]
                if event["type"] == "interval":
                    intervals.setdefault(event["period_ns"], []).append((order, handler))
                    continue
                event_key = (event["type"], event.get("name") if event["type"] == "custom" else event.get("hotkey"))
                handlers.setdefault(event_key, []).append(handler)
        for matching in handlers.values():
            matching.sort(key=lambda handler: -handler.get("priority", 0))

        events = iter(sorted(hotkeys, key=lambda event: event[0]))
        next_event = next(events, None)
        machine = self.program.get("state_machine")
        transitions = machine["transitions"] if machine else []
        checks_conditions = any("hotkey" not in edge for edge in transitions)
        next_check = start + STATE_CHECK_NS
        ticks = [(start + period, period) for period in intervals]
        heapify(ticks)
        keep_open = bool(transitions or intervals) or any(kind == "hotkey_pressed" for kind, _ in handlers)
        paused = False
        stopped_at = None

        try:
            if machine:
                self._enter_state(machine["initial"])
            self._dispatch(handlers.get(("project_start", None), []))
            while not self._input_failed and (self._ready or self._custom_events or keep_open or paused):
                if poll is not None:
                    request, signals = poll()
                    if request == "stop":
                        stopped_at = self.clock.now_ns
                        self.termination = "stopped"
                        break
                    if request == "pause" and not paused:
                        paused = True
                        self._cancel_tasks()
                        if self._input_failed:
                            break
                    if request == "resume" and paused:
                        paused = False
                        now = self.clock.now_ns
                        ticks = [(now + period - (now - start) % period, period) for period in intervals]
                        heapify(ticks)
                    if not paused:
                        for signal in signals:
                            self._transition(transitions, signal)
                            if self._input_failed:
                                break
                            self._dispatch(handlers.get(("hotkey_pressed", signal), []))
                            if self._input_failed:
                                break
                    if self._input_failed or not (self._ready or self._custom_events or keep_open or paused):
                        break
                event_at = start + next_event[0] if next_event else cutoff
                task_at = self._ready[0][0] if self._ready else cutoff
                state_at = next_check if checks_conditions and not paused else cutoff
                interval_at = ticks[0][0] if ticks and not paused else cutoff
                custom_at = self._custom_events[0][0] if self._custom_events else cutoff
                deadline = min(event_at, task_at, state_at, interval_at, custom_at, cutoff)
                waiting = deadline > self.clock.now_ns
                self._advance_to(deadline)
                if self.clock.now_ns >= cutoff:
                    stopped_at = cutoff
                    self.termination = "duration"
                    break
                if self.clock.now_ns < deadline or (poll is not None and waiting):
                    continue
                if next_event is not None and event_at == deadline:
                    if not paused:
                        self._transition(transitions, next_event[1])
                        if not self._input_failed:
                            self._dispatch(handlers.get(("hotkey_pressed", next_event[1]), []))
                    next_event = next(events, None)
                elif state_at == deadline:
                    next_check = self.clock.now_ns + STATE_CHECK_NS
                    self._transition(transitions, None)
                elif interval_at == deadline:
                    now = self.clock.now_ns
                    due = []
                    while ticks and ticks[0][0] <= now:
                        at, period = heappop(ticks)
                        elapsed = (now - at) // period
                        heappush(ticks, (at + (elapsed + 1) * period, period))
                        due.extend((at + elapsed * period, -handler["priority"], order, handler)
                                   for order, handler in intervals[period])
                    self._dispatch([handler for _, _, _, handler in sorted(due)])
                elif custom_at == deadline:
                    _, name = self._custom_events.popleft()
                    self._dispatch(handlers.get(("custom", name), []))
                elif self._ready:
                    _, _, task = heappop(self._ready)
                    self._resume(task)
        except KeyboardInterrupt:
            stopped_at = self.clock.now_ns
            self.termination = "stopped"
        finally:
            self._cancel_tasks()
            for task in list(self._owners.values()):
                self._cleanup_key(task)
            if self._input_failed:
                self.termination = "input_error"
            if stopped_at is not None:
                self.stop_latency_ns = self.clock.now_ns - stopped_at

    def _enter_state(self, name: str) -> None:
        self._preempt([task for task in self._active if task.handler_id == ""])
        if self._input_failed:
            return
        self.machine_state = name
        if self.on_transition is not None:
            self.on_transition(name)
        state = self.program["state_machine"]["states"][name]
        workflow = state.get("workflow_id")
        if workflow:
            task = Task("", block_id=state.get("block_id"))
            task.execution = self._execute(task, self.program["workflows"][workflow]["steps"])
            self._start_task(task)

    def _transition(self, transitions: list[dict], hotkey: str | None) -> None:
        for edge in transitions:
            if edge["from"] != self.machine_state or edge.get("hotkey") != hotkey:
                continue
            try:
                matches = self._evaluate(edge.get("guard", True))
            except (ArithmeticError, ValueError) as error:
                self._report(Task("", block_id=edge.get("block_id")), "transition_error", error)
                continue
            if matches:
                self._enter_state(edge["to"])
                return

    def _cancel_tasks(self) -> None:
        self._ready.clear()
        self._custom_events.clear()
        self._pending.clear()
        for task in tuple(self._active):
            self._drop(task)

    def _advance_to(self, deadline: int) -> None:
        if deadline > self.clock.now_ns:
            self.clock.advance_to(deadline)

    def _dispatch(self, matching: list[dict]) -> None:
        for handler in matching:
            task = Task(handler["id"], handler.get("priority", 0), handler["policy"],
                        block_id=handler.get("block_id"))
            if any(active.policy == "override" and active.priority > task.priority
                   for active in self._active):
                continue
            try:
                if not self._guard_matches(handler):
                    continue
            except (ArithmeticError, ValueError) as error:
                self._report(task, "guard_error", error)
                continue

            steps = self.program["workflows"][handler["workflow_id"]]["steps"]
            task.execution = self._execute(task, steps)
            existing = [active for active in self._active if active.handler_id == task.handler_id]
            if existing and task.policy == "ignore_if_running":
                continue
            if existing and task.policy == "queue":
                if sum(map(len, self._pending.values())) >= MAX_PENDING_TASKS:
                    self._report(task, "queue_limit", ValueError("pending task limit reached"))
                else:
                    self._pending.setdefault(task.handler_id, deque()).append(task)
                continue
            if task.policy == "override":
                existing = [active for active in self._active
                            if active.handler_id == task.handler_id or active.priority < task.priority]
            if task.policy in ("restart", "override"):
                self._preempt(existing)
                if self._input_failed:
                    return
            self._start_task(task)

    def _start_task(self, task: Task) -> None:
        if len(self._active) >= MAX_ACTIVE_TASKS:
            self._report(task, "task_limit", ValueError("too many active tasks"))
            return
        self._active[task] = None
        self._schedule(task, self.clock.now_ns)

    def _preempt(self, tasks: list[Task]) -> None:
        cancelled = set(tasks)
        self._ready = [entry for entry in self._ready if entry[2] not in cancelled]
        heapify(self._ready)
        for task in tasks:
            self._pending.pop(task.handler_id, None)
            self._drop(task)

    def _guard_matches(self, handler: dict) -> bool:
        return "guard" not in handler or self._evaluate(handler["guard"])

    def _evaluate(self, expression, parameters=None):
        variables = ChainMap(parameters, self.globals) if parameters else self.globals
        return evaluate(expression, variables, self.read_sensor, self.read_window)

    def _schedule(self, task: Task, deadline: int) -> None:
        heappush(self._ready, (deadline, next(self._order), task))

    def _resume(self, task: Task) -> None:
        try:
            assert task.execution is not None
            deadline = next(task.execution)
        except StopIteration:
            self._finish(task)
        except InputFault as error:
            self._report_input_failure(task, error)
        except (ArithmeticError, ValueError) as error:
            code = error.code if isinstance(error, TaskFault) else "execution_error"
            self._report(task, code, error)
            self._finish(task)
        else:
            self._schedule(task, deadline)

    def _drop(self, task: Task) -> None:
        del self._active[task]
        self._cleanup_key(task)

    def _finish(self, task: Task) -> None:
        self._drop(task)
        pending = self._pending.get(task.handler_id)
        if pending and not self._input_failed:
            next_task = pending.popleft()
            if not pending:
                del self._pending[task.handler_id]
            self._start_task(next_task)

    def _cleanup_key(self, task: Task) -> None:
        if task.held_key is not None:
            try:
                self._release(task)
            except InputFault as error:
                self._report_input_failure(task, error)

    def _report_input_failure(self, task: Task, error: InputFault) -> None:
        self._input_failed = True
        self._report(task, "input_error", error)

    def _report(self, task: Task, code: str, error: Exception) -> None:
        self.errors.append({
            "timestamp_ns": self.clock.now_ns,
            "handler_id": task.handler_id,
            "block_id": task.block_id,
            "code": code,
            "message": str(error),
        })

    def _checkpoint(self, task: Task) -> Iterator[int]:
        task.remaining_instructions -= 1
        if task.remaining_instructions == 0:
            task.remaining_instructions = INSTRUCTION_BUDGET
            yield self.clock.now_ns + YIELD_NS

    def _execute(self, task: Task, steps: list[dict]) -> Iterator[int]:
        frames = [Frame.once(steps, workflow=True)]
        scopes = [{}]
        while frames:
            frame = frames[-1]
            step = next(frame.commands, None)
            if step is None:
                if next(frame.iterations, None) is None:
                    frames.pop()
                    if frame.workflow:
                        scopes.pop()
                        if frame.caller is not None:
                            workflow = self.program["workflows"][frame.caller["workflow_id"]]
                            if workflow.get("return_type") is not None:
                                task.block_id = frame.caller.get("block_id")
                                raise TaskFault("missing_return", "workflow ended without returning a value")
                else:
                    task.block_id = frame.loop_block_id
                    yield from self._checkpoint(task)
                    frame.commands = iter(frame.steps)
                continue

            task.block_id = step.get("block_id")
            yield from self._checkpoint(task)
            if self.before_block is not None:
                self.before_block(step)
            scope = scopes[-1]
            match step["op"]:
                case "wait":
                    duration_ns = step.get("duration_ns")
                    if "duration" in step:
                        duration = step["duration"]
                        value = self._evaluate(duration["value"], scope)
                        unit_ns = DURATION_UNIT_NS[duration["unit"]]
                        if not 0 <= value <= MAX_DURATION_NS // unit_ns:
                            raise TaskFault("wait_duration", "wait duration must be between 0 and 24 hours")
                        duration_ns = round(value * unit_ns)
                    yield self.clock.now_ns + duration_ns
                case "emit_event":
                    if len(self._custom_events) >= MAX_CUSTOM_EVENTS:
                        raise TaskFault("event_queue_limit", "custom event queue limit reached")
                    self._custom_events.append((self.clock.now_ns + YIELD_NS, step["name"]))
                case "set":
                    self.globals[step["var"]] = self._evaluate(step["value"], scope)
                case "if":
                    condition = self._evaluate(step["condition"], scope)
                    branch = step["then"] if condition else step.get("else", [])
                    frames.append(Frame.once(branch))
                case "repeat" | "forever" | "while":
                    frames.append(self._loop_frame(step, scope))
                case "break" | "continue":
                    while not frames[-1].is_loop:
                        frames.pop()
                    if step["op"] == "break":
                        frames.pop()
                    else:
                        frames[-1].commands = iter(())
                case "call":
                    if len(scopes) >= MAX_CALL_DEPTH:
                        raise TaskFault("call_depth", "workflow call depth exceeded")
                    workflow = self.program["workflows"][step["workflow_id"]]
                    args = [self._evaluate(arg, scope) for arg in step.get("args", ())]
                    scopes.append({PARAM_PREFIX + param["name"]: arg
                                   for param, arg in zip(workflow.get("params", ()), args)})
                    callee = Frame.once(workflow["steps"], workflow=True)
                    callee.caller = step
                    frames.append(callee)
                case "return":
                    value = self._evaluate(step["value"], scope)
                    while not frames[-1].workflow:
                        frames.pop()
                    caller = frames.pop().caller
                    scopes.pop()
                    assert caller is not None
                    if "result" in caller:
                        self.globals[caller["result"]] = value
                    if "then" in caller:
                        frames.append(Frame.once(caller["then"] if value else caller["else"]))
                case "end":
                    return
                case "key_tap" | "mouse_click":
                    pressed_at = self.clock.now_ns
                    key = step["key"] if step["op"] == "key_tap" else "mouse:" + step["button"]
                    self._press(task, key, pressed_at)
                    release_at = self.clock.now_ns + step["hold_ns"]
                    yield release_at
                    self._release(task, release_at)
                case "tap_sequence":
                    yield from self._tap_sequence(task, step)

    def _loop_frame(self, step: dict, parameters: dict) -> Frame:
        if step["op"] == "forever":
            iterations = count()
        elif step["op"] == "while":
            iterations = takewhile(lambda _: self._evaluate(step["condition"], parameters), count())
        else:
            repeats = self._evaluate(step["count"], parameters)
            if type(repeats) is not int or not 0 <= repeats <= MAX_REPEAT_COUNT:
                raise TaskFault("repeat_count", "repeat count must be an integer within the allowed range")
            iterations = iter(range(repeats))

        return Frame(step["steps"], iterations, loop_block_id=step.get("block_id"), is_loop=True)

    def _tap_sequence(self, task: Task, step: dict) -> Iterator[int]:
        origin = self.clock.now_ns
        frequency = step["frequency_hz"]
        hold_ns = step["hold_ns"]
        count = step["count"]
        tick = 0
        while tick < count:
            press_at = origin + tick * SECOND_NS // frequency
            if press_at > self.clock.now_ns:
                yield press_at
            release_at = press_at + hold_ns
            if self.clock.now_ns >= release_at:
                missed_until = self.clock.now_ns - origin - hold_ns
                elapsed_ticks = ((missed_until + 1) * frequency + SECOND_NS - 1) // SECOND_NS
                next_tick = min(count, elapsed_ticks)
                self.missed_ticks += next_tick - tick
                tick = next_tick
                continue
            self._press(task, step["key"], press_at)
            yield release_at
            self._release(task, release_at)
            tick += 1

    def _press(self, task: Task, key: str, scheduled: int) -> None:
        if key in self._owners:
            raise TaskFault("input_conflict", f"input is already held: {key}")
        if self.before_press is not None:
            self.before_press(key)
        if key.startswith("mouse:"):
            self._send(key[6:], "mouse_down", scheduled, self.backend.send_mouse_down)
        else:
            self._send(key, "key_down", scheduled, self.backend.send_key_down)
        self._owners[key] = task
        task.held_key = key

    def _release(self, task: Task, scheduled: int | None = None) -> None:
        key = task.held_key
        assert key is not None
        if key.startswith("mouse:"):
            self._send(key[6:], "mouse_up", scheduled, self.backend.send_mouse_up)
        else:
            self._send(key, "key_up", scheduled, self.backend.send_key_up)
        del self._owners[key]
        task.held_key = None

    def _send(self, key: str, action: str, scheduled: int | None, send: Callable) -> None:
        actual = self.clock.now_ns
        inserted = False
        try:
            send(key)
            inserted = True
        except Exception as error:
            raise InputFault(f"{action} failed for {key}: {error}") from error
        finally:
            if self.observe_input is not None:
                self.observe_input(scheduled, actual, action, inserted)
