from io import BytesIO
import json
import sys
from time import monotonic, sleep
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets")
from PySide6.QtCore import QProcess
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

from macro_studio import editor_runner, gui, win32
from macro_studio.ir import validate_program
from macro_studio.live import ArmStore
from macro_studio.output import HidMode, OutputKind


def test_editor_checks_hid_and_runner_receives_port_only_for_armed_hid(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui.Studio, "load_targets", lambda self: None)
    monkeypatch.setattr(gui.Studio, "build_canvas", lambda self: QWidget())
    monkeypatch.setattr(gui.ArmStore, "local", lambda: ArmStore(tmp_path / "armed.json"))
    ports = [{"port": "COM5", "description": "Arduino Leonardo 3(COM5)"}, {"port": "COM9"}]
    probed = []
    monkeypatch.setattr(gui, "list_ports", lambda: ports[:])

    def probe(port):
        probed.append(port)
        if port == "COM5":
            raise OSError("device not responding")
        return {"board": "Leonardo", "firmware": "0.2.0", "protocol": "1.0", "max_onboard_jobs": 1}

    monkeypatch.setattr(gui, "probe_port", probe)
    studio = gui.Studio()
    studio.resize(960, 640)
    studio.show()
    studio.path = tmp_path / "project.macroproj"
    studio.store = ArmStore(tmp_path / "armed.json")
    studio.targets.addItem("Test window", 42)
    program = validate_program({
        "ir_version": 1, "project_id": "hid-gui", "workflows": {}, "handlers": [],
    })
    monkeypatch.setattr(studio, "verify", lambda: program)
    monkeypatch.setattr(gui.QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    sent = []
    studio.process = SimpleNamespace(
        start=lambda *args: None, waitForStarted=lambda timeout: True,
        write=lambda data: sent.append(data) if data.startswith(b"{") else None,
        readAllStandardOutput=lambda: b"",
    )

    def wait_for_probe():
        deadline = monotonic() + 2
        while studio.probing and monotonic() < deadline:
            app.processEvents()
            sleep(0.005)
        assert not studio.probing

    def finish():
        studio.session_state = "STOPPED"
        studio.runner_finished(0, QProcess.ExitStatus.NormalExit)
        wait_for_probe()

    try:
        assert studio.output.currentData() == OutputKind.FAKE
        assert studio.hid_port.isHidden()
        studio.output.setCurrentIndex(studio.output.findData(OutputKind.EXTERNAL_HID))
        assert not studio.hid_port.isHidden()
        assert studio.hid_port.currentData() is None and not probed
        studio.hid_port.setCurrentIndex(studio.hid_port.findData("COM5"))
        assert studio.hid_port.currentText() == "COM5 · Arduino Leonardo 3"
        wait_for_probe()
        assert studio.hid_status.text() == "COM5 연결 확인 실패"
        studio.hid_port.setCurrentIndex(studio.hid_port.findData("COM9"))
        assert studio.hid_port.currentText() == "COM9"
        wait_for_probe()
        assert probed == ["COM5", "COM9"]
        assert studio.hid_status.text() == "COM9 연결 확인"
        studio.hid_mode.setCurrentIndex(studio.hid_mode.findData(HidMode.ONBOARD))
        assert studio.targets.geometry().bottom() < studio.output.geometry().top()
        assert studio.output.geometry().bottom() < studio.hid_port.geometry().top()

        studio.start()
        dry = json.loads(sent[-1])
        assert dry["target"] is None and "hid_port" not in dry and "hid_onboard" not in dry
        finish()

        studio.toggle_arm()
        assert studio.armed()
        studio.start()
        armed_payload = sent[-1]
        armed = json.loads(armed_payload)
        assert armed["target"] == 42 and armed["hid_port"] == "COM9" and armed["hid_onboard"] is True
        assert not studio.output.isEnabled() and not studio.hid_port.isEnabled()
        finish()

        ports.pop()
        studio.load_hid_ports()
        assert studio.hid_status.text() == "COM9 연결 끊김"
        studio.start()
        assert sent[-1] == armed_payload
        studio.output.setCurrentIndex(studio.output.findData(OutputKind.FAKE))
        studio.start()
        explicit_dry = json.loads(sent[-1])
        assert studio.armed() and explicit_dry["target"] is None and "hid_port" not in explicit_dry
        finish()

        calls = []
        monkeypatch.setattr(sys, "stdin", SimpleNamespace(fileno=lambda: 0))
        monkeypatch.setattr(editor_runner, "FileIO", lambda *args, **kwargs: BytesIO(armed_payload))
        monkeypatch.setattr(win32, "WindowsDesktop", object)

        def record(_program, _desktop, target, _duration, *, hid_port, hid_onboard, **_callbacks):
            calls.append((target, hid_port, hid_onboard))
            return {"errors": []}

        monkeypatch.setattr(editor_runner, "run_live", record)
        editor_runner.main()
        assert calls == [(42, "COM9", True)]
    finally:
        studio.heartbeat.stop()
        studio.hid_timer.stop()
        studio.running = False
        wait_for_probe()
        studio.dirty = False
        studio.close()


def test_project_restores_leonardo_by_usb_identity_after_com_number_changes(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(gui.Studio, "load_targets", lambda self: None)
    monkeypatch.setattr(gui.Studio, "build_canvas", lambda self: QWidget())
    leonardo = {"vid": 0x2341, "pid": 0x8036, "serial_number": "LEONARDO-ONE"}
    ports = [dict(leonardo, port="COM5")]
    probed = []
    monkeypatch.setattr(gui, "list_ports", lambda: ports[:])

    def probe(port):
        probed.append(port)
        return {"board": "Leonardo", "firmware": "0.1.0", "protocol": "1.0"}

    monkeypatch.setattr(gui, "probe_port", probe)
    studio = gui.Studio()

    def wait_for_probe():
        deadline = monotonic() + 2
        while studio.probing and monotonic() < deadline:
            app.processEvents()
            sleep(0.005)
        assert not studio.probing

    path = tmp_path / "saved.macroproj"
    legacy = tmp_path / "legacy.macroproj"
    legacy.write_text(json.dumps(gui.new_project()), encoding="utf-8")
    selected_file = [str(path)]
    monkeypatch.setattr(gui.QFileDialog, "getOpenFileName", lambda *args: (selected_file[0], ""))
    try:
        studio.output.setCurrentIndex(studio.output.findData(OutputKind.EXTERNAL_HID))
        studio.hid_port.setCurrentIndex(studio.hid_port.findData("COM5"))
        wait_for_probe()
        assert studio.dirty and studio.write(path)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["output_profile"] == {"output_kind": "external_hid", "device_selector": leonardo}

        studio.create()
        assert studio.output.currentData() == OutputKind.FAKE
        ports[:] = [dict(leonardo, port="COM5", serial_number="OTHER-LEONARDO")]
        studio.open()
        assert studio.output.currentData() == OutputKind.EXTERNAL_HID
        assert studio.hid_port.currentData() is None and studio.checked_port is None
        assert studio.hid_status.text() == "저장된 HID 장치를 찾을 수 없습니다."
        assert probed == ["COM5"]

        ports.append(dict(leonardo, port="COM12"))
        studio.load_hid_ports()
        wait_for_probe()
        assert studio.hid_port.currentData() == studio.checked_port == "COM12"
        assert probed == ["COM5", "COM12"] and not studio.dirty

        ports.append(dict(leonardo, port="COM13"))
        studio.load_hid_ports()
        assert studio.hid_port.currentData() is None and studio.checked_port is None
        selected_file[0] = str(legacy)
        studio.open()
        assert studio.output.currentData() == OutputKind.FAKE
    finally:
        studio.hid_timer.stop()
        wait_for_probe()
        studio.dirty = False
        studio.close()
