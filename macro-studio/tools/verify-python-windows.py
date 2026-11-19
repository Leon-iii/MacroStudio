"""Opt-in Python editor/Runner safety checks using only owned test windows."""

import argparse
import ctypes
from ctypes import wintypes
import json
from pathlib import Path
from time import monotonic, perf_counter_ns
import traceback

from PySide6.QtCore import QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QVBoxLayout, QWidget

from macro_studio.gui import Studio, new_project
from macro_studio.live import ArmStore
from macro_studio.output import OutputKind
from macro_studio.win32 import WindowsDesktop


SOURCE = '''@on_start()
async def hold_key():
    await keyboard.tap("A", hold_ms=5000)

@on_start()
async def hold_button():
    await mouse.click(button="left", hold_ms=5000)
'''


class Target(QWidget):
    def __init__(self, title):
        super().__init__()
        self.setWindowTitle(title)
        self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.resize(640, 380)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.presses = []
        layout = QVBoxLayout(self)
        self.label = QLabel("Macro Studio Python acceptance\nKey A and left mouse button test")
        self.label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self.label)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_A and not event.isAutoRepeat():
            self.presses.append("key")

    def mousePressEvent(self, event):
        self.presses.append("mouse")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--hid-port")
    parser.add_argument("--confirm-real-input", action="store_true", required=True)
    args = parser.parse_args()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    app = QApplication([])
    desktop = WindowsDesktop()
    original_cursor = desktop.mouse_position()
    target = Target("Macro Studio Python Acceptance Target")
    target.move(150, 150)
    other = Target("Macro Studio Python Focus Transfer")
    other.move(850, 150)
    studio = Studio()
    studio.show()
    target.show()
    app.processEvents()
    target_hwnd = int(target.winId())
    target_info = desktop.target(target_hwnd)
    store_path = args.report.with_suffix(".arm.json")
    studio.store = ArmStore(store_path)
    studio.project = new_project()
    studio.project.update(schema_version=2, editor={"active_mode": "python", "python": {"source": SOURCE}})
    studio.path = args.report.with_suffix(".macroproj")
    studio.targets.addItem("Python acceptance target", target_hwnd)
    studio.targets.setCurrentIndex(studio.targets.findData(target_hwnd))
    studio.load_workspace()
    assert studio.write(studio.path)
    report = {"cases": [], "hid_port": args.hid_port}

    def wait(check, description, timeout=10):
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            app.processEvents()
            if check():
                return
            QTest.qWait(10)
        raise AssertionError(f"{description}\n{studio.logs.toPlainText()}")

    def held():
        return [bool(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000) for key in (0x41, 0x01)]

    def physical_cursor():
        with desktop.physical_coordinates():
            return desktop.mouse_position()

    def focus(window):
        user32 = ctypes.windll.user32
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetFocus.argtypes = (wintypes.HWND,)
        current = ctypes.windll.kernel32.GetCurrentThreadId()
        foreground_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
        attached = current != foreground_thread and user32.AttachThreadInput(current, foreground_thread, True)
        try:
            window.raise_()
            window.activateWindow()
            user32.SetForegroundWindow(int(window.winId()))
            user32.SetFocus(int(window.winId()))
        finally:
            if attached:
                user32.AttachThreadInput(current, foreground_thread, False)

    def foreground():
        focus(target)
        wait(lambda: desktop.focused(target_info), "test target foreground")
        center = physical_point(target.rect().center().x(), target.rect().center().y())
        with desktop.physical_coordinates():
            desktop.send_mouse_move(center)
        assert desktop.cursor_over(target_info)

    def physical_point(x, y):
        origin = wintypes.POINT(0, 0)
        user32 = ctypes.windll.user32
        user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        with desktop.physical_coordinates():
            if not user32.ClientToScreen(target_hwnd, ctypes.byref(origin)):
                raise ctypes.WinError()
        scale = target.devicePixelRatioF()
        return origin.x + round(x * scale), origin.y + round(y * scale)

    def stop():
        studio.send("stop")
        wait(lambda: not studio.running, "Runner exit")
        wait(lambda: held() == [False, False], "input release")

    def approve_arm():
        modal = app.activeModalWidget()
        if isinstance(modal, QMessageBox):
            modal.button(QMessageBox.StandardButton.Yes).click()
        elif studio.python_busy:
            QTimer.singleShot(30, approve_arm)

    def start_held():
        assert held() == [False, False]
        if studio.uses_hid():
            wait(lambda: studio.checked_port == args.hid_port and not studio.probing, "HID ready after previous Runner", 15)
        foreground()
        before = len(target.presses)
        studio.start()
        wait(lambda: studio.running, "Runner start")
        foreground()
        wait(lambda: held() == [True, True], "owned key and mouse held")
        wait(lambda: {"key", "mouse"} <= set(target.presses[before:]), "test window received both inputs")

    try:
        wait(lambda: studio.editor_ready, "Blockly readiness")
        assert held() == [False, False], "Release physical A and left mouse before running this check"
        for output, port in ((OutputKind.SENDINPUT, None), (OutputKind.EXTERNAL_HID, args.hid_port)):
            if output == OutputKind.EXTERNAL_HID and not port:
                continue
            studio.store.set(studio.path, studio.project, False)
            studio.output.setCurrentIndex(studio.output.findData(output))
            if port:
                studio.load_hid_ports()
                studio.hid_port.setCurrentIndex(studio.hid_port.findData(port))
                wait(lambda: studio.checked_port == port and not studio.probing, "HID connection", 15)
            studio.verify()
            wait(lambda: not studio.python_busy, "source compilation")
            assert studio.python_snapshot is not None
            foreground()
            before = len(target.presses)
            studio.start()
            wait(lambda: studio.session_state == "RUNNING", "unarmed dry Runner")
            QTest.qWait(150)
            assert studio.session_mode == "dry" and held() == [False, False]
            assert len(target.presses) == before
            stop()
            report["cases"].append({"output": output.value, "case": "unarmed", "pass": True, "physical_inputs": 0})

            QTimer.singleShot(30, approve_arm)
            studio.toggle_arm()
            wait(studio.armed, "explicit ARM")
            assert not studio.running and held() == [False, False], (studio.running, held(), target.presses)
            start_held()
            started = perf_counter_ns()
            stop()
            report["cases"].append({"output": output.value, "case": "stop", "pass": True,
                                     "release_observed_ms": (perf_counter_ns() - started) / 1e6})

            start_held()
            other.show()
            focus(other)
            wait(lambda: desktop.focused(desktop.target(int(other.winId()))), "focus transfer")
            wait(lambda: studio.session_state == "PAUSED" and held() == [False, False], "focus cleanup")
            foreground()
            before = len(target.presses)
            QTest.qWait(300)
            assert studio.session_state == "PAUSED" and held() == [False, False]
            assert len(target.presses) == before
            stop()
            other.hide()
            report["cases"].append({"output": output.value, "case": "focus_loss", "pass": True,
                                     "held_after_focus_return": held(), "automatic_resume": False})

            start_held()
            emergency = ("scan:1D", "scan:38", "scan:2A", "scan:57")
            try:
                for key in emergency:
                    desktop.send_key_down(key)
            finally:
                for key in reversed(emergency):
                    desktop.send_key_up(key)
            wait(lambda: not studio.running and held() == [False, False], "emergency stop")
            report["cases"].append({"output": output.value, "case": "emergency_stop", "pass": True})
            studio.store.set(studio.path, studio.project, False)
        from base64 import b64encode
        import cv2
        from macro_studio.perception import capture_once, list_capture_sources

        studio.output.setCurrentIndex(studio.output.findData(OutputKind.SENDINPUT))
        foreground()
        QTest.qWait(100)
        px, py = physical_point(target.label.x() + 2, target.label.y() + target.label.height() // 2 - 24)
        width, height = round(300 * target.devicePixelRatioF()), round(55 * target.devicePixelRatioF())
        source = next(item for item in list_capture_sources() if item.source_id != "desktop" and
                      item.region.left <= px and item.region.top <= py and
                      px + width <= item.region.left + item.region.width and
                      py + height <= item.region.top + item.region.height)
        frame = capture_once(source.source_id)
        x, y = px - frame.origin_x, py - frame.origin_y
        patch = frame.bgr[y:y + height, x:x + width]
        assert patch.shape[:2] == (height, width) and patch.std() > 1
        with desktop.physical_coordinates():
            assert desktop.point_over(target_info, (px + width // 2, py + height // 2))
        ok, png = cv2.imencode(".png", patch)
        assert ok
        studio.project["image_assets"] = {"asset:acceptance": {"name": "Owned test window label",
            "png": b64encode(png.tobytes()).decode("ascii")}}
        studio.project["vision_source"] = source.source_id
        studio.project["editor"]["python"]["source"] = f'''@on_start()
async def locate_and_click():
    result = await vision.find("asset:acceptance", timeout_ms=3000, enter_threshold=0.99, exit_threshold=0.98, max_age_ms=1000, roi_left={x}, roi_top={y}, roi_width={width}, roi_height={height})
    if result.status == "FOUND":
        await mouse.move_to(result.center_x, result.center_y, profile_id="builtin.direct", duration_ms=100)
        await mouse.click(hold_ms=30)
'''
        studio.load_workspace()
        assert studio.write(studio.path)
        before = len(target.presses)
        cursor = physical_cursor()
        studio.start()
        wait(lambda: not studio.python_busy and not studio.running and "마우스 X" in studio.logs.toPlainText(), "vision dry run", 15)
        assert studio.session_mode == "dry"
        assert len(target.presses) == before and physical_cursor() == cursor, (
            before, len(target.presses), cursor, physical_cursor(), studio.logs.toPlainText())
        report["cases"].append({"output": "fake", "case": "vision_motion_click", "pass": True, "physical_inputs": 0})
        QTimer.singleShot(30, approve_arm)
        studio.toggle_arm()
        wait(studio.armed, "vision explicit ARM")
        foreground()
        studio.start()
        wait(lambda: studio.running, "vision real Runner")
        foreground()
        wait(lambda: not studio.running, "vision motion completion", 15)
        assert "mouse" in target.presses[before:] and studio.session_state == "STOPPED", studio.logs.toPlainText()
        assert held() == [False, False]
        report["cases"].append({"output": "sendinput", "case": "vision_motion_click", "pass": True})
        report["pass"] = True
    except Exception:
        report["error"] = traceback.format_exc()
        traceback.print_exc()
    finally:
        if studio.running:
            studio.send("stop")
            try:
                wait(lambda: not studio.running, "cleanup Runner", 5)
            except AssertionError:
                studio.process.kill()
                studio.process.waitForFinished(2000)
        # These are exactly the two inputs exercised by this dedicated test session.
        desktop.send_key_up("scan:1E")
        desktop.send_mouse_up("left")
        desktop.send_mouse_move(original_cursor)
        studio.store.set(studio.path, studio.project, False)
        studio.dirty = False
        studio.close()
        target.close()
        other.close()
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True), flush=True)
    return 0 if report.get("pass") else 1


if __name__ == "__main__":
    raise SystemExit(main())
