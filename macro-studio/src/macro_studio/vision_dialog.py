"""Recognition profile, capture, and preview dialogs for the desktop editor."""

from base64 import b64decode
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import perf_counter_ns
from uuid import uuid4

from PySide6.QtCore import QPoint, QRect, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMessageBox, QPushButton, QSpinBox, QDoubleSpinBox,
    QVBoxLayout, QWidget,
)

from .vision_profiles import BUILTIN_PROFILES, DEFAULT_PROFILE, resolve_profile, validate_profiles


def qimage_from_frame(frame):
    image = frame.image_bgr
    return QImage(image.data, image.shape[1], image.shape[0], image.strides[0],
                  QImage.Format.Format_BGR888).copy()


def image_profile_id(asset):
    return asset.get("recognition_profile_id", "inherit")


class ProfileDialog(QDialog):
    """Manage built-in and project recognition presets."""

    def __init__(self, parent, project, changed, application_default=DEFAULT_PROFILE,
                 application_default_changed=None, preview_requested=None):
        super().__init__(parent)
        self.project = project
        self.changed = changed
        self.application_default = application_default
        self.application_default_changed = application_default_changed or (lambda _profile_id: None)
        self.preview_requested = preview_requested or (lambda _profile_id, _profile: None)
        self.setWindowTitle("인식 프로파일")
        self.resize(760, 500)
        root = QVBoxLayout(self)
        content = QHBoxLayout()
        root.addLayout(content, 1)
        self.items = QListWidget()
        self.items.setAccessibleName("인식 프로파일 목록")
        content.addWidget(self.items, 1)
        form_host = QWidget()
        self.form = QFormLayout(form_host)
        content.addWidget(form_host, 2)
        self.name = QLineEdit()
        self.detector = QComboBox()
        self.detector.addItem("템플릿 매칭", "template")
        self.detector.addItem("HSV 색상", "hsv")
        self.enter = self.decimal(0, 1)
        self.exit = self.decimal(0, 1)
        self.appear = self.integer(1, 300)
        self.disappear = self.integer(1, 300)
        self.cooldown = self.integer(0, 60000)
        self.max_age = self.integer(1, 60000)
        self.interval = self.integer(16, 60000)
        self.roi_left = self.integer(0, 32767)
        self.roi_top = self.integer(0, 32767)
        self.roi_width = self.integer(0, 32768)
        self.roi_height = self.integer(0, 32768)
        for label, widget in (
            ("이름", self.name), ("탐지 방식", self.detector), ("진입 임계값", self.enter),
            ("이탈 임계값", self.exit), ("출현 안정 프레임", self.appear),
            ("소멸 안정 프레임", self.disappear), ("쿨다운 (ms)", self.cooldown),
            ("결과 유효 기간 (ms)", self.max_age), ("스캔 간격 (ms)", self.interval),
        ):
            self.form.addRow(label, widget)
        self.form.addRow(QLabel("탐색 영역 (소스 좌표, 0 너비는 전체)"))
        roi = QHBoxLayout()
        for widget in (self.roi_left, self.roi_top, self.roi_width, self.roi_height):
            roi.addWidget(widget)
        self.form.addRow(roi)
        controls = QHBoxLayout()
        self.clone = QPushButton("선택 항목 복제")
        self.preview_profile = QPushButton("선택 프로파일 미리보기…")
        self.save_profile = QPushButton("프로파일 저장")
        self.delete_profile = QPushButton("사용자 프로파일 삭제")
        controls.addWidget(self.clone)
        controls.addWidget(self.preview_profile)
        controls.addWidget(self.save_profile)
        controls.addWidget(self.delete_profile)
        root.addLayout(controls)
        default_row = QHBoxLayout()
        default_row.addWidget(QLabel("프로젝트 기본 프로파일"))
        self.default = QComboBox()
        default_row.addWidget(self.default, 1)
        root.addLayout(default_row)
        app_default_row = QHBoxLayout()
        app_default_row.addWidget(QLabel("앱 기본 프로파일"))
        self.app_default = QComboBox()
        for profile_id, profile in BUILTIN_PROFILES.items():
            self.app_default.addItem(profile["name"], profile_id)
        self.app_default.setCurrentIndex(max(0, self.app_default.findData(application_default)))
        app_default_row.addWidget(self.app_default, 1)
        root.addLayout(app_default_row)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.accept)
        root.addWidget(buttons)
        self.items.currentItemChanged.connect(self.show_profile)
        self.clone.clicked.connect(self.clone_profile)
        self.preview_profile.clicked.connect(self.preview_selected)
        self.save_profile.clicked.connect(self.save_selected)
        self.delete_profile.clicked.connect(self.delete_selected)
        self.default.currentIndexChanged.connect(self.set_default)
        self.app_default.currentIndexChanged.connect(self.set_application_default)
        self.refresh()

    @staticmethod
    def decimal(minimum, maximum):
        widget = QDoubleSpinBox()
        widget.setRange(minimum, maximum)
        widget.setDecimals(3)
        widget.setSingleStep(0.01)
        return widget

    @staticmethod
    def integer(minimum, maximum):
        widget = QSpinBox()
        widget.setRange(minimum, maximum)
        return widget

    def refresh(self, selected=None):
        current = selected or (self.items.currentItem().data(Qt.ItemDataRole.UserRole)
                               if self.items.currentItem() else DEFAULT_PROFILE)
        self.items.clear()
        profiles = BUILTIN_PROFILES | self.project.setdefault("vision_profiles", {})
        for profile_id, profile in profiles.items():
            item = QListWidgetItem(profile.get("name", profile_id))
            item.setData(Qt.ItemDataRole.UserRole, profile_id)
            self.items.addItem(item)
            if profile_id == current:
                self.items.setCurrentItem(item)
        self.default.blockSignals(True)
        self.default.clear()
        for profile_id, profile in profiles.items():
            self.default.addItem(profile["name"], profile_id)
        default_id = self.project.get("vision_default_profile", "inherit")
        self.default.insertItem(0, "앱 기본값 사용", "inherit")
        self.default.setCurrentIndex(max(0, self.default.findData(default_id)))
        self.default.blockSignals(False)

    def show_profile(self, item, _old=None):
        if not item:
            return
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        profile = {**BUILTIN_PROFILES[DEFAULT_PROFILE], **self.project.get("vision_profiles", {}).get(profile_id,
                                                                                                 BUILTIN_PROFILES.get(profile_id, {}))}
        editable = profile_id not in BUILTIN_PROFILES
        self.name.setText(profile["name"])
        self.name.setReadOnly(not editable)
        self.detector.setCurrentIndex(max(0, self.detector.findData(profile["detector"])))
        for widget, key in ((self.enter, "enter_threshold"), (self.exit, "exit_threshold")):
            widget.setValue(profile[key])
        for widget, key in ((self.appear, "appearance_frames"), (self.disappear, "disappearance_frames"),
                            (self.cooldown, "cooldown_ms"), (self.max_age, "max_age_ms"),
                            (self.interval, "scan_interval_ms")):
            widget.setValue(profile[key])
        roi = profile.get("roi") or {}
        for widget, key in ((self.roi_left, "left"), (self.roi_top, "top"),
                            (self.roi_width, "width"), (self.roi_height, "height")):
            widget.setValue(roi.get(key, 0))
        for widget in (self.detector, self.enter, self.exit, self.appear, self.disappear,
                       self.cooldown, self.max_age, self.interval, self.roi_left,
                       self.roi_top, self.roi_width, self.roi_height):
            widget.setEnabled(editable)
        self.save_profile.setEnabled(editable)
        self.delete_profile.setEnabled(editable)

    def profile_value(self, name):
        roi = None
        if self.roi_width.value() or self.roi_height.value():
            roi = {"kind": "source", "left": self.roi_left.value(), "top": self.roi_top.value(),
                   "width": self.roi_width.value(), "height": self.roi_height.value()}
        profile = {
            **BUILTIN_PROFILES[DEFAULT_PROFILE], "name": name,
            "detector": self.detector.currentData(),
            "enter_threshold": self.enter.value(), "exit_threshold": self.exit.value(),
            "appearance_frames": self.appear.value(), "disappearance_frames": self.disappear.value(),
            "cooldown_ms": self.cooldown.value(), "max_age_ms": self.max_age.value(),
            "scan_interval_ms": self.interval.value(),
        }
        if roi is not None:
            profile["roi"] = roi
        return profile

    def clone_profile(self):
        item = self.items.currentItem()
        if item is None:
            return
        name = self.name.text().strip() + " 복사본"
        if not name:
            return
        profile_id = "user." + uuid4().hex
        self.project.setdefault("vision_profiles", {})[profile_id] = self.profile_value(name)
        self.changed()
        self.refresh(profile_id)

    def save_selected(self):
        item = self.items.currentItem()
        if item is None:
            return
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        profile = self.profile_value(self.name.text().strip())
        if not profile["name"]:
            QMessageBox.warning(self, "프로파일 이름", "프로파일 이름을 입력하세요.")
            return
        records = dict(self.project.get("vision_profiles", {}))
        records[profile_id] = profile
        try:
            validate_profiles(records)
        except ValueError as error:
            QMessageBox.warning(self, "프로파일 설정", str(error))
            return
        self.project["vision_profiles"] = records
        self.changed()
        self.refresh(profile_id)

    def delete_selected(self):
        item = self.items.currentItem()
        if item is None:
            return
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        if profile_id in BUILTIN_PROFILES:
            return
        def uses_profile(value):
            if isinstance(value, dict):
                return (value.get("fields", {}).get("PROFILE") == profile_id
                        or any(uses_profile(child) for child in value.values()))
            if isinstance(value, list):
                return any(uses_profile(child) for child in value)
            return False
        if uses_profile(self.project.get("workspace", {})):
            QMessageBox.information(self, "사용 중인 프로파일", "이 프로파일을 선택한 블록이 있습니다. 먼저 다른 프로파일로 변경하세요.")
            return
        referenced = [asset["name"] for asset in self.project.get("image_assets", {}).values()
                      if asset.get("recognition_profile_id") == profile_id]
        if referenced:
            QMessageBox.information(self, "사용 중인 프로파일", "템플릿 프로파일 재설정을 먼저 하세요: " + ", ".join(referenced))
            return
        del self.project["vision_profiles"][profile_id]
        if self.project.get("vision_default_profile") == profile_id:
            self.project["vision_default_profile"] = "inherit"
        self.changed()
        self.refresh(DEFAULT_PROFILE)

    def set_default(self, _index):
        if not self.default.isEnabled():
            return
        profile_id = self.default.currentData()
        self.project["vision_default_profile"] = profile_id
        self.changed()

    def set_application_default(self, _index):
        profile_id = self.app_default.currentData()
        if profile_id in BUILTIN_PROFILES:
            self.application_default = profile_id
            self.application_default_changed(profile_id)

    def preview_selected(self):
        item = self.items.currentItem()
        if item is None:
            return
        profile_id = item.data(Qt.ItemDataRole.UserRole)
        profile = None
        if profile_id not in BUILTIN_PROFILES:
            profile = self.profile_value(self.name.text().strip())
            records = dict(self.project.get("vision_profiles", {}))
            records[profile_id] = profile
            try:
                validate_profiles(records)
            except ValueError as error:
                QMessageBox.warning(self, "프로파일 미리보기", str(error))
                return
        self.preview_requested(profile_id, profile)


