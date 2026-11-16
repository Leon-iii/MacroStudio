from bisect import bisect_right
from collections import ChainMap, deque
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass, field
from heapq import heapify, heappop, heappush
from itertools import count, takewhile
from secrets import randbits
from typing import TYPE_CHECKING

from .backend import Clock, InputBackend
from .ir import MAX_CALL_DEPTH, MAX_REPEAT_COUNT, PARAM_PREFIX, VISION_EVENTS, duration_nanoseconds, evaluate
from .mouse_motion import MotionGenerator, MouseMotionProfile
from .mouse_motion.quantizer import RelativeQuantizer, first_report

if TYPE_CHECKING:
    from .perception import PerceptionService, RecognitionResult


MAX_ACTIVE_TASKS = 256
MAX_PENDING_TASKS = 256
MAX_CUSTOM_EVENTS = 256
INSTRUCTION_BUDGET = 256
YIELD_NS = 1_000_000
SECOND_NS = 1_000_000_000
STATE_CHECK_NS = 200_000_000
POINTER_STEP_NS = 16_000_000
HID_POINTER_REPORT_NS = 8_000_000
POINTER_CORRECTION_NS = 600_000_000
POINTER_CORRECTION_STEPS = 32
KEYBOARD_RETRY_NS = 5_000_000
MODIFIER_RELEASE_TIMEOUT_NS = 2_000_000_000
VISION_POLL_NS = 10_000_000


