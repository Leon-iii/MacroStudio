import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from PySide6.QtCore import QByteArray, QIODevice, QObject, QProcess, QSize, QTimer, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from .block_help import BlockHelp
from .compiler import compile_project
from .image_assets import ImageAssetsDialog, asset_files, asset_references, validate_assets
from .ir import normalize_hotkey
from .key_capture import KeyCaptureDialog
from .live import ArmStore
from .sensors import sensor_specs

ASSETS = Path(__file__).with_name("editor")
STATES = {"RUNNING": "실행 중", "PAUSED": "일시정지", "BREAKPOINT": "디버거에서 멈춤",
          "STOPPED": "정지", "ERROR": "오류"}
STYLE = """
QWidget { background: #f6f8fb; color: #19283b; font-family: 'Malgun Gothic'; font-size: 13px; }
QMainWindow, QMenuBar { background: #f6f8fb; }
QMenuBar { padding: 3px 12px; }
QMenuBar::item:selected, QMenu::item:selected { background: #e1e9f5; }
QWidget#toolbar { background: #fff; border-top: 1px solid #d8e0ea; border-bottom: 1px solid #cbd5e1; }
QWidget#toolbarIdentity, QWidget#toolbar QLabel#title { background: transparent; }
QLabel#title { font-size: 21px; font-weight: 700; }
QLabel#section { font-size: 15px; font-weight: 700; }
QLabel#muted { color: #475b74; }
QLabel#mode { color: #274775; background: #e9f0fb; padding: 5px 9px; border-radius: 4px; font-weight: 700; }
QLabel#mode[armed="true"] { color: #733c0d; background: #f8ead4; }
QPushButton, QComboBox, QLineEdit { background: #fff; border: 1px solid #b9c7d7; border-radius: 5px; padding: 6px 10px; min-height: 24px; }
QPushButton:hover { background: #eef3fa; }
QPushButton:focus, QLineEdit:focus, QComboBox:focus { border: 2px solid #2455cb; padding: 5px 9px; }
QPushButton[transportIcon="true"] { padding: 0; min-height: 0; border-radius: 7px; }
QPushButton#transport { background: #234fbf; border-color: #234fbf; }
QPushButton#transport:hover { background: #1a429f; }
QPushButton#transport[stopping="true"] { background: #a12e38; border-color: #a12e38; }
QPushButton#transport[stopping="true"]:hover { background: #86232c; }
QPushButton#arm[armed="true"] { background: #f8ead4; border-color: #ce9f61; }
QPushButton[transportIcon="true"]:focus { border: 2px solid #4877e4; }
QPushButton:disabled, QPushButton#transport:disabled { color: #576b82; background: #e8edf3; border-color: #d0d9e4; }
QWidget#inspector { border-left: 1px solid #cbd5e1; }
QPlainTextEdit { background: #fff; border: 1px solid #c7d2df; border-radius: 5px; padding: 6px; }
QLabel#footer { color: #435670; padding: 6px 15px; border-top: 1px solid #cbd5e1; }
"""
SHIELD = '<path d="M12 3.2 19 6v5.5c0 4-2.8 7-7 9.3-4.2-2.3-7-5.3-7-9.3V6Z"/>'
TRANSPORT_ICONS = {
    "arm": (SHIELD, "#274775"),
    "armed": (SHIELD + '<path d="m8.6 11.8 2.2 2.2 4.5-4.5"/>', "#733c0d"),
    "play": ('<path d="M8 4.5 20 12 8 19.5Z" fill="currentColor" stroke="none"/>', "#fff"),
    "stop": ('<rect x="6" y="6" width="12" height="12" rx="1.4" fill="currentColor" stroke="none"/>', "#fff"),
    "pause": ('<path d="M9 5.5v13M15 5.5v13" stroke-width="2.8"/>', "#274775"),
    "resume": ('<path d="M8 4.5 20 12 8 19.5Z" fill="currentColor" stroke="none"/>', "#274775"),
}


