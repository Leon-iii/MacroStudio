import pytest

pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from macro_studio.image_assets import ImageAssetsDialog, asset_references, encode_image, store_asset, validate_assets
from macro_studio.compiler import compile_project
from macro_studio.perception import CaptureRegion, CaptureSourceInfo
from macro_studio.vision_dialog import PreviewDialog, ProfileDialog, SelectionLabel
from macro_studio.vision_profiles import validate_profiles


def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    return QApplication.instance() or QApplication([])


def test_project_profile_manager_clones_and_persists_a_valid_profile(monkeypatch):
    app(monkeypatch)
    project = {"vision_profiles": {}, "image_assets": {},
               "workspace": {"blocks": {"blocks": []}}}
    changes = []
    previewed, app_defaults = [], []
    dialog = ProfileDialog(None, project, lambda: changes.append(True),
                           application_default="builtin.balanced",
                           application_default_changed=app_defaults.append,
                           preview_requested=lambda profile_id, profile: previewed.append((profile_id, profile)))
    try:
        dialog.clone_profile()
        profile_id = dialog.items.currentItem().data(Qt.ItemDataRole.UserRole)
        assert profile_id.startswith("user.")
        dialog.name.setText("작업용")
        dialog.enter.setValue(0.94)
        dialog.exit.setValue(0.86)
        dialog.save_selected()
        validate_profiles(project["vision_profiles"])
        assert project["vision_profiles"][profile_id]["name"] == "작업용"
        assert changes
        dialog.preview_selected()
        assert previewed[-1][0] == profile_id
        assert previewed[-1][1]["enter_threshold"] == 0.94
        dialog.app_default.setCurrentIndex(dialog.app_default.findData("builtin.flexible"))
        assert app_defaults == ["builtin.flexible"]
    finally:
        dialog.close()


