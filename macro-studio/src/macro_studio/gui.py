import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from threading import Thread
from uuid import uuid4

from PySide6.QtCore import (
    QByteArray, QIODevice, QObject, QPointF, QProcess, QSignalBlocker, QSize, QTimer, Qt, QUrl, Signal, Slot,
)
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings, QWebEngineUrlRequestInterceptor
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import (
    QApplication, QColorDialog, QComboBox, QFileDialog, QFormLayout, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea, QStyle, QStyleOptionComboBox, QTabWidget,
    QVBoxLayout, QWidget,
)

from .block_help import BlockHelp
from .compiler import compile_project
from .external_hid.serial_link import list_ports, probe_port
from .image_assets import ImageAssetsDialog, asset_files, asset_references, validate_assets
from .ir import normalize_hotkey
from .key_capture import KeyCaptureDialog
from .live import ArmStore
from .output import HidMode, OutputKind
from .properties_panel import PropertiesPanel, workspace_properties
from .sensors import sensor_specs

ASSETS = Path(__file__).with_name("editor")


def usb_selector(port):
    vid, pid, serial = (port.get(field) for field in ("vid", "pid", "serial_number"))
    if (type(vid) is int and 0 <= vid <= 0xFFFF and type(pid) is int and 0 <= pid <= 0xFFFF
            and isinstance(serial, str) and serial.strip()):
        return {"vid": vid, "pid": pid, "serial_number": serial}
    return None


def project_output(project):
    profile = project.get("output_profile", {"output_kind": OutputKind.FAKE})
    if not isinstance(profile, dict) or profile.get("output_kind") not in tuple(OutputKind):
        raise ValueError("지원하지 않는 출력 프로필입니다.")
    if profile.get("hid_mode", HidMode.STREAMING) not in tuple(HidMode):
        raise ValueError("지원하지 않는 HID 실행 방식입니다.")
    selector = profile.get("device_selector")
    if selector is not None and (not isinstance(selector, dict) or usb_selector(selector) is None):
        raise ValueError("출력 프로필의 USB 장치 식별 정보가 잘못되었습니다.")
    return profile


STATES = {"RUNNING": "실행 중", "PAUSED": "일시정지", "BREAKPOINT": "디버거에서 멈춤",
          "STOPPED": "정지", "ERROR": "오류"}
