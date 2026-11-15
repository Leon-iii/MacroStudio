import json
from struct import pack
import sys
from types import SimpleNamespace

import pytest

from macro_studio.__main__ import main
from macro_studio.external_hid import FrameParser, Opcode
from macro_studio.external_hid.serial_link import SerialLink
from macro_studio.external_hid.simulator import McuSimulator
from macro_studio.external_hid.transport import TransportError


class SimulatorPort:
    def __init__(self):
        self.device = McuSimulator()
        self.parser = FrameParser()
        self.written = []
        self.incoming = bytearray()
        self.closed = False
        self.drop_reply = False

    @property
    def in_waiting(self):
        return len(self.incoming)

    def write(self, data):
        self.written.extend(frame.opcode for frame in self.parser.feed(data))
        response = self.device.receive(data)
        if not self.drop_reply:
            self.incoming.extend(response)
        return len(data)

    def read(self, size):
        data = bytes(self.incoming[:min(size, 3)])
        del self.incoming[:len(data)]
        return data

    def close(self):
        self.closed = True


def test_probe_opens_only_selected_port_and_does_not_arm(monkeypatch, capsys):
    port = SimulatorPort()
    opened = []

    def open_port(name, baud, *, timeout, write_timeout):
        opened.append((name, baud, timeout, write_timeout))
        return port

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=open_port))
    assert main(["devices", "probe", "--port", "COM15"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert opened == [("COM15", 115200, 0.02, 0.1)]
    assert result["board"] == "simulator"
    assert result["watchdog_ms"] == 1500
    assert port.written == [Opcode.HELLO, Opcode.GET_CAPS, Opcode.RELEASE_ALL]
    assert port.closed


def test_lost_input_ack_sends_release_without_retrying_input(monkeypatch):
    port = SimulatorPort()
    now = 0
    monkeypatch.setattr("macro_studio.external_hid.serial_link._now_ms", lambda: now)

    def read(size):
        nonlocal now
        now += 20 if port.drop_reply else 1
        return read_port(size)

    read_port, port.read = port.read, read
    with pytest.raises(TransportError, match="timed out"):
        with SerialLink(port, session_id=123, timeout_ms=100) as link:
            link.inspect()
            link.exchange(Opcode.ARM_SESSION)
            port.drop_reply = True
            link.exchange(Opcode.INPUT_EVENT, pack("<BBHH", 1, 1, 7, 4))
    assert port.written[-2:] == [Opcode.INPUT_EVENT, Opcode.RELEASE_ALL]
    assert not port.device.pressed_keys
    assert port.closed