def test_asset_metadata_and_vision_block_references_are_preserved(monkeypatch):
    app(monkeypatch)
    asset_id = "asset:screen"
    image = QImage(12, 8, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    assets = {asset_id: {"name": "확인", "png": encode_image(image), "source": "screen_selection",
                         "capture_metadata": {"source_id": "monitor.1"},
                         "recognition_profile_id": "builtin.strict"}}
    validate_assets(assets)
    workspace = {"blocks": {"blocks": [{"type": "ms_vision_find", "fields": {"IMAGE": asset_id}}]}}
    assert asset_references(workspace, asset_id) == 1


def test_first_import_is_stored_and_identical_pngs_reuse_the_existing_asset(monkeypatch):
    app(monkeypatch)
    image = QImage(12, 8, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    png = encode_image(image)
    assets = {}
    asset_id, created = store_asset(assets, "Button", png)
    duplicate_id, duplicate_created = store_asset(assets, "Button copy", png)
    assert created and asset_id in assets
    assert not duplicate_created and duplicate_id == asset_id
    assert assets[asset_id]["name"] == "Button"
    validate_assets(assets)


def test_image_library_stores_a_first_clipboard_import(monkeypatch):
    app(monkeypatch)
    image = QImage(18, 12, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    monkeypatch.setattr(QApplication, "clipboard", lambda: type("Clipboard", (), {"image": lambda self: image})())
    assets, changes = {}, []
    dialog = ImageAssetsDialog(None, assets, lambda _asset_id: 0, lambda: changes.append(True))
    try:
        dialog.import_image(clipboard=True)
        assert len(assets) == 1
        asset = next(iter(assets.values()))
        assert asset["name"] == "붙여넣은 이미지"
        assert asset["source"] == "import"
        assert len(asset["sha256"]) == 64
        assert changes
    finally:
        dialog.close()


def test_preview_is_idle_until_user_starts_capture(monkeypatch):
    app(monkeypatch)
    source = CaptureSourceInfo("monitor.1", CaptureRegion(0, 0, 640, 480))
    dialog = PreviewDialog(None, {"image_assets": {}, "vision_profiles": {}}, ".", [source])
    try:
        assert dialog.worker is None
        assert not dialog.timer.isActive()
        assert "640 × 480" in dialog.source.itemText(0)
        assert "CaptureRegion" not in dialog.source.itemText(0)
        assert dialog.diagnostics.text() == "캡처가 꺼져 있습니다."
    finally:
        dialog.close()


def test_template_capture_uses_the_frozen_frame_and_minimum_selection():
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    label = SelectionLabel(QPixmap.fromImage(image), image)
    label.start, label.end = QPoint(10, 5), QPoint(29, 19)
    crop, bounds = label.crop()
    assert bounds == (10, 5, 20, 15)
    assert (crop.width(), crop.height()) == (20, 15)


def test_template_capture_clamps_drag_that_ends_outside_label():
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    label = SelectionLabel(QPixmap.fromImage(image), image)
    label.start, label.end = QPoint(-10, -5), QPoint(15, 20)
    crop, bounds = label.crop()
    assert bounds == (0, 0, 16, 21)
    assert (crop.width(), crop.height()) == (16, 21)


def test_capture_save_without_selection_stays_open(monkeypatch):
    app(monkeypatch)
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    from macro_studio.vision_dialog import CaptureDialog
    from types import SimpleNamespace
    frame = SimpleNamespace(image_bgr=__import__("numpy").zeros((40, 80, 3), dtype="uint8"))
    dialog = CaptureDialog(None, frame)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    try:
        dialog.accept_selection()
        assert dialog.result() == 0
        assert dialog.selection is None
        assert warnings
    finally:
        dialog.close()


def test_color_alias_blocks_compile_to_existing_hsv_vision_operations():
    color_visible = {
        "type": "ms_vision_color_visible", "id": "color-condition",
        "fields": {"PROFILE": "inherit", "MAX_AGE_MS": "", "ROI_LEFT": 2, "ROI_TOP": 3,
                   "ROI_WIDTH": 50, "ROI_HEIGHT": 40, "HSV_LOWER": "20,40,50",
                   "HSV_UPPER": "70,255,255", "MIN_PIXEL_COUNT": "12", "MIN_FRACTION": "0.02"},
    }
    color_find = {
        "type": "ms_vision_color_find", "id": "color-search",
        "fields": {"RESULT": "colour", "PROFILE": "inherit", "TIMEOUT_MS": 1000,
                   "ROI_LEFT": 2, "ROI_TOP": 3, "ROI_WIDTH": 50, "ROI_HEIGHT": 40,
                   "MAX_AGE_MS": "", "HSV_LOWER": "20,40,50", "HSV_UPPER": "70,255,255",
                   "MIN_PIXEL_COUNT": "12", "MIN_FRACTION": "0.02"},
    }
    project = {"project_id": "color-ui", "workspace": {"blocks": {"blocks": [
        {"type": "ms_start", "id": "start", "inputs": {"GUARD": {"block": color_visible}},
         "next": {"block": color_find}},
    ]}}}
    program, _ = compile_project(project)
    handler, = program["handlers"]
    guard = handler["guard"]
    step, = program["workflows"][handler["workflow_id"]]["steps"]
    assert guard["op"] == "vision_visible"
    assert guard["spec"]["detector"] == "hsv"
    assert guard["spec"]["hsv_lower"] == [20, 40, 50]
    assert step["op"] == "vision_find"
    assert step["spec"]["detector"] == "hsv"
    assert step["spec"]["roi"] == {"left": 2, "top": 3, "width": 50, "height": 40}


def test_blank_vision_fields_keep_the_selected_profile_values():
    visible = {"type": "ms_vision_visible", "id": "visible", "fields": {
        "DETECTOR": "template", "IMAGE": "asset:button", "PROFILE": "inherit",
        "MAX_AGE_MS": "", "ENTER_THRESHOLD": "", "EXIT_THRESHOLD": "",
        "ROI_LEFT": 0, "ROI_TOP": 0, "ROI_WIDTH": 0, "ROI_HEIGHT": 0,
    }}
    project = {"project_id": "profile-ui", "image_assets": {"asset:button": {"name": "Button"}},
               "workspace": {"blocks": {"blocks": [
                   {"type": "ms_start", "id": "start", "inputs": {"GUARD": {"block": visible}}},
               ]}}}
    program, _ = compile_project(project)
    guard = program["handlers"][0]["guard"]
    assert guard["spec"]["enter_threshold"] == 0.9
    assert guard["spec"]["exit_threshold"] == 0.82
    assert guard["spec"]["max_age_ms"] == 250


def test_compiler_receives_application_vision_profile():
    project = {"project_id": "app-profile", "image_assets": {"asset:button": {"name": "Button"}},
               "workspace": {"blocks": {"blocks": [{"type": "ms_start", "id": "start",
                   "inputs": {"GUARD": {"block": {"type": "ms_vision_visible", "id": "visible",
                       "fields": {"DETECTOR": "template", "IMAGE": "asset:button", "PROFILE": "inherit",
                                  "MAX_AGE_MS": "", "ENTER_THRESHOLD": "", "EXIT_THRESHOLD": ""}}}}}]}}}
    program, _ = compile_project(project, application_vision_profile="builtin.strict")
    assert program["handlers"][0]["guard"]["spec"]["enter_threshold"] == 0.95
