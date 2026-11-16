from contextlib import nullcontext
from struct import unpack

import pytest

from macro_studio.compiler import compile_project
from macro_studio.external_hid import FrameParser, Opcode
from macro_studio.external_hid.serial_link import SerialLink
from macro_studio.external_hid.trajectory_batch import timed_pointer_batch
from macro_studio.live import run_live
from macro_studio.mouse_motion import BUILTIN_PROFILES, MotionGenerator, TimedPoint
from macro_studio.mouse_motion.quantizer import RelativeQuantizer, first_report

from test_hid_serial_link import SimulatorPort
from test_live import Desktop, PollClock
from test_profiled_mouse_move import move, project


def test_relative_rounding_carries_fractional_signed_movement_and_splits_large_steps():
    quantizer = RelativeQuantizer((10, 20))
    for index in range(1, 12):
        point = TimedPoint(index, 10 + 0.4 * index, 20 - 0.3 * index)
        quantizer.record(quantizer.remaining(point))
        assert (quantizer.sent_x, quantizer.sent_y) == (round(0.4 * index), round(-0.3 * index))
    point = TimedPoint(12, 620, -330)
    packets = []
    while (delta := quantizer.remaining(point)) != (0, 0):
        report = first_report(delta)
        assert all(-127 <= component <= 127 for component in report)
        packets.append(report)
        quantizer.record(report)
    assert len(packets) > 1
    assert (quantizer.sent_x, quantizer.sent_y) == (610, -350)


def test_bounded_timed_batch_rejects_incompatible_device_constraints():
    profile = BUILTIN_PROFILES["builtin.direct"]
    motion = MotionGenerator.generate((0, 0), (25, -11), profile, 7, 20)
    edges = timed_pointer_batch(motion, max_payload=64, minimum_interval_us=1000)
    assert (sum(edge["dx"] for edge in edges), sum(edge["dy"] for edge in edges)) == (25, -11)
    with pytest.raises(ValueError, match="buffer"):
        timed_pointer_batch(motion, max_payload=10, minimum_interval_us=1000)
    with pytest.raises(ValueError, match="sample times"):
        timed_pointer_batch(motion, max_payload=64, minimum_interval_us=6000)


@pytest.mark.parametrize("case", ["streaming", "adaptive", "stop", "focus", "disconnect", "onboard"])
def test_profiled_moves_use_relative_reports_and_stop_safely(monkeypatch, case):
    clock = PollClock()

    class TracedPort(SimulatorPort):
        reports = []
        lose_focus = False
        trace = FrameParser()

        def write(self, data):
            for frame in self.trace.feed(data):
                if frame.opcode is Opcode.INPUT_EVENT:
                    kind, dx, dy = unpack("<Bhh", frame.payload)
                    assert kind == 3
                    if case == "disconnect" and self.reports:
                        raise OSError("HID device disconnected")
                    self.reports.append((clock.now_ns, dx, dy))
                    if case == "focus":
                        self.lose_focus = True
            return super().write(data)

    port = TracedPort()

    class RelativeDesktop(Desktop):
        def mouse_position(self):
            dx, dy = port.device.pointer_offset
            return 150 + dx, 100 + dy

        def point_over(self, target, position):
            return True

        def focused(self, target):
            return not port.lose_focus

        def physical_coordinates(self):
            return nullcontext()

    desktop = RelativeDesktop(clock)
    class TestLink(SerialLink):
        def inspect(self):
            return {**super().inspect(), "max_onboard_jobs": 1}

    monkeypatch.setattr("macro_studio.live.open_port", lambda *_args, **_kwargs: TestLink(port, session_id=4711))
    code, _ = compile_project(project(
        move("absolute", 750, 20, duration=25, profile="builtin.direct"),
        move("relative", -45, 12, duration=60, kind="mouse_move_relative_timed", profile="builtin.direct"),
    ))
    if case == "onboard":
        with pytest.raises(ValueError, match="absolute.*profiled_mouse"):
            run_live(code, desktop, 1, 1_000_000_000, clock=clock, hid_port="COM5", hid_mode="onboard")
        assert Opcode.ARM_SESSION not in port.written and not port.reports
        return
    status = []
    def control():
        return "stop" if case == "stop" and len(port.reports) == 2 else None

    result = run_live(code, desktop, 1, 1_000_000_000, clock=clock, hid_port="COM5",
                      hid_mode="adaptive" if case == "adaptive" else "streaming",
                      control=control, status=status.append)
    assert not desktop.events
    if case == "disconnect":
        assert result["termination"] == "input_error" and len(port.reports) == 1
        assert [error["block_id"] for error in result["errors"]] == ["absolute"]
        return
    assert not result["errors"]
    assert all(abs(dx) <= 127 and abs(dy) <= 127 for _, dx, dy in port.reports)
    assert all(second[0] - first[0] >= 8_000_000 for first, second in zip(port.reports, port.reports[1:]))
    if case in ("stop", "focus"):
        assert len(port.reports) == (2 if case == "stop" else 1)
        assert 0 < port.device.pointer_offset[0] < 600
    else:
        assert result["termination"] == "completed"
        assert port.device.pointer_offset == (555, -68)
        assert not any(op in port.written for op in (Opcode.PREPARE_JOB, Opcode.QUEUE_BATCH, Opcode.START_JOB))
