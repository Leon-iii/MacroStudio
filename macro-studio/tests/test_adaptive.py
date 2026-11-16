from struct import pack
from types import SimpleNamespace

import pytest

from macro_studio.external_hid import Opcode
from macro_studio.ir import validate_program
from macro_studio.live import run_live

from test_live import Desktop, PollClock


class JobLink:
    def __init__(self, *, caps=True, fail_start=False):
        self.caps = caps
        self.fail_start = fail_start
        self.commands = []
        self.pending_count = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True

    def inspect(self):
        return {
            "keyboard": True, "mouse_buttons": True, "mouse_relative": True,
            "timed_batch": self.caps, "max_payload": 64, "watchdog_ms": 1500,
            "max_onboard_jobs": int(self.caps),
        }

    def exchange(self, opcode, payload=b""):
        self.commands.append((opcode, payload))
        if opcode is Opcode.START_JOB:
            if self.fail_start:
                raise OSError("START_JOB acknowledgement lost")
        elif opcode is Opcode.PREPARE_JOB:
            self.pending_count = int.from_bytes(payload[-4:], "little")
        elif opcode is Opcode.QUEUE_BATCH:
            self.pending_count = payload[2]
        return SimpleNamespace(payload=b"")

    def job_done(self):
        if self.pending_count is None:
            return None
        self.commands.append(("JOB_DONE", b""))
        count, self.pending_count = self.pending_count, None
        job_id = 1
        return SimpleNamespace(payload=pack("<HBIH", job_id, 0, count, 0))

    def release_stuck_inputs(self):
        self.commands.append(("release_stuck_inputs", b""))


def _program(steps, *, handlers=None, globals_=None):
    return validate_program({
        "ir_version": 1, "project_id": "adaptive-tests", "globals": globals_ or {},
        "workflows": {"main": {"steps": steps}},
        "handlers": handlers or [{"id": "start", "workflow_id": "main",
                                  "event": {"type": "project_start"}}],
    })


def _run(monkeypatch, program, *, link=None, clock=None, desktop=None):
    link = link or JobLink()
    clock = clock or PollClock()
    desktop = desktop or Desktop(clock)
    monkeypatch.setattr("macro_studio.live.open_port", lambda *args, **kwargs: link)
    result = run_live(program, desktop, 1, 500_000_000, clock=clock,
                      hid_port="COM5", hid_mode="adaptive")
    return result, desktop, link


@pytest.mark.parametrize(("frequency", "hold_ns"), [(100, 100_000), (200, 99_000)])
def test_static_tap_train_finishes_on_device_before_following_key_stream(monkeypatch, frequency, hold_ns):
    program = _program([
        {"op": "tap_sequence", "key": "A", "frequency_hz": frequency,
         "count": 3, "hold_ns": hold_ns},
        {"op": "key_tap", "key": "B", "hold_ns": 1_000_000},
    ])

    result, desktop, link = _run(monkeypatch, program)

    commands = [opcode for opcode, _ in link.commands]
    prepare = commands.index(Opcode.PREPARE_JOB)
    start = commands.index(Opcode.START_JOB)
    complete = commands.index("JOB_DONE") if "JOB_DONE" in commands else len(commands)
    stream = commands.index(Opcode.INPUT_EVENT)
    assert prepare < start < complete < stream
    assert result["errors"] == []
    streamed = [payload for opcode, payload in link.commands if opcode is Opcode.INPUT_EVENT]
    assert len(streamed) == 2
    assert all(payload[0] == 1 and int.from_bytes(payload[4:6], "little") == 5 for payload in streamed)


def test_focus_lost_during_prepare_cancels_without_starting_train(monkeypatch):
    class FocusDesktop(Desktop):
        in_front = True

        def focused(self, target):
            return self.in_front

    class FocusLosingLink(JobLink):
        def exchange(self, opcode, payload=b""):
            reply = super().exchange(opcode, payload)
            if opcode is Opcode.PREPARE_JOB:
                desktop.in_front = False
            return reply

    clock = PollClock()
    desktop = FocusDesktop(clock)
    program = _program([{"op": "tap_sequence", "block_id": "train", "key": "A",
                        "frequency_hz": 100, "count": 3, "hold_ns": 100_000}])
    result, _, link = _run(monkeypatch, program, link=FocusLosingLink(), clock=clock, desktop=desktop)
    commands = [opcode for opcode, _ in link.commands]
    assert Opcode.PREPARE_JOB in commands and Opcode.CANCEL_JOB in commands
    assert Opcode.START_JOB not in commands and Opcode.INPUT_EVENT not in commands
    assert [(error["code"], error["block_id"]) for error in result["errors"]] == [("focus_lost", "train")]


@pytest.mark.parametrize("frequency", [100, 60])
def test_dynamic_or_non_integral_frequency_stays_on_host(monkeypatch, frequency):
    rate = {"var": "rate"} if frequency == 100 else 60
    program = _program([
        {"op": "tap_sequence", "key": "A", "frequency_hz": rate,
         "count": 2, "hold_ns": 1_000_000},
    ], globals_={"rate": 100})
    result, desktop, link = _run(monkeypatch, program)
    commands = [opcode for opcode, _ in link.commands]
    assert Opcode.INPUT_EVENT in commands
    assert Opcode.PREPARE_JOB not in commands and Opcode.START_JOB not in commands
    assert sum(opcode is Opcode.INPUT_EVENT for opcode, _ in link.commands) == 4
    assert result["errors"] == []


