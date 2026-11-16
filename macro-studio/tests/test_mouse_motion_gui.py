import json

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets")
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QFileDialog, QMessageBox, QWidget

from macro_studio.gui import Studio
from macro_studio.mouse_motion.registry import MotionProfileRegistry, project_motion_profiles
from macro_studio.mouse_profile_dialog import MotionProfilesDialog, ProfileEditor


def test_mouse_menu_and_profile_manager_restore_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(Studio, "build_canvas", lambda self: QWidget())
    monkeypatch.setattr(Studio, "load_targets", lambda self: None)
    registry_path = tmp_path / "mouse-motion.json"
    monkeypatch.setattr(MotionProfileRegistry, "local", lambda: MotionProfileRegistry(registry_path))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    app = QApplication.instance() or QApplication([])
    studio = Studio()

    def edit(button, name, duration):
        def enter_values():
            editor = app.activeModalWidget()
            if not isinstance(editor, ProfileEditor):
                return
            editor.name.setText(name)
            editor.values["duration_ms"].setText(str(duration))
            buttons = editor.findChild(QDialogButtonBox)
            buttons.button(QDialogButtonBox.StandardButton.Save).click()
            if editor.isVisible():
                editor.reject()

        QTimer.singleShot(0, enter_values)
        button.click()
        assert manager.selected().name == name

    def action(menu, title):
        return next(item for item in menu.actions() if item.text() == title)

    manager = MotionProfilesDialog(studio, studio.motion_profiles, None)
    manager.show()
    app.processEvents()
    try:
        assert not manager.edit.isEnabled()
        edit(manager.add, "업무용", 420)
        profile_id = manager.selected().id
        edit(manager.clone, "복사본", 510)
        duplicate_id = manager.selected().id
        edit(manager.edit, "빠른 복사본", 300)
        assert manager.selected().id == duplicate_id and manager.selected().revision == 2
        manager.delete_button.click()
        assert duplicate_id not in studio.motion_profiles.load()[1]

        studio.mouse_menu.aboutToShow.emit()
        assert action(studio.app_motion_menu, "Natural").isChecked()
        assert action(studio.project_motion_menu, "앱 기본값 사용").isChecked()
        action(studio.app_motion_menu, "Smooth").trigger()
        action(studio.project_motion_menu, "업무용").trigger()
        path = tmp_path / "motion.macroproj"
        assert studio.write(path)
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert project_motion_profiles(payload)[1][profile_id].duration_ms == 420

        restarted = Studio()
        try:
            monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
            restarted.open()
            restarted.mouse_menu.aboutToShow.emit()
            assert action(restarted.app_motion_menu, "Smooth").isChecked()
            assert action(restarted.project_motion_menu, "업무용").isChecked()
        finally:
            restarted.close()
            restarted.deleteLater()

        clean_registry = tmp_path / "clean-user" / "mouse-motion.json"
        monkeypatch.setattr(MotionProfileRegistry, "local", lambda: MotionProfileRegistry(clean_registry))
        clean = Studio()
        try:
            clean.open()
            clean.mouse_menu.aboutToShow.emit()
            assert action(clean.app_motion_menu, "Natural").isChecked()
            assert all(item.text() != "업무용" for item in clean.app_motion_menu.actions())
            assert action(clean.project_motion_menu, "업무용").isChecked()
            assert project_motion_profiles(clean.project)[1][profile_id].duration_ms == 420
        finally:
            clean.close()
            clean.deleteLater()
    finally:
        manager.close()
        studio.dirty = False
        studio.close()
        studio.deleteLater()
        app.processEvents()