class CaptureWorker(QThread):
    failed = Signal(str)

    def __init__(self, base, source_id, template_path, profile, target_fps):
        super().__init__()
        self.base, self.source_id = base, source_id
        self.template_path, self.profile, self.target_fps = template_path, profile, target_fps
        self.cancelled = Event()
        self.latest = None
        self.candidate = None
        self.candidate_frames = 0
        self.stable_status = None
        self.stable_present = False
        self.event_count = 0

    def run(self):
        backend = None
        try:
            from .perception import CaptureRegion, MSSBackend, recognize
            backend = MSSBackend()
            roi = CaptureRegion(**self.profile["roi"]) if self.profile.get("roi") else None
            backend.start(self.source_id, roi, self.target_fps)
            interval = 1 / min(self.target_fps, 15)
            while not self.cancelled.is_set():
                started = perf_counter_ns()
                frame = backend.read_latest()
                present = getattr(self, "stable_present", False)
                spec = {**self.profile, "detector": "template", "image": Path(self.template_path).name,
                        "template_id": "preview"}
                if present:
                    spec["enter_threshold"] = self.profile["exit_threshold"]
                result = recognize(frame, spec, self.base)
                status = str(result.status)
                if status == "UNKNOWN":
                    self.candidate, self.candidate_frames = None, 0
                elif self.stable_status is None:
                    self.candidate_frames = self.candidate_frames + 1 if status == self.candidate else 1
                    self.candidate = status
                    needed = self.profile["appearance_frames"] if status == "FOUND" else self.profile["disappearance_frames"]
                    if self.candidate_frames >= needed:
                        self.stable_status = status
                        self.stable_present = status == "FOUND"
                        if status == "FOUND" and self.profile.get("fire_on_initial_present", False):
                            self.event_count += 1
                        self.candidate, self.candidate_frames = None, 0
                elif status == self.stable_status:
                    self.candidate, self.candidate_frames = None, 0
                else:
                    self.candidate_frames = self.candidate_frames + 1 if status == self.candidate else 1
                    self.candidate = status
                    needed = self.profile["appearance_frames"] if status == "FOUND" else self.profile["disappearance_frames"]
                    if self.candidate_frames >= needed:
                        self.stable_status = status
                        self.stable_present = status == "FOUND"
                        self.event_count += 1
                        self.candidate, self.candidate_frames = None, 0
                self.latest = (frame, result, started, self.event_count)
                self.cancelled.wait(max(0, interval - (perf_counter_ns() - started) / 1e9))
        except Exception as error:
            self.failed.emit(str(error))
        finally:
            if backend is not None:
                backend.close()

    def stop(self):
        self.cancelled.set()