def test_missing_tap_train_capability_keeps_fixed_sequence_on_host(monkeypatch):
    program = _program([{"op": "tap_sequence", "key": "A", "frequency_hz": 100,
                        "count": 2, "hold_ns": 100_000}])
    result, desktop, link = _run(monkeypatch, program, link=JobLink(caps=False))
    commands = [opcode for opcode, _ in link.commands]
    assert Opcode.INPUT_EVENT in commands
    assert Opcode.PREPARE_JOB not in commands and Opcode.START_JOB not in commands
    assert sum(opcode is Opcode.INPUT_EVENT for opcode, _ in link.commands) == 4
    assert result["errors"] == []


@pytest.mark.parametrize("parallel", [False, True])
def test_multiple_or_parallel_handlers_keep_tap_train_on_host(monkeypatch, parallel):
    handlers = [
        {"id": "start", "workflow_id": "main", "event": {"type": "project_start"},
         "policy": "parallel" if parallel else "ignore_if_running"},
    ]
    if not parallel:
        handlers.append({"id": "extra", "workflow_id": "main",
                         "event": {"type": "hotkey_pressed", "hotkey": "CTRL+F8"}})
    program = _program([{"op": "tap_sequence", "key": "A", "frequency_hz": 100,
                        "count": 2, "hold_ns": 100_000}], handlers=handlers)
    _, desktop, link = _run(monkeypatch, program)
    commands = [opcode for opcode, _ in link.commands]
    assert Opcode.INPUT_EVENT in commands
    assert Opcode.PREPARE_JOB not in commands and Opcode.START_JOB not in commands
    assert sum(opcode is Opcode.INPUT_EVENT for opcode, _ in link.commands) == 4


def test_false_host_guard_skips_the_onboard_candidate(monkeypatch):
    program = _program([{"op": "if", "condition": False, "then": [
        {"op": "tap_sequence", "key": "A", "frequency_hz": 100,
         "count": 2, "hold_ns": 100_000},
    ], "else": [{"op": "key_tap", "key": "B", "hold_ns": 1_000_000}]}])
    _, desktop, link = _run(monkeypatch, program)
    commands = [opcode for opcode, _ in link.commands]
    assert Opcode.PREPARE_JOB not in commands and Opcode.START_JOB not in commands
    streamed = [payload for opcode, payload in link.commands if opcode is Opcode.INPUT_EVENT]
    assert len(streamed) == 2
    assert all(int.from_bytes(payload[4:6], "little") == 5 for payload in streamed)


def test_failed_start_ack_stops_without_streaming_or_replaying_key(monkeypatch):
    program = _program([{"op": "tap_sequence", "key": "A", "frequency_hz": 100,
                        "count": 3, "hold_ns": 100_000}])
    result, _, link = _run(monkeypatch, program, link=JobLink(fail_start=True))
    commands = [opcode for opcode, _ in link.commands]
    assert commands.count(Opcode.PREPARE_JOB) == 1
    assert commands.count(Opcode.START_JOB) == 1
    assert Opcode.INPUT_EVENT not in commands
    assert result["errors"]


def _batch(events):
    return {"op": "timed_batch", "events": events}


def test_keyboard_only_timed_batch_uses_device_with_capability(monkeypatch):
    program = _program([_batch([
        {"offset_us": 0, "action": "key_down", "key": "A"},
        {"offset_us": 1000, "action": "key_up", "key": "A"},
    ])])
    result, desktop, link = _run(monkeypatch, program)
    commands = [opcode for opcode, _ in link.commands]
    assert Opcode.QUEUE_BATCH in commands and Opcode.START_JOB in commands
    assert Opcode.INPUT_EVENT not in commands
    assert desktop.events == [] and result["errors"] == []


def test_pointer_timed_batch_stays_on_host_and_obeys_target_guard(monkeypatch):
    class OutsideDesktop(Desktop):
        def cursor_over(self, target):
            return False

    clock = PollClock()
    desktop = OutsideDesktop(clock)
    program = _program([_batch([
        {"offset_us": 0, "action": "mouse_down", "button": "left"},
        {"offset_us": 1000, "action": "mouse_up", "button": "left"},
    ])])
    result, desktop, link = _run(monkeypatch, program, desktop=desktop, clock=clock)
    commands = [opcode for opcode, _ in link.commands]
    assert Opcode.QUEUE_BATCH not in commands and Opcode.START_JOB not in commands
    assert result["errors"] and result["errors"][0]["code"] == "pointer_outside_target"
    assert not desktop.events


def test_timed_batch_without_capability_streams_on_host(monkeypatch):
    program = _program([_batch([
        {"offset_us": 0, "action": "key_down", "key": "A"},
        {"offset_us": 1000, "action": "key_up", "key": "A"},
    ])])
    result, desktop, link = _run(monkeypatch, program, link=JobLink(caps=False))
    commands = [opcode for opcode, _ in link.commands]
    assert Opcode.QUEUE_BATCH not in commands and Opcode.START_JOB not in commands
    assert sum(opcode is Opcode.INPUT_EVENT for opcode in commands) == 2
    assert result["errors"] == []