class TaskFault(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class InputFault(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VisionObservation:
    result: "RecognitionResult"
    spec: dict
    generation: int


@dataclass(slots=True)
class Scope:
    values: dict = field(default_factory=dict)
    sources: dict[str, tuple[VisionObservation, ...]] = field(default_factory=dict)


@dataclass(slots=True, eq=False)
class Task:
    handler_id: str
    priority: int = 0
    policy: str = "ignore_if_running"
    execution: Iterator[int] | None = None
    block_id: str | None = None
    held_key: str | None = None
    remaining_instructions: int = INSTRUCTION_BUDGET
    vision_results: dict[str, VisionObservation] = field(default_factory=dict)
    vision_request: str | int | None = None
    vision_pointer: tuple[tuple[int, int], tuple[VisionObservation, ...]] | None = None


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
    def __init__(self, program: dict, clock: Clock, backend: InputBackend, *,
                 perception: "PerceptionService | None" = None, perception_now=None) -> None:
        self.program = program
        self.clock = clock
        self.backend = backend
        self.globals = dict(program["globals"])
        self.perception = perception
        self._perception_now = perception_now or (lambda: self.clock.now_ns)
        self._vision_warnings: set[str] = set()
        self._vision_generation = 0
        self._vision_handlers: dict[str | int, dict] = {}
        self._vision_event_seq: dict[str | int, int] = {}
        self._global_sources: dict[str, tuple[VisionObservation, ...]] = {}
        self.errors: list[dict] = []
        self.warnings: list[dict] = []
        self.missed_ticks = 0
        self._active: dict[Task, None] = {}
        self._pending: dict[str, deque[Task]] = {}
        self._owners: dict[str, Task] = {}
        self._pointer_owner: Task | None = None
        self._ready: list[tuple[int, int, Task]] = []
        self._custom_events: deque[tuple[int, str]] = deque()
        self._order = count()
        self._input_failed = False
        self.termination = "completed"
        self.stop_latency_ns: int | None = None
        self.before_press: Callable[[str], None] | None = None
        self.modifiers_pressed: Callable[[], bool] | None = None
        self.before_move: Callable[[tuple[int, int]], None] | None = None
        self.send_profiled_move: Callable[[tuple[int, int]], None] | None = None
        self.send_pointer_delta: Callable[[tuple[int, int]], None] | None = None
        self.send_profiled_report: Callable[[tuple[int, int]], None] | None = None
        self._next_profiled_report = 0
        self.onboard_taps = None
        self.onboard_done: Callable[[int], None] | None = None
        self.batch_done: Callable[[int], None] | None = None
        self.adaptive_device_steps: frozenset[int] | None = None
        self.on_adaptive_route: Callable[[dict, bool], None] | None = None
        self._adaptive_routes: dict[int, bool] = {}
        self._onboard_owner: Task | None = None
        self._batch_active = False
        self._batch_held: set[str] = set()
        self.before_block: Callable[[dict], None] | None = None
        self.read_sensor: Callable[[str], bool] | None = None
        self.read_sensor_at: Callable[[str, tuple[int, ...]], bool] | None = None
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
        self._global_sources.clear()
        self._vision_warnings.clear()
        self.errors.clear()
        self.warnings.clear()
        self._adaptive_routes.clear()
        self._next_profiled_report = 0
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
        vision_handlers = []
        for order, handler in enumerate(self.program["handlers"]):
            if handler.get("enabled", True):
                event = handler["event"]
                if event["type"] == "interval":
                    intervals.setdefault(event["period_ns"], []).append((order, handler))
                    continue
                if event["type"] in VISION_EVENTS:
                    vision_handlers.append(handler)
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
        keep_open = bool(transitions or intervals or (vision_handlers and self.perception is not None)) or any(
            kind == "hotkey_pressed" for kind, _ in handlers)
        paused = False
        stopped_at = None
        next_vision = start

        try:
            self._subscribe_vision(vision_handlers)
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
                        self._vision_generation += 1
                        self._cancel_tasks()
                        self._unsubscribe_vision()
                        if self._input_failed:
                            break
                    if request == "resume" and paused:
                        paused = False
                        now = self.clock.now_ns
                        self._subscribe_vision(vision_handlers)
                        next_vision = now
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
                vision_at = next_vision if self._vision_handlers and not paused else cutoff
                custom_at = self._custom_events[0][0] if self._custom_events else cutoff
                deadline = min(event_at, task_at, state_at, interval_at, vision_at, custom_at, cutoff)
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
                elif vision_at == deadline:
                    next_vision = self.clock.now_ns + VISION_POLL_NS
                    self._dispatch_vision()
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
            self._unsubscribe_vision()
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

    def _subscribe_vision(self, handlers) -> None:
        for handler in handlers:
            event = handler["event"]
            identifier = self.perception.subscribe(event["spec"]) if self.perception is not None else None
            if identifier is None:
                task = Task(handler["id"], block_id=event["block_id"])
                self._report(task, "vision_unavailable", ValueError("unable to subscribe to screen recognition"))
            else:
                self._vision_handlers[identifier] = handler

    def _unsubscribe_vision(self) -> None:
        if self.perception is not None:
            for identifier in self._vision_handlers:
                self.perception.unsubscribe(identifier)
        self._vision_handlers.clear()
        self._vision_event_seq.clear()

    def _dispatch_vision(self) -> None:
        if (message := self._vision_health_error()) is not None:
            for handler in self._vision_handlers.values():
                self._warn_vision(handler["event"], None, message)
        matching = []
        for event in self.perception.drain_events():
            identifier = event["subscription_id"]
            handler = self._vision_handlers.get(identifier)
            if handler is None or event["event_seq"] <= self._vision_event_seq.get(identifier, -1):
                continue
            self._vision_event_seq[identifier] = event["event_seq"]
            if handler["event"]["type"] == "on_" + event["type"]:
                matching.append(handler)
        self._dispatch(sorted(matching, key=lambda handler: -handler["priority"]))

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

    def _evaluate(self, expression, scope=None, task=None, sources=None):
        variables = ChainMap(scope.values, self.globals) if scope and scope.values else self.globals

        def track(observation):
            key = id(observation.spec)
            previous = sources.get(key)
            if previous is None or (observation.generation, observation.result.captured_at_ns) < (
                    previous.generation, previous.result.captured_at_ns):
                sources[key] = observation

        def on_variable(name):
            stored = scope.sources if name.startswith(PARAM_PREFIX) and scope is not None else self._global_sources
            for observation in stored.get(name, ()):
                track(observation)

        return evaluate(expression, variables, self.read_sensor, self.read_window, self.read_sensor_at,
                        vision=lambda node: self._vision_value(node, task, track if sources is not None else None),
                        on_variable=on_variable if sources is not None else None)

    def _fresh(self, observation: VisionObservation) -> bool:
        captured = observation.result.captured_at_ns
        check = getattr(self.perception, "is_current", None)
        return (observation.generation == self._vision_generation and captured is not None
                and 0 <= self._perception_now() - captured <= observation.spec.get("max_age_ms", 250) * 1_000_000
                and (check is None or check(observation.result)))

    def _vision_health_error(self):
        if self.perception is None:
            return "screen recognition is unavailable"
        health = getattr(self.perception, "health", None)
        if health is not None:
            state = health()
            return state.get("error_message") or state.get("error_code")
        return None

    def _vision_value(self, node, task, track):
        if node["op"] == "vision_visible":
            spec = node["spec"]
            result = self.perception.latest(spec) if self.perception is not None else None
            if (result is not None and result.status != "UNKNOWN" and self._vision_health_error() is None
                    and self._fresh(VisionObservation(result, spec, self._vision_generation))):
                return result.status == "FOUND"
            self._warn_vision(node, task, "recognition cache is empty, stale or unavailable")
            return False
        observation = task.vision_results.get(node["result_var"]) if task is not None else None
        result = observation.result if observation is not None else None
        fresh = observation is not None and self._fresh(observation) and self._vision_health_error() is None
        field = node["field"]
        if field == "status":
            return getattr(result.status, "value", result.status) if fresh else "UNKNOWN"
        if field == "error_code":
            if result is None:
                return "NO_RESULT"
            if result.status == "UNKNOWN":
                return result.error_code or "VISION_UNKNOWN"
            if not fresh:
                return "STALE_RESULT"
            return ""
        if result is None or not fresh or result.status == "UNKNOWN":
            raise TaskFault("vision_unknown", f"({node['block_id']}) recognition result is unavailable or stale")
        value = getattr(result, {"x": "center_x", "y": "center_y"}.get(field, field))
        if value is None:
            raise TaskFault("vision_not_found", f"({node['block_id']}) recognition result has no {field} value")
        if track is not None:
            track(observation)
        return value

    def _warn_vision(self, node, task, message):
        block_id = node["block_id"]
        if block_id not in self._vision_warnings:
            self._vision_warnings.add(block_id)
            self.warnings.append({"block_id": block_id, "handler_id": task.handler_id if task else "",
                                  "code": "vision_unknown", "message": message})

    def _cancel_vision_request(self, task):
        if task.vision_request is not None:
            self.perception.cancel(task.vision_request)
            task.vision_request = None

    def _request_vision(self, task, spec, deadline):
        if self.perception is None or (deadline is not None and self.clock.now_ns >= deadline):
            return None
        task.vision_request = self.perception.request(spec)
        if task.vision_request is None:
            return None
        while True:
            now = self.clock.now_ns
            if deadline is not None and now >= deadline:
                self._cancel_vision_request(task)
                return None
            result = self.perception.take(task.vision_request)
            if result is not None:
                task.vision_request = None
                return result
            next_poll = now + VISION_POLL_NS
            yield next_poll if deadline is None else min(deadline, next_poll)

    def _recognize(self, task, step):
        from .perception import RecognitionResult, RecognitionStatus

        spec = step["spec"]
        timeout = step["timeout_ms"]
        deadline = self.clock.now_ns + timeout * 1_000_000 if timeout is not None else None
        expected = {"appear": "FOUND", "disappear": "NOT_FOUND"}.get(step.get("condition"))
        error_code = None
        while True:
            result = yield from self._request_vision(task, spec, deadline)
            if result is None:
                error_code = "TIMEOUT" if deadline is not None and self.clock.now_ns >= deadline else "VISION_UNAVAILABLE"
                break
            observation = VisionObservation(result, spec, self._vision_generation)
            if result.status == "UNKNOWN" or not self._fresh(observation):
                if result.status != "UNKNOWN":
                    error_code = "STALE_RESULT"
                break
            if expected is None or result.status == expected:
                break
            next_scan = self.clock.now_ns + spec.get("scan_interval_ms", 33) * 1_000_000
            yield next_scan if deadline is None else min(deadline, next_scan)
        if error_code is not None:
            result = RecognitionResult(
                status=RecognitionStatus.UNKNOWN, detector_id=spec["detector"], template_id=spec.get("template_id"),
                frame_id=None, source_id=getattr(self.perception, "source_id", ""), captured_at_ns=None,
                processed_at_ns=self._perception_now(), bbox=None, center_x=None, center_y=None, score=None,
                error_code=error_code, error_message=None,
            )
        task.vision_results[step["result_var"]] = VisionObservation(result, spec, self._vision_generation)
        if result.status == "UNKNOWN" and result.error_code != "TIMEOUT":
            message = result.error_message or result.error_code or "recognition is unknown"
            if step.get("on_error") == "stop_task" or result.error_code in ("INVALID_TEMPLATE", "INVALID_ROI"):
                raise TaskFault("vision_unknown", message)
            self._warn_vision(step, task, message)

    def _pointer_destination(self, task, target, scope, *, origin=None):
        sources = {}
        x, y = (round(self._evaluate(target[axis], scope, task, sources)) for axis in ("x", "y"))
        if origin is not None:
            x, y = origin[0] + x, origin[1] + y
        if not (-32768 <= x <= 32767 and -32768 <= y <= 32767):
            raise TaskFault("invalid_position", "mouse coordinates must be between -32768 and 32767")
        if any(source.result.status != "FOUND" or not self._fresh(source) for source in sources.values()):
            raise TaskFault("vision_stale_target", "mouse destination needs a fresh recognition result")
        if sources and (message := self._vision_health_error()) is not None:
            raise TaskFault("vision_unknown", message)
        destination = (x, y)
        if self.before_move is not None:
            self.before_move(destination)
        task.vision_pointer = (destination, tuple(sources.values())) if sources else None
        return destination

    def _revalidate_click(self, task, step):
        destination, sources = task.vision_pointer
        if self._pointer_position() != destination:
            raise TaskFault("pointer_off_position", "mouse pointer moved before the click")
        if any(source.generation != self._vision_generation for source in sources):
            raise TaskFault("vision_stale_target", "click needs a fresh recognition result")
        if not step.get("revalidate_before_click", True):
            if any(not self._fresh(source) for source in sources):
                raise TaskFault("vision_stale_target", "click needs a fresh recognition result")
            if (message := self._vision_health_error()) is not None:
                raise TaskFault("vision_unknown", message)
            return
        started = self._perception_now()
        refreshed = []
        for source in sources:
            spec = source.spec
            deadline = self.clock.now_ns + min(spec.get("max_age_ms", 250), 1000) * 1_000_000
            while True:
                result = yield from self._request_vision(task, spec, deadline)
                if result is None or result.status != "FOUND" or result.bbox is None:
                    raise TaskFault("vision_revalidation_failed", "click target could not be verified")
                observation = VisionObservation(result, spec, self._vision_generation)
                if self._fresh(observation) and result.captured_at_ns >= started:
                    break
                yield min(deadline, self.clock.now_ns + VISION_POLL_NS)
            left, top, width, height = result.bbox
            x, y = destination
            if not (left <= x < left + width and top <= y < top + height):
                raise TaskFault("vision_target_moved", "click target moved after pointer positioning")
            refreshed.append(observation)
        if any(not self._fresh(source) for source in refreshed):
            raise TaskFault("vision_stale_target", "click needs a fresh recognition result")
        if (message := self._vision_health_error()) is not None:
            raise TaskFault("vision_unknown", message)
        task.vision_pointer = (destination, tuple(refreshed))

    def active_block_ids(self) -> tuple[str, ...]:
        return tuple(sorted({task.block_id for task in self._active if task.block_id is not None}))

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
        self._cancel_vision_request(task)
        if self._onboard_owner is task:
            self._onboard_owner = None
            try:
                if self.onboard_taps is not None:
                    self.onboard_taps.cancel_job()
            except (OSError, RuntimeError) as error:
                self._report_input_failure(task, InputFault(f"onboard cancel failed: {error}"))
            self._batch_active = False
            for key in sorted(self._batch_held):
                try:
                    self._batch_edge(key, False, self.clock.now_ns)
                except InputFault as error:
                    self._report_input_failure(task, error)
            self._batch_held.clear()
        if self._pointer_owner is task:
            self._pointer_owner = None
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
        scopes = [Scope()]
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
                    yield self.clock.now_ns + self._duration_ns(task, step, scope)
                case "vision_find" | "vision_wait":
                    yield from self._recognize(task, step)
                case "emit_event":
                    if len(self._custom_events) >= MAX_CUSTOM_EVENTS:
                        raise TaskFault("event_queue_limit", "custom event queue limit reached")
                    self._custom_events.append((self.clock.now_ns + YIELD_NS, step["name"]))
                case "set":
                    sources = {}
                    self.globals[step["var"]] = self._evaluate(step["value"], scope, task, sources)
                    self._global_sources[step["var"]] = tuple(sources.values())
                case "if":
                    condition = self._evaluate(step["condition"], scope, task)
                    branch = step["then"] if condition else step.get("else", [])
                    frames.append(Frame.once(branch))
                case "repeat" | "forever" | "while":
                    frames.append(self._loop_frame(task, step, scope))
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
                    callee_scope = Scope()
                    for param, arg in zip(workflow.get("params", ()), step.get("args", ())):
                        name = PARAM_PREFIX + param["name"]
                        sources = {}
                        callee_scope.values[name] = self._evaluate(arg, scope, task, sources)
                        callee_scope.sources[name] = tuple(sources.values())
                    scopes.append(callee_scope)
                    callee = Frame.once(workflow["steps"], workflow=True)
                    callee.caller = step
                    frames.append(callee)
                case "return":
                    sources = {}
                    value = self._evaluate(step["value"], scope, task, sources)
                    while not frames[-1].workflow:
                        frames.pop()
                    caller = frames.pop().caller
                    scopes.pop()
                    assert caller is not None
                    if "result" in caller:
                        self.globals[caller["result"]] = value
                        self._global_sources[caller["result"]] = tuple(sources.values())
                    if "then" in caller:
                        frames.append(Frame.once(caller["then"] if value else caller["else"]))
                case "end":
                    return
                case "mouse_move":
                    yield from self._move_pointer(task, step, scope, self._duration_ns(task, step, scope))
                case "mouse_move_timed":
                    yield from self._profiled_mouse_move(task, step, scope)
                case "key_tap" | "mouse_click" | "mouse_click_at":
                    hold_ns = self._duration_ns(task, step, scope, legacy_key="hold_ns")
                    click_position = None
                    if step["op"] == "key_tap":
                        yield from self._wait_for_modifiers()
                    if step["op"] == "mouse_click_at":
                        click_position = yield from self._move_pointer(task, step, scope, 0)
                    if step["op"] != "key_tap" and task.vision_pointer is not None:
                        yield from self._revalidate_click(task, step)
                        click_position = task.vision_pointer[0]
                    pressed_at = self.clock.now_ns
                    key = step["key"] if step["op"] == "key_tap" else "mouse:" + step["button"]
                    self._press(task, key, pressed_at, click_position=click_position)
                    release_at = self.clock.now_ns + hold_ns
                    yield release_at
                    self._release(task, release_at)
                case "tap_sequence":
                    yield from self._tap_sequence(task, step, scope)
                case "timed_batch":
                    yield from self._timed_batch(task, step)

    def _wait_for_modifiers(self) -> Iterator[int]:
        if self.modifiers_pressed is None:
            return
        deadline = self.clock.now_ns + MODIFIER_RELEASE_TIMEOUT_NS
        while self.modifiers_pressed():
            now = self.clock.now_ns
            if now >= deadline:
                raise TaskFault("keyboard_modifiers_held", "Ctrl, Alt, Shift 또는 Win 키를 놓아 주세요.")
            yield min(deadline, now + KEYBOARD_RETRY_NS)

    def _duration_ns(self, task: Task, step: dict, scope: Scope, *, legacy_key: str = "duration_ns") -> int:
        if legacy_key in step:
            return step[legacy_key]
        duration = step["duration"]
        value = self._evaluate(duration["value"], scope, task)
        try:
            nanoseconds = duration_nanoseconds(value, duration["unit"])
        except ValueError as error:
            raise TaskFault(f"{step['op']}_duration", str(error)) from error
        if legacy_key == "hold_ns" and nanoseconds == 0:
            raise TaskFault(f"{step['op']}_duration", "hold duration must be positive")
        return nanoseconds

    def _require_free_pointer(self) -> None:
        if self._batch_active or self._pointer_owner is not None or any(key.startswith("mouse:") for key in self._owners):
            raise TaskFault("input_conflict", "mouse pointer or button is already in use")

    def _profiled_mouse_move(self, task: Task, step: dict, scope: Scope) -> Iterator[int]:
        self._require_free_pointer()
        target = step["target"]
        origin = self._pointer_position()
        destination = self._pointer_destination(task, target, scope,
                                                origin=origin if target["kind"] == "relative" else None)
        motion = step["motion"]
        profile = MouseMotionProfile.from_dict(motion["profile"])
        override = motion.get("duration")
        duration = (duration_nanoseconds(self._evaluate(override["value"], scope, task), override["unit"]) / 1e6
                    if override is not None else None)
        seed = motion.get("seed_override")
        if seed is None:
            seed = 7 if profile.seed_policy == "fixed" else randbits(64)
        trajectory = MotionGenerator.generate(origin, destination, profile, seed, duration)
        if not trajectory.duration_ns:
            return

        self._pointer_owner = task
        start = self.clock.now_ns
        if self.send_profiled_report is not None:
            yield from self._stream_profiled_mouse(trajectory, start, self.send_profiled_report)
            self._pointer_owner = None
            return
        points = trajectory.points
        send = self.send_profiled_move or self.backend.send_mouse_move
        previous = origin
        index = 1
        while index < len(points):
            yield start + points[index].time_offset_ns
            elapsed = self.clock.now_ns - start
            index = bisect_right(points, elapsed, lo=index, key=lambda point: point.time_offset_ns) - 1
            point = points[index]
            position = (round(point.x), round(point.y))
            if position != previous or index == len(points) - 1:
                if self.before_move is not None:
                    self.before_move(position)
                self._send(position, "mouse_move", start + point.time_offset_ns, send)
                previous = position
            index += 1
        self._pointer_owner = None

    def _stream_profiled_mouse(self, trajectory, start, send) -> Iterator[int]:
        points = trajectory.points
        quantizer = RelativeQuantizer(trajectory.start)
        index = 1
        deadline = start + points[index].time_offset_ns
        while True:
            yield max(deadline, self._next_profiled_report)
            elapsed = self.clock.now_ns - start
            index = bisect_right(points, elapsed, lo=index, key=lambda point: point.time_offset_ns) - 1
            point = points[index]
            delta = quantizer.remaining(point)
            if delta != (0, 0):
                if self.before_move is not None:
                    self.before_move((round(point.x), round(point.y)))
                    self.before_move(self._pointer_position())
                report = first_report(delta)
                self._send(report, "mouse_move", start + point.time_offset_ns, send)
                quantizer.record(report)
                self._next_profiled_report = self.clock.now_ns + HID_POINTER_REPORT_NS
                if report != delta:
                    continue
            index += 1
            if index == len(points):
                return
            deadline = start + points[index].time_offset_ns

    def _move_pointer(self, task: Task, step: dict, scope: Scope, duration_ns: int) -> Generator[int, None, tuple[int, int]]:
        self._require_free_pointer()
        destination = self._pointer_destination(task, step["position"], scope)
        if not duration_ns:
            self._pointer_owner = task
            yield from self._finish_pointer_move(destination, self.clock.now_ns)
            self._pointer_owner = None
            return destination

        self._pointer_owner = task
        origin = self._pointer_position()
        start = self.clock.now_ns
        end = start + duration_ns
        deadline = min(end, start + POINTER_STEP_NS)
        while True:
            yield deadline
            if self.before_move is not None:
                self.before_move(destination)
            elapsed = min(duration_ns, self.clock.now_ns - start)
            if elapsed == duration_ns:
                yield from self._finish_pointer_move(destination, deadline)
                self._pointer_owner = None
                return destination
            fraction = elapsed / duration_ns
            x = round(origin[0] + (destination[0] - origin[0]) * fraction)
            y = round(origin[1] + (destination[1] - origin[1]) * fraction)
            self._send((x, y), "mouse_move", deadline, self.backend.send_mouse_move)
            deadline = min(end, start + (elapsed // POINTER_STEP_NS + 1) * POINTER_STEP_NS)

    def _finish_pointer_move(self, destination: tuple[int, int], scheduled: int) -> Iterator[int]:
        if self.send_pointer_delta is None:
            self._send(destination, "mouse_move", scheduled, self.backend.send_mouse_move)
            return

        deadline = self.clock.now_ns + POINTER_CORRECTION_NS
        limits = [127, 127]
        previous = (0, 0)
        for attempt in range(POINTER_CORRECTION_STEPS + 1):
            if self.before_move is not None:
                self.before_move(destination)
            x, y = self._pointer_position()
            delta = (destination[0] - x, destination[1] - y)
            if delta == (0, 0):
                return
            if attempt == POINTER_CORRECTION_STEPS or self.clock.now_ns >= deadline:
                break
            report = []
            for axis, error in enumerate(delta):
                if error * previous[axis] < 0:
                    limits[axis] = max(1, limits[axis] // 2)
                report.append(max(-limits[axis], min(limits[axis], error)))
            previous = delta
            self._send((report[0], report[1]), "mouse_move", self.clock.now_ns, self.send_pointer_delta)
            yield min(deadline, self.clock.now_ns + POINTER_STEP_NS)
        raise TaskFault("pointer_off_position", "mouse pointer did not reach the requested coordinates")

    def _pointer_position(self) -> tuple[int, int]:
        try:
            return self.backend.mouse_position()
        except OSError as error:
            raise InputFault(f"mouse position read failed: {error}") from error

    def _loop_frame(self, task: Task, step: dict, scope: Scope) -> Frame:
        if step["op"] == "forever":
            iterations = count()
        elif step["op"] == "while":
            iterations = takewhile(lambda _: self._evaluate(step["condition"], scope, task), count())
        else:
            repeats = self._evaluate(step["count"], scope, task)
            if type(repeats) is not int or not 0 <= repeats <= MAX_REPEAT_COUNT:
                raise TaskFault("repeat_count", "repeat count must be an integer within the allowed range")
            iterations = iter(range(repeats))

        return Frame(step["steps"], iterations, loop_block_id=step.get("block_id"), is_loop=True)

    def _use_onboard(self, step: dict) -> bool:
        if self.adaptive_device_steps is None:
            return self.onboard_taps is not None
        identifier = id(step)
        device = (identifier in self.adaptive_device_steps and len(self._active) == 1
                  and self._onboard_owner is None and self._pointer_owner is None and not self._owners)
        if self._adaptive_routes.get(identifier) is not device:
            self._adaptive_routes[identifier] = device
            if self.on_adaptive_route is not None:
                self.on_adaptive_route(step, device)
        return device

    def _tap_sequence(self, task: Task, step: dict, scope: Scope) -> Iterator[int]:
        frequency = self._sequence_integer(task, step, scope, "frequency_hz", 200)
        count = self._sequence_integer(task, step, scope, "count", MAX_REPEAT_COUNT)
        hold_ns = self._duration_ns(task, step, scope, legacy_key="hold_ns")
        if hold_ns * frequency >= SECOND_NS:
            raise TaskFault("tap_sequence_duration", "hold must be shorter than the period")
        yield from self._wait_for_modifiers()
        if self._use_onboard(step):
            yield from self._onboard_train(task, step["key"], frequency, hold_ns, count)
            return
        origin = self.clock.now_ns
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

    def _onboard_train(self, task: Task, key: str, frequency: int, hold_ns: int, count: int) -> Iterator[int]:
        if self._onboard_owner is not None or any(not item.startswith("mouse:") for item in self._owners):
            raise TaskFault("input_conflict", "keyboard is occupied by another task")
        period_us = (1_000_000 + frequency - 1) // frequency
        hold_us = (hold_ns + 999) // 1000
        if not 0 < hold_us < period_us:
            raise TaskFault("tap_sequence_duration", "onboard hold must be at least 1 microsecond and less than the period")
        if self.before_press is not None:
            self.before_press(key)
        self._onboard_owner = task
        try:
            self.onboard_taps.prepare_tap_train(key, period_us, hold_us, count)
            if self.before_press is not None:
                self.before_press(key)
            self.onboard_taps.start_tap_train()
            yield from self._await_job(count, period_us * count, self.onboard_done)
        except (OSError, RuntimeError) as error:
            raise InputFault(str(error)) from error

    def _await_job(self, count: int, duration_us: int, completed) -> Iterator[int]:
        deadline = self.clock.now_ns + duration_us * 1000 + 1_500_000_000
        while self.clock.now_ns < deadline:
            actual = self.onboard_taps.job_count()
            if actual is not None:
                self._onboard_owner = None
                self._batch_active = False
                if actual != count:
                    raise InputFault(f"onboard count mismatch: expected {count}, device reported {actual}")
                if completed is not None:
                    completed(actual)
                return
            yield min(deadline, self.clock.now_ns + 8_000_000)
        raise InputFault("onboard job completion timed out")

    def _timed_batch(self, task: Task, step: dict) -> Iterator[int]:
        if self._onboard_owner is not None or self._pointer_owner is not None or self._owners:
            raise TaskFault("input_conflict", "another task owns an input needed by the timed batch")
        events = step["events"]
        if task.vision_pointer is not None and any(event["action"] == "mouse_down" for event in events):
            raise TaskFault("vision_batch_click", "use a click block to revalidate a recognition target")
        device = self._use_onboard(step)
        self._onboard_owner = task
        self._batch_active = True
        if device:
            try:
                self.onboard_taps.queue_batch(events)
                if self.before_press is not None:
                    for edge in events:
                        self.before_press(edge["key"] if "key" in edge else "mouse:batch")
                self.onboard_taps.start_batch()
                yield from self._await_job(len(events), events[-1]["offset_us"], self.batch_done)
            except (OSError, RuntimeError) as error:
                raise InputFault(str(error)) from error
            return

        origin = self.clock.now_ns
        for edge in events:
            deadline = origin + edge["offset_us"] * 1000
            if self.clock.now_ns < deadline:
                yield deadline
            if edge["action"] == "mouse_move":
                x, y = self._pointer_position()
                position = (x + edge["dx"], y + edge["dy"])
                if self.before_move is not None:
                    self.before_move(position)
                self._send(position, "mouse_move", deadline, self.backend.send_mouse_move)
                continue
            key = edge["key"] if "key" in edge else "mouse:" + edge["button"]
            down = edge["action"].endswith("_down")
            if down:
                if self.before_press is not None:
                    self.before_press(key)
                self._batch_held.add(key)
            self._batch_edge(key, down, deadline)
            if not down:
                self._batch_held.remove(key)
        self._batch_active = False
        self._onboard_owner = None

    def _batch_edge(self, key: str, down: bool, scheduled: int) -> None:
        if key.startswith("mouse:"):
            send = self.backend.send_mouse_down if down else self.backend.send_mouse_up
            self._send(key[6:], "mouse_down" if down else "mouse_up", scheduled, send)
        else:
            send = self.backend.send_key_down if down else self.backend.send_key_up
            self._send(key, "key_down" if down else "key_up", scheduled, send)

    def _sequence_integer(self, task: Task, step: dict, scope: Scope, name: str, upper: int) -> int:
        value = self._evaluate(step[name], scope, task)
        if not 1 <= value <= upper or value != int(value):
            raise TaskFault(f"tap_sequence_{name}", f"{name} must be an integer from 1 to {upper}")
        return int(value)

    def _press(self, task: Task, key: str, scheduled: int, *, click_position: tuple[int, int] | None = None) -> None:
        if self._batch_active or (self._onboard_owner is not None and not key.startswith("mouse:")):
            raise TaskFault("input_conflict", "input is occupied by an onboard job")
        if key in self._owners or (key.startswith("mouse:") and self._pointer_owner is not None):
            raise TaskFault("input_conflict", f"input is already held: {key}")
        if self.before_press is not None:
            self.before_press(key)
        if click_position is not None:
            if self._pointer_position() != click_position:
                raise TaskFault("pointer_off_position", "mouse pointer did not reach the requested coordinates")
            if self.before_move is not None:
                self.before_move(click_position)
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

    def _send(self, key: str | tuple[int, int], action: str, scheduled: int | None, send: Callable) -> None:
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