STYLE = """
QWidget { background: #f6f8fb; color: #19283b; font-family: 'Malgun Gothic'; font-size: 9.75pt; }
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
QComboBox[flatArrow="true"] { padding-right: 26px; }
QComboBox[flatArrow="true"]::drop-down { width: 24px; border: none; background: transparent; }
QLabel#hidStatus[connection="ready"] { color: #176248; }
QLabel#hidStatus[connection="error"] { color: #912d37; }
QPushButton[transportIcon="true"] { padding: 0; min-height: 0; border-radius: 7px; }
QPushButton#transport { background: #234fbf; border-color: #234fbf; }
QPushButton#transport:hover { background: #1a429f; }
QPushButton#transport[stopping="true"] { background: #a12e38; border-color: #a12e38; }
QPushButton#transport[stopping="true"]:hover { background: #86232c; }
QPushButton#arm[armed="true"] { background: #f8ead4; border-color: #ce9f61; }
QPushButton[transportIcon="true"]:focus { border: 2px solid #4877e4; }
QPushButton:disabled, QPushButton#transport:disabled { color: #576b82; background: #e8edf3; border-color: #d0d9e4; }
QScrollArea#inspector { border: 0; border-left: 1px solid #cbd5e1; }
QScrollArea#inspector QScrollBar:vertical { background: #edf2f8; width: 10px; margin: 2px; }
QScrollArea#inspector QScrollBar::handle:vertical { background: #a8b8cc; border-radius: 3px; min-height: 32px; }
QScrollArea#inspector QScrollBar::add-line:vertical, QScrollArea#inspector QScrollBar::sub-line:vertical { height: 0; }
QScrollArea#inspector QScrollBar::add-page:vertical, QScrollArea#inspector QScrollBar::sub-page:vertical { background: transparent; }
QPlainTextEdit { background: #fff; border: 1px solid #c7d2df; border-radius: 5px; padding: 6px; }
QTabWidget#inspectorTabs::pane { border: 0; border-left: 1px solid #cbd5e1; }
QTabWidget#inspectorTabs QTabBar::tab { padding: 8px 16px; color: #475b74; }
QTabWidget#inspectorTabs QTabBar::tab:selected { border-bottom: 2px solid #2455cb; color: #19283b; }
QListWidget, QTreeWidget { background: #fff; border: 1px solid #c7d2df; border-radius: 5px; }
QListWidget::item, QTreeWidget::item { padding: 4px 5px; }
QListWidget::item:selected, QTreeWidget::item:selected { background: #e1e9f5; color: #19283b; }
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
    "refresh": ('<path d="M20 11a8 8 0 0 0-14-5L4 8m0-5v5h5m-5 5a8 8 0 0 0 14 5l2-2m0 5v-5h-5"/>',
                "#274775"),
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


class FlatComboBox(QComboBox):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.setProperty("flatArrow", True)

    def paintEvent(self, event):
        super().paintEvent(event)
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        arrow = self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                            QStyle.SubControl.SC_ComboBoxArrow, self)
        center = arrow.center()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor("#274775" if self.isEnabled() else "#576b82")
        painter.setPen(QPen(color, 1.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        tip = QPointF(center.x(), center.y() + 2)
        painter.drawLine(QPointF(center.x() - 4, center.y() - 2), tip)
        painter.drawLine(tip, QPointF(center.x() + 4, center.y() - 2))
        painter.end()


class Bridge(QObject):
    loadWorkspace = Signal(str)
    focusBlock = Signal(str)
    activeBlocksChanged = Signal(list)
    loaded = Signal()
    updated = Signal(str)
    helpRequested = Signal(str)
    imageAssetsChanged = Signal(str)
    propertyOptionsChanged = Signal(str)
    navigateBlock = Signal(str)

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
            {"type": "ms_start", "id": "example-start", "x": 72, "y": 70,
             "next": {"block": {"type": "ms_call", "id": "example-start-call", "fields": {"NAME": "A 누르기"}}}},
            {"type": "ms_hotkey", "id": "example-hotkey", "x": 72, "y": 318, "fields": {"KEY": "CTRL+F7"},
             "next": {"block": {"type": "ms_call", "id": "example-key-call", "fields": {"NAME": "A 누르기"}}}},
            {"type": "ms_workflow", "id": "example-workflow", "x": 72, "y": 566, "fields": {"NAME": "A 누르기"}, "inputs": {
                "BODY": {"block": {"type": "ms_key_tap", "id": "example-tap", "fields": {"KEY": "A", "MS": 50}}},
            }},
        ]
    return {"schema_version": 1, "project_id": uuid4().hex, "name": "예제 프로젝트" if example else "제목 없는 프로젝트",
            "workspace": {"blocks": {"languageVersion": 0, "blocks": blocks}}, "globals": {},
            "events": [], "image_assets": {}}


class Studio(QMainWindow):
    hid_probe_result = Signal(int, str, object, str)

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
        self.property_options = None
        self.hid_ports = {}
        self.hid_devices = {}
        self.checked_port = None
        self.hid_issue = ""
        self.probe_serial = 0
        self.probing = False
        self.running_hid_port = None
        self.hid_timer = QTimer(self, interval=1500)
        self.hid_timer.timeout.connect(self.load_hid_ports)
        self.hid_probe_result.connect(self.finish_hid_probe)
        self.build()
        self.update_properties(publish=True)
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
        identity_row.addSpacing(10)
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
        body.addWidget(self.build_canvas(), 1)

        inspector_scroll = QScrollArea(objectName="inspector")
        inspector_scroll.setWidgetResizable(True)
        inspector_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        inspector = QWidget()
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
        self.targets = FlatComboBox(objectName="targetWindow")
        self.targets.setAccessibleName("대상 창")
        self.targets.setMinimumWidth(100)
        self.targets.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        target_row = QHBoxLayout()
        target_row.setSpacing(6)
        target_row.addWidget(self.targets, 1)
        self.target_button = self.icon_button("refreshTargets", self.load_targets, size=38)
        self.set_icon_action(self.target_button, "refresh", "창 목록 새로 고침")
        target_row.addWidget(self.target_button)
        form.addRow("대상 창", target_row)
        self.output = FlatComboBox(objectName="outputMethod")
        self.output.setAccessibleName("출력 방식")
        self.output.addItem("드라이런", OutputKind.FAKE)
        self.output.addItem("SendInput", OutputKind.SENDINPUT)
        self.output.addItem("External HID", OutputKind.EXTERNAL_HID)
        form.addRow("출력 방식", self.output)
        self.hid_port = FlatComboBox(objectName="hidPort")
        self.hid_port.setAccessibleName("COM 포트")
        port_row = QHBoxLayout()
        port_row.setSpacing(6)
        port_row.addWidget(self.hid_port, 1)
        self.hid_refresh = self.icon_button("refreshHid", lambda: self.load_hid_ports(recheck=True), size=38)
        self.set_icon_action(self.hid_refresh, "refresh", "COM 포트 새로 고침")
        port_row.addWidget(self.hid_refresh)
        form.addRow("COM 포트", port_row)
        self.hid_mode = FlatComboBox(objectName="hidMode")
        self.hid_mode.setAccessibleName("HID 실행 방식")
        self.hid_mode.addItem("Streaming", HidMode.STREAMING)
        self.hid_mode.addItem("Onboard", HidMode.ONBOARD)
        form.addRow("HID 실행", self.hid_mode)
        self.hid_status = QLabel(objectName="hidStatus", wordWrap=True)
        self.hid_status.setTextFormat(Qt.TextFormat.PlainText)
        form.addRow(self.hid_status)
        self.output_form = form
        self.port_row = port_row
        self.output.currentIndexChanged.connect(self.output_changed)
        self.hid_port.currentIndexChanged.connect(self.hid_selection_changed)
        self.hid_mode.currentIndexChanged.connect(self.hid_mode_changed)
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
        self.logs.setMinimumHeight(88)
        self.logs.setMaximumBlockCount(200)
        self.logs.setAccessibleName("실행 로그")
        self.logs.setPlaceholderText("검증 및 실행 결과가 여기에 표시됩니다.")
        side.addWidget(self.logs, 1)
        self.jump = self.button("오류 블록 보기", self.jump_to_error)
        self.jump.setEnabled(False)
        side.addWidget(self.jump)
        inspector_scroll.setWidget(inspector)
        self.properties = PropertiesPanel()
        self.properties.addVariable.connect(self.set_variable)
        self.properties.removeVariable.connect(self.remove_variable)
        self.properties.addEvent.connect(self.add_event)
        self.properties.removeEvent.connect(self.remove_event)
        self.properties.focusBlock.connect(self.bridge.navigateBlock.emit)
        tabs = QTabWidget(objectName="inspectorTabs")
        tabs.setFixedWidth(286)
        tabs.addTab(inspector_scroll, "실행")
        tabs.addTab(self.properties, "속성")
        body.addWidget(tabs)
        layout.addLayout(body, 1)
        self.footer = QLabel(objectName="footer")
        layout.addWidget(self.footer)
        self.load_targets()

    def build_canvas(self):
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
        return self.view

    @staticmethod
    def button(text, action, name=""):
        button = QPushButton(text, objectName=name)
        button.clicked.connect(action)
        return button

    def icon_button(self, name, action, *, size=42):
        button = self.button("", action, name)
        button.setProperty("transportIcon", True)
        button.setFixedSize(size, size)
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
        self.update_properties(publish=True)
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
        self.update_properties()
        self.modified()

    def update_properties(self, *, publish=False):
        self.variable_uses, self.event_uses, workflows = workspace_properties(self.project["workspace"])
        variables = self.project.setdefault("globals", {})
        events = sorted(set(self.project.get("events", [])) | self.event_uses)
        self.project["events"] = events
        if variables != self.properties.initial_values or tuple(events) != self.properties.event_names or publish:
            self.properties.set_options(variables, events)
        self.properties.set_workflows(workflows)
        options = {"variable": sorted(variables), "event": events}
        if publish or self.property_options != options:
            self.property_options = options
            self.bridge.propertyOptionsChanged.emit(json.dumps(options, ensure_ascii=False))

    def set_variable(self, name, value):
        self.project["globals"][name] = value
        self.update_properties(publish=True)
        self.modified()

    def remove_variable(self, name):
        if name in self.variable_uses:
            QMessageBox.information(self, "변수 삭제", "이 변수를 사용하는 블록에서 먼저 선택을 바꾸세요.")
            return
        del self.project["globals"][name]
        self.update_properties()
        self.modified()

    def add_event(self, name):
        self.project["events"].append(name)
        self.update_properties()
        self.modified()

    def remove_event(self, name):
        if name in self.event_uses:
            QMessageBox.information(self, "이벤트 삭제", "이 이벤트를 사용하는 블록에서 먼저 선택을 바꾸세요.")
            return
        self.project["events"].remove(name)
        self.update_properties()
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
        project_armed = self.armed()
        explicit_dry_run = self.output.currentData() == OutputKind.FAKE
        armed = self.session_mode == "live" if self.running else project_armed and not explicit_dry_run
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
        self.arm_button.setEnabled(bool(self.store) and not self.running and (project_armed or not explicit_dry_run))
        self.set_style_state(self.arm_button, "armed", project_armed)
        self.set_icon_action(self.arm_button, "armed" if project_armed else "arm",
                             "ARM 해제" if project_armed else "ARM 설정")
        self.output.setEnabled(not self.running)
        self.hid_port.setEnabled(not self.running)
        self.hid_mode.setEnabled(not self.running and bool(self.checked_port))
        self.hid_refresh.setEnabled(not self.running)
        self.output_form.setRowVisible(self.port_row, self.uses_hid())
        self.output_form.setRowVisible(self.hid_mode, self.uses_hid())
        self.output_form.setRowVisible(self.hid_status, self.uses_hid())
        if self.running and self.running_hid_port:
            connection = {
                "RUNNING": ("연결 · 실행 중", "ready"),
                "PAUSED": ("연결 · 일시정지", "ready"),
                "ERROR": ("실행 오류", "error"),
                "STOPPED": ("실행 종료", ""),
            }
            label, style = connection.get(self.session_state, ("Runner 연결 대기", ""))
            self.set_hid_status(f"{self.running_hid_port} · {label}", style)
        self.targets.setEnabled(bool(self.store) and not self.running)
        self.target_button.setEnabled(bool(self.store) and not self.running)
        sim = self.running and not armed and self.session_state == "RUNNING"
        self.simulate.setEnabled(sim)
        self.hotkey.setEnabled(sim)

    def uses_hid(self):
        return self.output.currentData() == OutputKind.EXTERNAL_HID

    def real_output(self):
        return self.output.currentData() != OutputKind.FAKE and self.armed()

    def output_changed(self):
        self.project.setdefault("output_profile", {})["output_kind"] = self.output.currentData()
        self.modified()
        self.update_output_panel()

    def hid_selection_changed(self):
        profile = self.project.setdefault("output_profile", {"output_kind": self.output.currentData()})
        previously = profile.pop("device_selector", None)
        selector = self.hid_devices.get(self.hid_port.currentData())
        if selector is not None:
            profile["device_selector"] = selector
        if selector != previously:
            self.modified()
        self.check_hid_port()

    def hid_mode_changed(self):
        profile = self.project.setdefault("output_profile", {"output_kind": self.output.currentData()})
        if self.hid_mode.currentData() == HidMode.ONBOARD:
            profile["hid_mode"] = HidMode.ONBOARD
        else:
            profile.pop("hid_mode", None)
        self.modified()

    def restore_output_profile(self):
        profile = project_output(self.project)
        with QSignalBlocker(self.output), QSignalBlocker(self.hid_port), QSignalBlocker(self.hid_mode):
            self.output.setCurrentIndex(self.output.findData(profile["output_kind"]))
            self.hid_port.clear()
            self.hid_mode.setCurrentIndex(self.hid_mode.findData(profile.get("hid_mode", HidMode.STREAMING)))
        self.probe_serial += 1
        self.checked_port = None
        self.update_output_panel()

    def update_output_panel(self):
        self.refresh()
        if self.uses_hid():
            self.load_hid_ports(recheck=True)
            self.hid_timer.start()
        else:
            self.hid_timer.stop()

    def load_hid_ports(self, *, recheck=False):
        if self.running or not self.uses_hid():
            return
        selected = self.hid_port.currentData()
        was_available = selected in self.hid_ports
        previous_issue = self.hid_issue
        labels = {}
        try:
            ports = list_ports()
            devices = {item["port"]: usb_selector(item) for item in ports}
            for item in ports:
                name = item["port"]
                description = (item.get("description") or "").strip().removesuffix(f"({name})").rstrip()
                labels[name] = f"{name} · {description}" if description and description not in {"n/a", name} else name
            self.hid_issue = ""
        except (OSError, RuntimeError) as error:
            devices = {}
            self.hid_issue = str(error)
        saved = project_output(self.project).get("device_selector")
        matches = [port for port, selector in devices.items() if saved and selector == saved]
        new_select = (matches[0] if len(matches) == 1 else None) if saved else selected
        changed = selected != new_select or devices.get(selected) != self.hid_devices.get(selected)
        self.hid_devices = devices
        if not self.hid_port.count() or labels != self.hid_ports or changed:
            self.hid_ports = labels
            with QSignalBlocker(self.hid_port):
                self.hid_port.clear()
                self.hid_port.addItem("포트 선택", None)
                for name, label in labels.items():
                    self.hid_port.addItem(label, name)
                if new_select and new_select not in labels:
                    self.hid_port.addItem(f"{new_select} (미연결)", new_select)
                self.hid_port.setCurrentIndex(max(0, self.hid_port.findData(new_select)))
            recheck |= changed or was_available != (new_select in labels) or not new_select
        if recheck or self.hid_issue != previous_issue:
            self.check_hid_port()

    def set_hid_status(self, text, state="", detail=""):
        self.hid_status.setText(text)
        self.hid_status.setToolTip(detail)
        self.hid_status.setAccessibleDescription(detail)
        self.set_style_state(self.hid_status, "connection", state)

    def check_hid_port(self):
        self.probe_serial += 1
        self.checked_port = None
        self.hid_mode.setEnabled(False)
        self.begin_hid_probe()

    def begin_hid_probe(self):
        if self.running or not self.uses_hid():
            return
        port = self.hid_port.currentData()
        if self.hid_issue:
            self.set_hid_status("COM 포트 조회 실패", "error", self.hid_issue)
        elif not port and project_output(self.project).get("device_selector"):
            self.set_hid_status("저장된 HID 장치를 찾을 수 없습니다.", "error")
        elif not port:
            self.set_hid_status("장치를 연결하고 포트를 선택하세요." if self.hid_ports else "감지된 COM 포트 없음")
        elif port not in self.hid_ports:
            self.set_hid_status(f"{port} 연결 끊김", "error")
        else:
            self.set_hid_status(f"{port} 확인 중…")
            if not self.probing:
                self.probing = True
                Thread(target=self.probe_hid_port, args=(self.probe_serial, port), daemon=True).start()

    def probe_hid_port(self, serial, port):
        try:
            device, detail = probe_port(port), ""
        except (OSError, ValueError, RuntimeError) as error:
            device, detail = None, str(error)
        try:
            self.hid_probe_result.emit(serial, port, device, detail)
        except RuntimeError:
            # Qt may have already destroyed the window on exit.
            pass

    @Slot(int, str, object, str)
    def finish_hid_probe(self, serial, port, device, detail):
        self.probing = False
        if serial != self.probe_serial:
            self.begin_hid_probe()
        elif self.uses_hid() and not self.running:
            if device is None:
                self.set_hid_status(f"{port} 연결 확인 실패", "error", detail)
                self.log(f"{port} · {detail}")
            else:
                self.checked_port = port
                self.hid_mode.model().item(1).setEnabled(device.get("max_onboard_jobs", 0) > 0)
                self.hid_mode.setEnabled(True)
                self.set_hid_status(f"{port} 연결 확인", "ready",
                                    f"{device['board']} · 펌웨어 {device['firmware']} · HID {device['protocol']}")

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
            self.restore_output_profile()
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
            project_output(project)
            if (not isinstance(project.get("globals", {}), dict)
                    or not isinstance(project.get("events", []), list)
                    or not all(isinstance(name, str) and name.strip() for name in project.get("events", []))):
                raise ValueError("변수 또는 이벤트 목록의 형식이 잘못되었습니다.")
            self.project, self.path, self.dirty = project, Path(path), False
            self.restore_output_profile()
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
            project = dict(self.project, name=path.stem)
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
            program, detached = compile_project(self.project)
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
        if self.output.currentData() == OutputKind.FAKE and not self.armed():
            return
        if not self.path and not self.save():
            return
        armed = self.armed()
        if not armed:
            if self.verify() is None:
                return
            if QMessageBox.question(
                self, "실입력 허용",
                f"{self.output.currentText()}에서 선택한 창에 실제 키보드·마우스 입력을 보낼 수 있습니다. ARM을 설정할까요?",
            ) != QMessageBox.StandardButton.Yes:
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
        armed = self.real_output()
        if debug and armed:
            return
        selected = self.targets.currentData()
        target = selected if armed else None
        if armed and not target:
            self.log("실입력에 사용할 대상 창을 선택하세요.")
            self.targets.setFocus()
            return
        hid_port = None
        if armed and self.uses_hid():
            self.load_hid_ports()
            hid_port = self.hid_port.currentData()
            if not hid_port or hid_port != self.checked_port:
                self.log("External HID 장치를 연결하고 COM 포트의 연결 확인을 완료하세요.")
                self.hid_port.setFocus()
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
        config = {"program": program, "target": target, "query_target": selected, "debug": debug,
                  "image_assets": images, "project_directory": str(self.path.resolve().parent if self.path else Path.cwd())}
        if hid_port:
            config["hid_port"] = hid_port
            if self.hid_mode.currentData() == HidMode.ONBOARD:
                config["hid_onboard"] = True
        data = json.dumps(config).encode("ascii")
        if len(data) >= 4 * 1024 * 1024 - 1:
            self.clear_runner_assets()
            self.log("실행 프로그램이 4 MiB 한도를 초과했습니다.")
            return
        self.pending_output = b""
        self.stats.setText("누름 0 · 해제 0\n누락 주기 0 · 오류 0")
        self.machine_label.hide()
        self.session_mode = "live" if armed else "dry"
        self.running_hid_port = hid_port
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
            if hid_port:
                self.check_hid_port()
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
                elif kind == "active_blocks":
                    self.bridge.activeBlocksChanged.emit(message["block_ids"])
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
        self.bridge.activeBlocksChanged.emit([])
        self.clear_runner_assets()
        self.heartbeat.stop()
        self.running = False
        self.running_hid_port = None
        if code or exit_status == QProcess.ExitStatus.CrashExit or self.session_state not in ("STOPPED", "ERROR"):
            if self.session_state == "BREAKPOINT":
                self.bridge.focusBlock.emit("")
            self.session_state = "ERROR"
            self.log("Runner 연결이 끊겼습니다.")
        self.refresh()
        self.load_hid_ports(recheck=True)
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
