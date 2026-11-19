import json
import time
from copy import deepcopy
from threading import Event

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets")
from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QWidget

from macro_studio import gui
from macro_studio.gui import Studio, new_project
from macro_studio.mouse_motion.registry import MotionProfileRegistry
from macro_studio.python_editor import validate_editor
from macro_studio.compiler import compile_project


SOURCE = '@on_start()\nasync def run():\n    await keyboard.tap("A", hold_ms=10)\n'

LEGACY_PROJECTS = sorted(path for path in (gui.Path(__file__).parents[1] / "examples").glob("*.macroproj")
                         if json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1)


def wait_until(check):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        QCoreApplication.processEvents()
        if check():
            return
        time.sleep(0.005)
    raise AssertionError("GUI operation timed out")


@pytest.fixture
def studio(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(Studio, "build_canvas", lambda self: QWidget())
    monkeypatch.setattr(Studio, "load_targets", lambda self: None)
    monkeypatch.setattr(MotionProfileRegistry, "local", lambda: MotionProfileRegistry(tmp_path / "profiles.json"))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    app = QApplication.instance() or QApplication([])
    window = Studio()
    window.project = new_project()
    window.editor_ready = True
    window.load_workspace()
    yield window
    if window.running:
        window.send("stop")
        wait_until(lambda: not window.running)
    wait_until(lambda: not window.python_busy)
    window.dirty = False
    window.close()
    window.deleteLater()
    app.processEvents()


def load_python(studio, source=SOURCE):
    studio.project.update(schema_version=2, editor={"active_mode": "python", "python": {"source": source}})
    studio.load_workspace()


def test_python_draft_save_reopen_and_old_project_stays_schema_one(studio, tmp_path, monkeypatch):
    path = tmp_path / "legacy.macroproj"
    assert studio.write(path)
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    studio.open()
    assert studio.project["schema_version"] == 1
    assert studio.edit_mode == "blocks"
    load_python(studio)
    studio.python_editor.code.setPlainText("async def incomplete(")
    path = tmp_path / "draft.macroproj"
    assert studio.write(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 2
    assert payload["editor"]["python"]["source"] == "async def incomplete("
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    studio.project = new_project()
    studio.open()
    assert studio.edit_mode == "python"
    assert studio.python_editor.code.toPlainText() == "async def incomplete("
    assert not studio.transport_button.isEnabled()
    studio.verify()
    wait_until(lambda: not studio.python_busy)
    assert "SYNTAX_ERROR" in studio.python_editor.status.text()
    assert studio.python_snapshot is None


@pytest.mark.parametrize("path", LEGACY_PROJECTS, ids=lambda path: path.stem)
def test_v13_examples_open_save_without_changing_blocks_assets_events_or_ir(studio, tmp_path, monkeypatch, path):
    original = json.loads(path.read_text(encoding="utf-8"))
    expected, _ = compile_project(original)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    studio.open()
    assert studio.edit_mode == "blocks"
    saved_path = tmp_path / path.name
    assert studio.write(saved_path)
    restored = json.loads(saved_path.read_text(encoding="utf-8"))
    assert restored["schema_version"] == 1
    for field in ("workspace", "image_assets", "globals", "settings", "output_profile"):
        assert restored.get(field) == original.get(field)
    rebuilt, _ = compile_project(restored)
    assert rebuilt == expected


def test_conversion_preserves_python_draft_and_previous_blocks(studio):
    original_blocks = deepcopy(studio.project["workspace"])
    load_python(studio, "# My own formatting\n" + SOURCE)
    studio.editors.setCurrentIndex(0)
    wait_until(lambda: not studio.python_busy)
    assert studio.edit_mode == "blocks"
    assert studio.project["workspace"] != original_blocks
    assert studio.project["editor"]["previous_workspace"] == original_blocks
    assert studio.project["editor"]["python"]["source"].startswith("# My own")
    studio.editors.setCurrentIndex(1)
    wait_until(lambda: not studio.python_busy and studio.edit_mode == "python")
    assert studio.python_snapshot is not None
    assert studio.project["editor"]["python"]["previous_source"].startswith("# My own")
    studio.restore_python()
    assert studio.python_editor.code.toPlainText().startswith("# My own")
    studio.restore_blocks()
    assert studio.edit_mode == "blocks"
    assert studio.project["workspace"] == original_blocks


def test_failed_conversion_keeps_source_and_workspace(studio):
    load_python(studio, SOURCE + '    import os\n')
    before = deepcopy(studio.project)
    studio.editors.setCurrentIndex(0)
    wait_until(lambda: not studio.python_busy)
    assert studio.project == before
    assert studio.edit_mode == "python"
    assert studio.editors.currentIndex() == 1
    assert studio.python_editor.code.textCursor().blockNumber() == 3
    studio.python_editor.code.moveCursor(studio.python_editor.code.textCursor().MoveOperation.Start)
    studio.jump.click()
    assert studio.python_editor.code.textCursor().blockNumber() == 3
    assert not studio.transport_button.isEnabled()


def test_edits_during_compile_discard_results_and_never_start(studio, monkeypatch):
    load_python(studio)
    entered, release = Event(), Event()
    compile_python = gui.compile_python

    def delayed(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return compile_python(*args, **kwargs)

    monkeypatch.setattr(gui, "compile_python", delayed)
    calls = []
    monkeypatch.setattr(studio, "start_program", lambda *args: calls.append(args))
    studio.start()
    assert entered.wait(2)
    studio.python_editor.code.insertPlainText("# changed\n")
    release.set()
    wait_until(lambda: not studio.python_busy)
    assert not calls
    assert studio.python_snapshot is None
    assert "다시 검증" in studio.python_editor.status.text()


def test_verified_source_is_invalidated_by_edit_and_invalid_start_is_rejected(studio, monkeypatch):
    load_python(studio)
    studio.verify()
    wait_until(lambda: not studio.python_busy)
    assert studio.transport_button.isEnabled()
    studio.python_editor.code.setPlainText("import os")
    assert not studio.transport_button.isEnabled()
    calls = []
    monkeypatch.setattr(studio, "start_program", lambda *args: calls.append(args))
    studio.start()
    wait_until(lambda: not studio.python_busy)
    assert not calls
    assert studio.python_snapshot is None


def test_python_import_export_and_restore_are_text_only(studio, monkeypatch, tmp_path):
    load_python(studio)
    monkeypatch.setattr(gui.ReviewPythonDialog, "exec", lambda self: gui.QDialog.DialogCode.Accepted)
    path = tmp_path / "source.py"
    path.write_text("# 새 초안\n" + SOURCE, encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    studio.import_python()
    assert studio.python_editor.code.toPlainText().startswith("# 새 초안")
    target = tmp_path / "exported.py"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    studio.export_python()
    assert target.read_text(encoding="utf-8") == path.read_text(encoding="utf-8")
    studio.restore_python()
    assert studio.python_editor.code.toPlainText() == SOURCE


def test_generated_source_ids_survive_save_and_reload(studio, monkeypatch, tmp_path):
    load_python(studio)
    studio.editors.setCurrentIndex(0)
    wait_until(lambda: not studio.python_busy)
    handler_id = studio.project["workspace"]["blocks"]["blocks"][0]["id"]
    studio.editors.setCurrentIndex(1)
    wait_until(lambda: not studio.python_busy)
    assert studio.python_snapshot is not None
    source = studio.python_editor.code.toPlainText()
    program = studio.python_snapshot.program_for(source, studio.project, **studio.compiler_settings())
    assert program["handlers"][0]["id"] == handler_id
    path = tmp_path / "generated.macroproj"
    assert studio.write(path)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    studio.open()
    assert studio.python_snapshot is None
    studio.verify()
    wait_until(lambda: not studio.python_busy)
    program = studio.python_snapshot.program_for(source, studio.project, **studio.compiler_settings())
    assert program["handlers"][0]["id"] == handler_id


def test_python_motion_profile_is_portable_without_local_registry(studio, monkeypatch, tmp_path):
    profile = studio.motion_profiles.save(MotionProfileRegistry.draft(name="Python motion"))
    source = ('@on_start()\nasync def run():\n'
              f'    await mouse.move_to(100, 200, profile_id="{profile.id}")\n')
    load_python(studio, source)
    path = tmp_path / "portable.macroproj"
    assert studio.write(path)
    studio.motion_profiles = MotionProfileRegistry(tmp_path / "other-user.json")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    studio.open()
    studio.verify()
    wait_until(lambda: not studio.python_busy)
    assert studio.python_snapshot is not None, studio.logs.toPlainText()
    assert profile.id in studio.python_references()
    assert studio.project["motion_profile_snapshots"][0]["id"] == profile.id


def test_readonly_preview_keeps_project_and_mode(studio):
    load_python(studio)
    before = deepcopy(studio.project)
    studio.request_python("preview")
    wait_until(lambda: not studio.python_busy)
    preview = next(widget for widget in studio.children()
                   if hasattr(widget, "windowTitle") and widget.windowTitle() == "생성된 Python · 읽기 전용")
    code = preview.findChild(gui.CodeEdit)
    assert code.isReadOnly()
    assert "keyboard.tap" in code.toPlainText()
    assert studio.project == before
    assert studio.edit_mode == "python"
    preview.close()


def test_python_gui_uses_separate_dry_runner_and_keeps_running_snapshot(studio, monkeypatch):
    monkeypatch.setenv("PYTHONPATH", str(gui.Path(gui.__file__).parent.parent))
    load_python(studio, '@on_hotkey("F6")\nasync def run():\n    await keyboard.tap("A", hold_ms=10)\n')
    studio.start()
    wait_until(lambda: studio.running and studio.session_state == "RUNNING")
    assert studio.process.processId() != 0
    studio.python_editor.code.setPlainText("import os")
    studio.send("hotkey:F6")
    wait_until(lambda: "해제 · scan:" in studio.logs.toPlainText())
    assert studio.session_mode == "dry"
    studio.send("stop")
    wait_until(lambda: not studio.running)
    assert studio.session_state == "STOPPED"
    assert not studio.transport_button.isEnabled()
    assert studio.python_editor.code.toPlainText() == "import os"


def test_generated_code_requires_review_and_preserves_original_on_cancel(studio, monkeypatch):
    load_python(studio)
    before = deepcopy(studio.project)

    def cancel_review(dialog):
        assert "-    await keyboard.tap" in dialog.diff.toPlainText()
        assert "+import os" in dialog.diff.toPlainText()
        return gui.QDialog.DialogCode.Rejected

    monkeypatch.setattr(gui.ReviewPythonDialog, "exec", cancel_review)
    studio.review_python("import os\n")
    assert studio.project == before
    assert not studio.running
    monkeypatch.setattr(gui.ReviewPythonDialog, "exec", lambda self: gui.QDialog.DialogCode.Accepted)
    studio.review_python("import os\n")
    assert studio.python_editor.code.toPlainText() == "import os\n"
    assert studio.project["editor"]["python"]["previous_source"] == SOURCE
    assert not studio.running
    assert studio.python_snapshot is None


def test_service_registration_diagnostics_export_and_separate_runner(studio, monkeypatch, tmp_path):
    import zipfile

    monkeypatch.setenv("PYTHONPATH", str(gui.Path(gui.__file__).parent.parent))
    source = '@on_start()\nasync def run():\n    await actions.run("desktop.confirm")\n'
    load_python(studio, source)
    studio.verify()
    wait_until(lambda: not studio.python_busy)
    assert studio.python_editor.feedback_button.isEnabled()
    studio.python_editor.feedback_button.click()
    assert json.loads(QApplication.clipboard().text())["code"] == "UNAVAILABLE_SERVICE"

    def register_pack(dialog):
        dialog.add_desktop()
        return gui.QDialog.DialogCode.Accepted

    monkeypatch.setattr(gui.ServicePacksDialog, "exec", register_pack)
    studio.manage_service_packs()
    assert studio.project["service_requirements"] == {"actions.run": "1"}
    assert studio.python_snapshot is None
    assert not studio.running
    saved = tmp_path / "services.macroproj"
    assert studio.write(saved)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(saved), ""))
    studio.open()
    assert studio.project["service_manifests"][0]["application_pack"]["id"] == "desktop.essentials"
    target = tmp_path / "authoring.zip"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    studio.export_authoring()
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        context = json.loads(archive.read("project-context.json"))
        assert {action["id"] for action in context["actions"]} >= {"desktop.confirm"}
    studio.start()
    wait_until(lambda: "해제 · scan:1C" in studio.logs.toPlainText() and not studio.running)
    assert studio.session_state == "STOPPED"
    assert studio.session_mode == "dry"


@pytest.mark.parametrize("metadata", [None, {}, {"active_mode": "bad", "python": {}},
                                     {"active_mode": "python", "python": {"source": 123}}])
def test_invalid_editor_metadata_is_rejected(metadata):
    with pytest.raises(ValueError):
        validate_editor({"schema_version": 2, "editor": metadata})