class PreviewDialog(QDialog):
    """Preview a selected template only after an explicit Start action."""

    def __init__(self, parent, project, base, sources, *, application_profile=DEFAULT_PROFILE,
                 profile_ref=None, profile_override=None):
        super().__init__(parent)
        self.project, self.base, self.sources = project, base, sources
        self.application_profile = application_profile
        self.initial_profile_ref = profile_ref
        self.initial_profile_override = profile_override
        self.worker = None
        self.tempdir = None
        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self.render_latest)
        self.last_frame_id = None
        self.previous_start_ns = None
        self.capture_fps = 0.0
        self.recognition_fps = 0.0
        self.last_state = None
        self.close_after_worker = False
        self.last_error = "없음"
        self.setWindowTitle("실시간 인식 미리보기")
        self.resize(900, 650)
        root = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.source = QComboBox()
        for info in sources:
            self.source.addItem(f"{info.source_id} · {info.region.width} × {info.region.height}", info.source_id)
        self.source.setCurrentIndex(max(0, self.source.findData(project.get("vision_source", "monitor.1"))))
        self.template = QComboBox()
        for asset_id, asset in project.get("image_assets", {}).items():
            self.template.addItem(asset["name"], asset_id)
        self.profile = QComboBox()
        self.profile.addItem("상속", "inherit")
        for profile_id, value in (BUILTIN_PROFILES | project.get("vision_profiles", {})).items():
            self.profile.addItem(value["name"], profile_id)
        if profile_ref is not None:
            self.profile.setCurrentIndex(max(0, self.profile.findData(profile_ref)))
        self.start_button = QPushButton("미리보기 시작")
        self.start_button.clicked.connect(self.start_preview)
        for label, widget in (("화면", self.source), ("템플릿", self.template), ("프로파일", self.profile)):
            controls.addWidget(QLabel(label))
            controls.addWidget(widget, 1)
        controls.addWidget(self.start_button)
        root.addLayout(controls)
        self.view = QLabel("소스와 템플릿을 선택한 뒤 미리보기를 시작하세요.", alignment=Qt.AlignmentFlag.AlignCenter)
        self.view.setMinimumSize(500, 360)
        self.view.setFrameShape(QLabel.Shape.StyledPanel)
        root.addWidget(self.view, 1)
        self.diagnostics = QLabel("캡처가 꺼져 있습니다.")
        self.diagnostics.setTextFormat(Qt.TextFormat.PlainText)
        root.addWidget(self.diagnostics)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def start_preview(self):
        if self.worker:
            self.worker.stop()
            self.start_button.setEnabled(False)
            self.timer.stop()
            self.diagnostics.setText("캡처를 중지하고 있습니다.")
            return
        asset_id = self.template.currentData()
        if not asset_id:
            self.diagnostics.setText("템플릿을 등록하고 선택하세요.")
            return
        try:
            asset = self.project["image_assets"][asset_id]
            self.tempdir = TemporaryDirectory(prefix="macro-studio-preview-")
            folder = self.tempdir.name
            path = Path(folder) / "template.png"
            path.write_bytes(b64decode(asset["png"], validate=True))
            overrides = (self.initial_profile_override
                         if self.profile.currentData() == self.initial_profile_ref else None)
            profile = resolve_profile(
                self.project, self.profile.currentData(), asset_id, overrides,
                application_profile=self.application_profile,
            )
        except (KeyError, OSError, ValueError) as error:
            if self.tempdir:
                self.tempdir.cleanup()
                self.tempdir = None
            self.show_error(f"템플릿 또는 프로파일을 준비하지 못했습니다. {error}")
            return
        target_fps = self.project.get("vision_fps", 15)
        if type(target_fps) is not int or target_fps < 5:
            self.tempdir.cleanup()
            self.tempdir = None
            self.show_error("목표 캡처 빈도는 5 FPS 이상이어야 합니다.")
            return
        target_fps = min(target_fps, 15)
        self.last_error = "없음"
        self.worker = CaptureWorker(folder, self.source.currentData(), str(path), profile, target_fps)
        self.worker.failed.connect(self.show_error)
        self.worker.finished.connect(self.worker_finished)
        self.source.setEnabled(False)
        self.template.setEnabled(False)
        self.profile.setEnabled(False)
        self.start_button.setText("미리보기 중지")
        self.diagnostics.setText("캡처를 시작했습니다.")
        self.worker.start()
        self.timer.start()

    def render_latest(self):
        if not self.worker or self.worker.latest is None:
            return
        frame, result, started, event_count = self.worker.latest
        if frame.frame_id == self.last_frame_id:
            return
        self.last_frame_id = frame.frame_id
        if self.previous_start_ns is not None and started > self.previous_start_ns:
            self.capture_fps = 1e9 / (started - self.previous_start_ns)
            self.recognition_fps = self.capture_fps
        self.previous_start_ns = started
        image = qimage_from_frame(frame)
        pixmap = QPixmap.fromImage(image)
        box = getattr(result, "bbox", None)
        if box:
            painter = QPainter(pixmap)
            painter.setPen(QPen(Qt.GlobalColor.green, 2))
            painter.drawRect(box[0] - frame.origin_x, box[1] - frame.origin_y, box[2], box[3])
            center_x = getattr(result, "center_x", None)
            center_y = getattr(result, "center_y", None)
            if center_x is not None and center_y is not None:
                painter.drawEllipse(QPoint(round(center_x - frame.origin_x),
                                           round(center_y - frame.origin_y)), 4, 4)
            painter.end()
        self.view.setPixmap(pixmap.scaled(self.view.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                          Qt.TransformationMode.FastTransformation))
        status = getattr(result, "status", "UNKNOWN")
        score = getattr(result, "score", None)
        age_ms = max(0, (perf_counter_ns() - frame.captured_at_ns) / 1e6)
        score_text = "—" if score is None else f"{score:.3f}"
        if status == "UNKNOWN":
            self.last_error = getattr(result, "error_message", None) or "인식 결과를 확인할 수 없습니다."
        else:
            self.last_error = "없음"
        capture_ms = max(0, (frame.captured_at_ns - started) / 1e6)
        process_ms = max(0, (result.processed_at_ns - frame.captured_at_ns) / 1e6)
        self.diagnostics.setText(
            f"{status} · 점수 {score_text} · 캡처 {self.capture_fps:.1f} FPS · 인식 {self.recognition_fps:.1f} FPS"
            f" · 캡처 지연 {capture_ms:.1f} ms · 처리 {process_ms:.1f} ms · 프레임 나이 {age_ms:.1f} ms"
            f" · 안정된 이벤트 {event_count} · 마지막 오류 {self.last_error}")

    def show_error(self, message):
        self.last_error = message
        self.diagnostics.setText("UNKNOWN · " + message)

    def worker_finished(self):
        self.timer.stop()
        self.worker = None
        if self.tempdir:
            self.tempdir.cleanup()
            self.tempdir = None
        self.start_button.setText("미리보기 시작")
        self.source.setEnabled(True)
        self.template.setEnabled(True)
        self.profile.setEnabled(True)
        suffix = f" · 마지막 오류 {self.last_error}" if self.last_error != "없음" else ""
        self.diagnostics.setText("캡처가 꺼져 있습니다." + suffix)
        if self.close_after_worker:
            self.close_after_worker = False
            QDialog.reject(self)

    def reject(self):
        if self.worker:
            self.worker.stop()
            self.close_after_worker = True
            self.timer.stop()
            self.start_button.setEnabled(False)
            self.diagnostics.setText("캡처를 중지하고 있습니다.")
            return
        self.timer.stop()
        if self.tempdir:
            self.tempdir.cleanup()
            self.tempdir = None
        super().reject()


