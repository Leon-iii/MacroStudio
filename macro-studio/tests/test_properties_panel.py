import json

import pytest

pytest.importorskip("PySide6.QtWebEngineWidgets")
from PySide6.QtWidgets import QApplication, QFileDialog, QWidget

from macro_studio.gui import Studio


def test_properties_recover_legacy_events_and_navigate_saved_workflow_calls(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(Studio, "build_canvas", lambda self: QWidget())
    monkeypatch.setattr(Studio, "load_targets", lambda self: None)
    studio = Studio()
    try:
        block = {"type": "ms_emit_event", "id": "emit", "fields": {"NAME": " ready "}}
        studio.project["workspace"]["blocks"]["blocks"][0]["next"]["block"]["next"] = {"block": block}
        del studio.project["events"]
        studio.load_workspace()
        assert studio.properties.events.item(0).text() == "ready"

        navigated = []
        studio.bridge.navigateBlock.connect(navigated.append)
        root = studio.properties.workflows.topLevelItem(0)
        for index in range(root.childCount()):
            studio.properties.workflows.itemClicked.emit(root.child(index), 0)
        assert navigated == ["example-workflow", "example-start-call", "example-key-call"]

        studio.properties.addVariable.emit("rate", 2.5)
        studio.properties.addEvent.emit("unused")
        path = tmp_path / "saved.macroproj"
        assert studio.write(path)
        saved = json.loads(path.read_text(encoding="utf-8"))
        assert saved["globals"] == {"rate": 2.5}
        assert saved["events"] == ["ready", "unused"]

        studio.properties.addVariable.emit("other", True)
        studio.dirty = False
        monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
        studio.open()
        assert studio.properties.variables.count() == 1
        assert studio.properties.variables.item(0).text() == "rate  ·  2.5"
        assert studio.properties.events.count() == 2
    finally:
        studio.dirty = False
        studio.close()
        studio.deleteLater()
        app.processEvents()