def transport_icon(shape, color):
    source = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">'
              f'<g fill="none" stroke="{color}" stroke-width="1.9" stroke-linecap="round" '
              f'stroke-linejoin="round">{shape.replace("currentColor", color)}</g></svg>')
    renderer = QSvgRenderer(QByteArray(source.encode("ascii")))
    icon = QIcon()
    for size in (20, 30, 40, 60):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        renderer.render(painter)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


class Bridge(QObject):
    loadWorkspace = Signal(str)
    focusBlock = Signal(str)
    loaded = Signal()
    updated = Signal(str)
    helpRequested = Signal(str)
    imageAssetsChanged = Signal(str)

    @Slot()
    def ready(self):
        self.loaded.emit()

    @Slot(str)
    def changed(self, value):
        self.updated.emit(value)

    @Slot(str)
    def requestHelp(self, block_type):
        self.helpRequested.emit(block_type)

    @Slot(str, result=str)
    def chooseColour(self, initial):
        colour = QColorDialog.getColor(QColor(initial), self.parent(), "색상 선택",
                                       QColorDialog.ColorDialogOption.DontUseNativeDialog)
        return colour.name() if colour.isValid() else ""

    @Slot(str, result=str)
    def chooseKey(self, mode):
        if self.parent().running:
            return ""
        dialog = KeyCaptureDialog(self.parent(), mode)
        dialog.exec()
        value = dialog.value
        dialog.deleteLater()
        return value


class LocalRequests(QWebEngineUrlRequestInterceptor):
    def interceptRequest(self, info):
        url = info.requestUrl()
        allowed = url.scheme() in ("qrc", "data", "blob")
        if url.isLocalFile():
            allowed = Path(url.toLocalFile()).resolve().is_relative_to(ASSETS.resolve())
        info.block(not allowed)


class EditorPage(QWebEnginePage):
    def acceptNavigationRequest(self, url, kind, main_frame):
        return url.isLocalFile() and Path(url.toLocalFile()).resolve() == (ASSETS / "index.html").resolve()


def new_project(example=False):
    blocks = []
    if example:
        blocks = [
            {"type": "ms_start", "id": "example-start", "x": 72, "y": 70, "inputs": {
                "BODY": {"block": {"type": "ms_call", "id": "example-start-call", "fields": {"NAME": "A 누르기"}}},
            }},
            {"type": "ms_hotkey", "id": "example-hotkey", "x": 72, "y": 318, "fields": {"KEY": "CTRL+F7"}, "inputs": {
                "BODY": {"block": {"type": "ms_call", "id": "example-key-call", "fields": {"NAME": "A 누르기"}}},
            }},
            {"type": "ms_workflow", "id": "example-workflow", "x": 72, "y": 566, "fields": {"NAME": "A 누르기"}, "inputs": {
                "BODY": {"block": {"type": "ms_key_tap", "id": "example-tap", "fields": {"KEY": "A", "MS": 50}}},
            }},
        ]
    return {"schema_version": 1, "project_id": uuid4().hex, "name": "예제 프로젝트" if example else "제목 없는 프로젝트",
            "workspace": {"blocks": {"languageVersion": 0, "blocks": blocks}}, "globals": {}, "image_assets": {}}