class SelectionLabel(QLabel):
    def __init__(self, pixmap, source_image, parent=None):
        super().__init__(parent)
        self.source_image = source_image
        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.start = None
        self.end = None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.start = event.position().toPoint()
            self.end = self.start
            self.update()

    def mouseMoveEvent(self, event):
        if self.start is not None:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if self.start is not None:
            self.end = event.position().toPoint()
            self.update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.start is not None and self.end is not None:
            painter = QPainter(self)
            painter.setPen(QPen(Qt.GlobalColor.red, 2))
            painter.drawRect(QRect(self.start, self.end).normalized())

    def crop(self):
        if self.start is None or self.end is None:
            raise ValueError("저장하기 전에 템플릿 영역을 선택하세요.")
        bounds = QRect(self.start, self.end).normalized().intersected(self.rect())
        if bounds.width() < 8 or bounds.height() < 8:
            raise ValueError("템플릿 영역은 가로와 세로가 8픽셀 이상이어야 합니다.")
        sx = self.source_image.width() / self.width()
        sy = self.source_image.height() / self.height()
        x = round(bounds.left() * sx)
        y = round(bounds.top() * sy)
        right = round((bounds.right() + 1) * sx)
        bottom = round((bounds.bottom() + 1) * sy)
        x, y = max(0, x), max(0, y)
        right, bottom = min(self.source_image.width(), right), min(self.source_image.height(), bottom)
        width, height = right - x, bottom - y
        if width < 1 or height < 1:
            raise ValueError("선택 영역이 이미지 크기를 벗어났습니다.")
        return self.source_image.copy(x, y, width, height), (x, y, width, height)