class Studio(QMainWindow):
    def __init__(self):
        super().__init__()
        self.resize(1260, 790)
        self.setMinimumSize(960, 640)
        self.project = new_project(example=True)
        self.path = None
        self.dirty = False
        self.running = False
        self.closing = False
        self.session_state = "STOPPED"
        self.session_mode = None
        self.store = ArmStore.local() if sys.platform == "win32" else None
        self.bridge = Bridge(self)
        self.process = QProcess(self)
        self.runner_assets = None
        self.process.readyReadStandardOutput.connect(self.read_runner)
        self.process.readyReadStandardError.connect(self.read_stderr)
        self.process.finished.connect(self.runner_finished)
        self.process.errorOccurred.connect(lambda _: self.log(self.process.errorString()))
        self.heartbeat = QTimer(self)
        self.heartbeat.setInterval(250)
        self.heartbeat.timeout.connect(lambda: self.send("heartbeat"))
        self.pending_output = b""
        self.last_error_block = ""
        self.editor_ready = False
        self.build()
        self.refresh()

    def build(self):
        self.setStyleSheet(STYLE)
        self.block_help = BlockHelp(self, ASSETS / "block_help.json")
        self.bridge.helpRequested.connect(self.block_help.show_block)
        menu = self.menuBar().addMenu("파일")
        for title, shortcut, slot in (
            ("새 프로젝트", "Ctrl+N", self.create), ("열기…", "Ctrl+O", self.open),
            ("저장", "Ctrl+S", self.save), ("다른 이름으로 저장…", "Ctrl+Shift+S", self.save_as),
        ):
            action = menu.addAction(title, slot)
            action.setShortcut(shortcut)
        images = self.menuBar().addMenu("이미지")
        self.manage_images = images.addAction("이미지 에셋 관리…", self.open_image_assets)
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.setCentralWidget(root)

        toolbar = QWidget(objectName="toolbar")
        row = QGridLayout(toolbar)
        row.setContentsMargins(18, 10, 18, 10)
        row.setHorizontalSpacing(12)
        row.setColumnStretch(0, 1)
        row.setColumnStretch(2, 1)
        identity = QWidget(objectName="toolbarIdentity")
        identity_row = QHBoxLayout(identity)
        identity_row.setContentsMargins(0, 0, 0, 0)
        identity_row.addWidget(QLabel("Macro Studio", objectName="title"))
        self.mode = QLabel(objectName="mode")
        identity_row.addWidget(self.mode)
        row.addWidget(identity, 0, 0, Qt.AlignmentFlag.AlignLeft)
        self.verify_button = self.button("검증", self.verify)
        row.addWidget(self.verify_button, 0, 2, Qt.AlignmentFlag.AlignRight)
        self.transport_icons = {name: transport_icon(*shape) for name, shape in TRANSPORT_ICONS.items()}
        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.arm_button = self.icon_button("arm", self.toggle_arm)
        self.transport_button = self.icon_button("transport", self.toggle_run)
        self.pause = self.icon_button("pause", self.toggle_pause)
        for button in (self.arm_button, self.transport_button, self.pause):
            controls.addWidget(button)
        row.addLayout(controls, 0, 1)
        layout.addWidget(toolbar)

        body = QHBoxLayout()
        body.setSpacing(0)
        self.view = QWebEngineView()
        page = EditorPage(self.view)
        self.view.setPage(page)
        self.requests = LocalRequests(page)
        page.profile().setUrlRequestInterceptor(self.requests)
        page.settings().setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, False)
        channel = QWebChannel(page)
        channel.registerObject("bridge", self.bridge)
        page.setWebChannel(channel)
        self.bridge.loaded.connect(self.ready)
        self.bridge.updated.connect(self.workspace_changed)
        self.view.loadFinished.connect(self.page_loaded)
        self.view.load(QUrl.fromLocalFile(str(ASSETS / "index.html")))
        body.addWidget(self.view, 1)

        inspector = QWidget(objectName="inspector")
        inspector.setFixedWidth(276)
        side = QVBoxLayout(inspector)
        side.setContentsMargins(16, 18, 16, 14)
        side.setSpacing(9)
        side.addWidget(QLabel("실행 설정", objectName="section"))
        self.state_label = QLabel(objectName="muted")
        side.addWidget(self.state_label)
        self.machine_label = QLabel(objectName="muted", wordWrap=True)
        self.machine_label.setTextFormat(Qt.TextFormat.PlainText)
        self.machine_label.hide()
        side.addWidget(self.machine_label)
        self.stats = QLabel("누름 0 · 해제 0\n누락 주기 0 · 오류 0", objectName="muted")
        self.stats.setAccessibleName("실행 통계")
        side.addWidget(self.stats)
        form = QFormLayout()
        self.targets = QComboBox()
        self.targets.setMinimumWidth(140)
        self.targets.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        form.addRow("대상 창", self.targets)
        self.target_button = self.button("창 목록 새로 고침", self.load_targets)
        form.addRow(self.target_button)
        self.globals = QLineEdit("{}")
        self.globals.setToolTip('전역 변수 초기값 JSON. 예: {"enabled": true}')
        self.globals.textEdited.connect(self.modified)
        form.addRow("전역 변수", self.globals)
        side.addLayout(form)
        debug_row = QHBoxLayout()
        self.debug_button = self.button("디버그 시작", lambda: self.start(debug=True))
        self.debug_button.setToolTip("블록을 우클릭해 브레이크포인트를 설정하세요.")
        self.step_button = self.button("한 단계", lambda: self.send("step"))
        debug_row.addWidget(self.debug_button)
        debug_row.addWidget(self.step_button)
        side.addLayout(debug_row)
        self.hotkey = QLineEdit("CTRL+F7")
        self.hotkey.setAccessibleName("모의 핫키")
        self.simulate = self.button("보내기", self.send_hotkey)
        sim = QHBoxLayout()
        sim.addWidget(self.hotkey, 1)
        sim.addWidget(self.simulate)
        side.addWidget(QLabel("드라이런 핫키"))
        side.addLayout(sim)
        side.addSpacing(12)
        side.addWidget(QLabel("실행 로그", objectName="section"))
        self.logs = QPlainTextEdit(readOnly=True)
        self.logs.setMaximumBlockCount(200)
        self.logs.setAccessibleName("실행 로그")
        self.logs.setPlaceholderText("검증 및 실행 결과가 여기에 표시됩니다.")
        side.addWidget(self.logs, 1)
        self.jump = self.button("오류 블록 보기", self.jump_to_error)
        self.jump.setEnabled(False)
        side.addWidget(self.jump)
        body.addWidget(inspector)
        layout.addLayout(body, 1)
        self.footer = QLabel(objectName="footer")
        layout.addWidget(self.footer)
        self.load_targets()

    @staticmethod
    def button(text, action, name=""):
        button = QPushButton(text, objectName=name)
        button.clicked.connect(action)
        return button

    def icon_button(self, name, action):
        button = self.button("", action, name)
        button.setProperty("transportIcon", True)
        button.setFixedSize(42, 42)
        button.setIconSize(QSize(20, 20))
        return button

    def set_icon_action(self, button, icon, label):
        button.setIcon(self.transport_icons[icon])
        button.setToolTip(label)
        button.setAccessibleName(label)

    @staticmethod
    def set_style_state(widget, name, value):
        if widget.property(name) != value:
            widget.setProperty(name, value)
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def ready(self):
        self.editor_ready = True
        self.load_workspace()

    def page_loaded(self, ok):
        if not ok:
            self.log("블록 편집기를 불러오지 못했습니다.")

    def load_workspace(self):
        self.globals.setText(json.dumps(self.project.get("globals", {}), ensure_ascii=False))
        self.publish_image_assets()
        self.bridge.loadWorkspace.emit(json.dumps(self.project["workspace"], ensure_ascii=False))
        self.refresh()

    def publish_image_assets(self):
        assets = self.project.get("image_assets", {})
        self.bridge.imageAssetsChanged.emit(json.dumps({key: asset["name"] for key, asset in assets.items()},
                                                       ensure_ascii=False))

    def open_image_assets(self):
        if self.running:
            return
        dialog = ImageAssetsDialog(
            self, self.project.setdefault("image_assets", {}),
            lambda asset_id: asset_references(self.project["workspace"], asset_id), self.image_assets_changed,
        )
        dialog.exec()

    def image_assets_changed(self):
        self.publish_image_assets()
        self.modified()

    def workspace_changed(self, value):
        self.project["workspace"] = json.loads(value)
        self.modified()

    def modified(self):
        self.dirty = True
        self.refresh()

    def armed(self):
        try:
            return bool(self.path and self.store and self.store.armed(self.path, self.project))
        except (OSError, ValueError):
            return False

    def refresh(self):
        armed = self.armed() if not self.running else self.session_mode == "live"
        name = self.project["name"]
        changed = " • 저장 안 됨" if self.dirty else ""
        self.setWindowTitle(f"{name}{changed} | Macro Studio")
        self.mode.setText("실입력 · ARM" if armed else "드라이런")
        self.set_style_state(self.mode, "armed", armed)
        self.footer.setText(f"{name}{changed}    |    {self.path.name if self.path else '저장된 파일 없음'}")
        self.state_label.setText(STATES.get(self.session_state, "대상 창 전환 대기") if self.running
                                 else STATES[self.session_state] if self.session_state in STATES else "준비")
        self.transport_button.setEnabled(self.running or self.editor_ready)
        self.set_style_state(self.transport_button, "stopping", self.running)
        self.set_icon_action(self.transport_button, "stop" if self.running else "play",
                             "정지" if self.running else "시작")
        self.verify_button.setEnabled(self.editor_ready)
        self.manage_images.setEnabled(self.editor_ready and not self.running)
        at_breakpoint = self.running and self.session_state == "BREAKPOINT"
        self.debug_button.setEnabled(self.editor_ready and not self.running and not armed)
        self.step_button.setEnabled(at_breakpoint)
        self.pause.setEnabled(self.running and self.session_state in ("RUNNING", "PAUSED", "BREAKPOINT"))
        resumable = self.session_state in ("PAUSED", "BREAKPOINT")
        self.set_icon_action(self.pause, "resume" if resumable else "pause", "재개" if resumable else "일시정지")
        self.arm_button.setEnabled(bool(self.store) and not self.running)
        self.set_style_state(self.arm_button, "armed", armed)
        self.set_icon_action(self.arm_button, "armed" if armed else "arm", "ARM 해제" if armed else "ARM 설정")
        self.targets.setEnabled(bool(self.store) and not self.running)
        self.target_button.setEnabled(bool(self.store) and not self.running)
        sim = self.running and not armed and self.session_state == "RUNNING"
        self.simulate.setEnabled(sim)
        self.hotkey.setEnabled(sim)

    def log(self, text):
        self.logs.appendPlainText(text)

    def load_targets(self):
        selected = self.targets.currentData()
        self.targets.clear()
        self.targets.addItem("창 선택", None)
        if self.store:
            from .win32 import WindowsDesktop
            try:
                for item in WindowsDesktop().windows():
                    if item["hwnd"] != int(self.winId()):
                        self.targets.addItem(item["title"], item["hwnd"])
                self.targets.setCurrentIndex(max(0, self.targets.findData(selected)))
            except OSError as error:
                self.log(str(error))

    def confirm_changes(self):
        if not self.dirty:
            return True
        selected = QMessageBox.question(self, "저장되지 않은 변경", "현재 프로젝트의 변경 사항을 저장할까요?",
                                        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard |
                                        QMessageBox.StandardButton.Cancel)
        return selected == QMessageBox.StandardButton.Discard or (
            selected == QMessageBox.StandardButton.Save and self.save())

    def create(self):
        if not self.running and self.confirm_changes():
            self.project, self.path, self.dirty = new_project(), None, False
            self.load_workspace()

    def open(self):
        if self.running or not self.confirm_changes():
            return
        path, _ = QFileDialog.getOpenFileName(self, "프로젝트 열기", "", "Macro Studio (*.macroproj)")
        if not path:
            return
        try:
            project = json.loads(Path(path).read_text(encoding="utf-8-sig"))
            if (not isinstance(project, dict) or project.get("schema_version") != 1
                    or not isinstance(project.get("project_id"), str)
                    or not isinstance(project.get("workspace"), dict)
                    or not isinstance(project.get("name"), str)):
                raise ValueError("지원하지 않는 프로젝트 형식입니다.")
            validate_assets(project.get("image_assets", {}))
            self.project, self.path, self.dirty = project, Path(path), False
            self.load_workspace()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "열기 실패", str(error))

    def save(self):
        return self.write(self.path) if self.path else self.save_as()

    def save_as(self):
        path, _ = QFileDialog.getSaveFileName(self, "프로젝트 저장", self.project["name"], "Macro Studio (*.macroproj)")
        if not path:
            return False
        target = Path(path)
        if target.suffix.lower() != ".macroproj":
            target = target.with_suffix(".macroproj")
        return self.write(target, copy=self.path is not None and target.resolve() != self.path.resolve())

    def write(self, path, copy=False):
        from PySide6.QtCore import QSaveFile
        try:
            values = self.global_values()
            project = dict(self.project, globals=values, name=path.stem)
            if copy:
                project["project_id"] = uuid4().hex
            output = QSaveFile(str(path))
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):
                raise OSError(output.errorString())
            data = json.dumps(project, ensure_ascii=False, indent=2).encode("utf-8")
            if output.write(data) != len(data) or not output.commit():
                raise OSError(output.errorString())
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "저장 실패", str(error))
            return False
        self.project, self.path, self.dirty = project, path, False
        self.refresh()
        return True

    def global_values(self):
        values = json.loads(self.globals.text())
        if not isinstance(values, dict):
            raise ValueError("전역 변수는 JSON 객체로 입력하세요.")
        return values

    def jump_to_error(self):
        self.bridge.focusBlock.emit(self.last_error_block)

    def show_error(self, message, block=""):
        self.log(message)
        self.last_error_block = block or ""
        self.jump.setEnabled(bool(block))
        self.jump_to_error()

    def verify(self):
        self.last_error_block = ""
        self.jump.setEnabled(False)
        self.jump_to_error()
        try:
            program, detached = compile_project(dict(self.project, globals=self.global_values()))
            sensor_specs(program)
        except (ValueError, KeyError, TypeError, RecursionError) as error:
            match = re.search(r"\(([^()]*)\)", str(error))
            self.show_error(f"검증 실패 · {error}", match[1] if match else "")
            return None
        self.log("검증 완료" + (f" · 연결되지 않은 블록 {len(detached)}개 제외" if detached else ""))
        return program

    def toggle_arm(self):
        if not self.store:
            return
        if not self.path and not self.save():
            return
        armed = self.armed()
        if not armed:
            if self.verify() is None:
                return
            if QMessageBox.question(self, "실입력 허용", "이 프로젝트를 시작하면 선택한 창에 실제 키 입력을 보낼 수 있습니다. ARM을 설정할까요?") != QMessageBox.StandardButton.Yes:
                return
        try:
            self.store.set(self.path, self.project, not armed)
        except (OSError, ValueError) as error:
            self.log(str(error))
        self.refresh()

    def start(self, debug=False):
        program = self.verify()
        if program is None or self.running:
            return
        armed = self.armed()
        if debug and armed:
            return
        selected = self.targets.currentData()
        target = selected if armed else None
        if armed and not target:
            self.log("실입력에 사용할 대상 창을 선택하세요.")
            self.targets.setFocus()
            return
        ids = [spec["image"] for spec in sensor_specs(program)
               if spec["op"] == "region_image" and spec["image"].startswith("asset:")]
        images = {}
        if ids:
            try:
                self.runner_assets = TemporaryDirectory(prefix="macro-studio-images-", ignore_cleanup_errors=True)
                images = asset_files(self.project["image_assets"], ids, self.runner_assets.name)
            except (OSError, ValueError) as error:
                self.clear_runner_assets()
                self.log(f"이미지 에셋 준비 실패 · {error}")
                return
        data = json.dumps({"program": program, "target": target, "query_target": selected, "debug": debug,
                           "image_assets": images,
                           "project_directory": str(self.path.resolve().parent if self.path else Path.cwd())}).encode("ascii")
        if len(data) >= 4 * 1024 * 1024 - 1:
            self.clear_runner_assets()
            self.log("실행 프로그램이 4 MiB 한도를 초과했습니다.")
            return
        self.pending_output = b""
        self.stats.setText("누름 0 · 해제 0\n누락 주기 0 · 오류 0")
        self.machine_label.hide()
        self.session_mode = "live" if armed else "dry"
        self.session_state = "WAITING"
        self.running = True
        self.refresh()
        runner_args = (["--editor-runner"] if getattr(sys, "frozen", False)
                       else ["-u", "-m", "macro_studio.editor_runner"])
        self.process.start(sys.executable, runner_args)
        if not self.process.waitForStarted(1000):
            self.clear_runner_assets()
            self.running = False
            self.session_state = "ERROR"
            self.refresh()
            return
        self.process.write(data + b"\n")
        self.heartbeat.start()

    def send(self, command):
        if self.running:
            self.process.write(command.encode("ascii") + b"\n")

    def toggle_pause(self):
        if self.session_state == "BREAKPOINT":
            self.send("continue")
        else:
            self.send("resume" if self.session_state == "PAUSED" else "pause")

    def toggle_run(self):
        if self.running:
            self.send("stop")
        else:
            self.start()

    def send_hotkey(self):
        try:
            self.send("hotkey:" + normalize_hotkey(self.hotkey.text()))
        except ValueError as error:
            self.log(str(error))

    def read_runner(self):
        self.pending_output += bytes(self.process.readAllStandardOutput())
        lines = self.pending_output.split(b"\n")
        self.pending_output = lines.pop()
        for line in lines:
            try:
                message = json.loads(line)
                kind = message["kind"]
                if kind == "state":
                    if self.session_state == "BREAKPOINT":
                        self.bridge.focusBlock.emit("")
                    self.session_state = message["state"]
                    self.log(STATES[self.session_state])
                    self.refresh()
                elif kind == "breakpoint":
                    self.session_state = "BREAKPOINT"
                    self.bridge.focusBlock.emit(message["block_id"])
                    self.log("브레이크포인트 · " + message["block_id"])
                    self.refresh()
                elif kind == "inputs":
                    self.log("\n".join(f"{action} · {key}" for action, key in message["events"]))
                    if message["omitted"]:
                        self.log(f"추가 입력 {message['omitted']}건")
                elif kind == "error":
                    self.show_error(message["message"], message.get("block_id"))
                elif kind == "transition":
                    self.machine_label.setText(f"현재 상태 · {message['name']}")
                    self.machine_label.show()
                    self.log(f"상태 진입 · {message['name']}")
                elif kind == "stats":
                    self.stats.setText(f"누름 {message['pressed']} · 해제 {message['released']}\n"
                                       f"누락 주기 {message['missed']} · 오류 {message['errors']}")
                else:
                    self.log(message["message"])
            except (ValueError, KeyError):
                self.log("Runner 응답을 읽지 못했습니다.")

    def read_stderr(self):
        self.log(bytes(self.process.readAllStandardError()).decode("utf-8", errors="replace").strip())

    def runner_finished(self, code, exit_status):
        self.read_runner()
        self.clear_runner_assets()
        self.heartbeat.stop()
        self.running = False
        if code or exit_status == QProcess.ExitStatus.CrashExit or self.session_state not in ("STOPPED", "ERROR"):
            if self.session_state == "BREAKPOINT":
                self.bridge.focusBlock.emit("")
            self.session_state = "ERROR"
            self.log("Runner 연결이 끊겼습니다.")
        self.refresh()
        if self.closing:
            self.close()

    def clear_runner_assets(self):
        if self.runner_assets is not None:
            self.runner_assets.cleanup()
            self.runner_assets = None

    def closeEvent(self, event):
        if self.running:
            self.closing = True
            self.send("stop")
            event.ignore()
        elif self.confirm_changes():
            event.accept()
        else:
            self.closing = False
            event.ignore()


def main():
    app = QApplication(sys.argv)
    studio = Studio()
    studio.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