class CaptureDialog(QDialog):
    """Select a template crop from a frame frozen before the overlay appears."""

    def __init__(self, parent, frame):
        super().__init__(parent)
        self.frame = frame
        self.selection = None
        self.setWindowTitle("화면에서 템플릿 선택")
        root = QVBoxLayout(self)
        self.source_image = qimage_from_frame(frame)
        pixmap = QPixmap.fromImage(self.source_image)
        if pixmap.width() > 1200 or pixmap.height() > 850:
            pixmap = pixmap.scaled(1200, 850, Qt.AspectRatioMode.KeepAspectRatio,
                                   Qt.TransformationMode.FastTransformation)
        self.label = SelectionLabel(pixmap, self.source_image)
        root.addWidget(self.label, 0, Qt.AlignmentFlag.AlignCenter)
        root.addWidget(QLabel("드래그하여 기준 이미지 영역을 선택하세요. 프레임은 캡처 전에 고정했습니다."))
        self.name = QLineEdit("새 템플릿")
        root.addWidget(self.name)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept_selection)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def accept_selection(self):
        try:
            self.selection = self.label.crop()
        except ValueError as error:
            QMessageBox.warning(self, "영역 선택", str(error))
            return
        if not self.name.text().strip():
            QMessageBox.warning(self, "템플릿 이름", "이름을 입력하세요.")
            return
        self.accept()
