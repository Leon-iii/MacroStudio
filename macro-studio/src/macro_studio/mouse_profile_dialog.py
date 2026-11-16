from dataclasses import replace

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMessageBox, QPushButton, QTabWidget, QVBoxLayout, QWidget,
)

from .mouse_motion import BUILTIN_PROFILES
from .mouse_motion_preview import MotionPreviewDialog

ROLE = Qt.ItemDataRole.UserRole


class ProfileEditor(QDialog):
    def __init__(self, parent, profile, title, save):
        super().__init__(parent, windowTitle=title)
        self.profile = profile
        self.save_profile = save
        self.result_profile = None
        self.resize(450, 420)
        layout = QVBoxLayout(self)
        name_form = QFormLayout()
        self.name = QLineEdit(profile.name, objectName="motionProfileName")
        name_form.addRow("이름", self.name)
        layout.addLayout(name_form)
        tabs = QTabWidget(objectName="motionEditorTabs")
        layout.addWidget(tabs, 1)
        self.values = {}
        for title, fields in (
            ("기본", {"duration_ms": "이동 시간 (ms)", "speed_sigma": "속도 변화 강도",
                      "tremor_amplitude_px": "횡방향 흔들림 (px)"}),
            ("고급", {"sampling_hz": "샘플링 (Hz)", "speed_correlation_ms": "속도 상관 시간 (ms)",
                      "tremor_frequency_hz": "흔들림 주파수 (Hz)", "drift_amplitude_px": "드리프트 (px)",
                      "drift_correlation_ms": "드리프트 상관 시간 (ms)"}),
        ):
            page = QWidget()
            form = QFormLayout(page)
            tabs.addTab(page, title)
            for key, label in fields.items():
                field = QLineEdit(str(getattr(profile, key)), objectName=key)
                self.values[key] = field
                form.addRow(label, field)
            if title == "고급":
                self.seed_policy = QComboBox()
                self.seed_policy.addItem("실행마다 새 시드", "random_per_execution")
                self.seed_policy.addItem("고정 시드", "fixed")
                self.seed_policy.setCurrentIndex(self.seed_policy.findData(profile.seed_policy))
                form.addRow("시드 정책", self.seed_policy)
        self.status = QLabel(wordWrap=True, objectName="muted")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("저장")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        buttons.accepted.connect(self.confirm)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.name.selectAll()

    def confirm(self):
        try:
            values = {key: (int(field.text()) if key == "sampling_hz" else float(field.text()))
                      for key, field in self.values.items()}
            edited = replace(self.profile, name=self.name.text().strip(), seed_policy=self.seed_policy.currentData(),
                             **values)
        except ValueError as error:
            self.status.setText(f"입력값을 확인하세요. {error}")
            return
        if edited.warnings and QMessageBox.question(
                self, "샘플링 확인", "흔들림 주파수가 샘플링 주파수의 1/5을 넘습니다. 그대로 저장할까요?"
        ) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.result_profile = self.save_profile(edited)
        except (ValueError, OSError) as error:
            self.status.setText(f"저장 실패 · {error}")
            return
        self.accept()

    def reject(self):
        changed = (self.name.text() != self.profile.name
                   or self.seed_policy.currentData() != self.profile.seed_policy
                   or any(field.text() != str(getattr(self.profile, key)) for key, field in self.values.items()))
        if not changed or QMessageBox.question(
                self, "프로파일 변경 취소", "저장하지 않은 변경을 취소할까요?"
        ) == QMessageBox.StandardButton.Yes:
            super().reject()


class MotionProfilesDialog(QDialog):
    def __init__(self, parent, registry, project_default, block_references=()):
        super().__init__(parent, windowTitle="마우스 모션 프로파일")
        self.registry = registry
        self.project_default = project_default
        self.block_references = block_references
        self.resize(680, 410)
        layout = QVBoxLayout(self)
        content = QHBoxLayout()
        content.setSpacing(16)
        layout.addLayout(content, 1)
        self.list = QListWidget(objectName="motionProfileList")
        self.list.setAccessibleName("모션 프로파일 목록")
        content.addWidget(self.list, 1)
        detail = QVBoxLayout()
        content.addLayout(detail, 2)
        self.title = QLabel(objectName="section")
        self.details = QLabel(wordWrap=True, objectName="muted")
        for label in (self.title, self.details):
            label.setTextFormat(Qt.TextFormat.PlainText)
            detail.addWidget(label)
        detail.addStretch()
        actions = QHBoxLayout()
        layout.addLayout(actions)
        self.add = QPushButton("새 프로파일…", clicked=self.create)
        self.clone = QPushButton("복제…", clicked=self.duplicate)
        self.edit = QPushButton("수정…", clicked=self.edit_selected)
        self.delete_button = QPushButton("삭제…", clicked=self.delete_selected)
        self.preview = QPushButton("궤적 미리보기…", clicked=self.preview_selected)
        for button in (self.add, self.clone, self.edit, self.delete_button, self.preview):
            actions.addWidget(button)
        actions.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.button(QDialogButtonBox.StandardButton.Close).setText("닫기")
        close.rejected.connect(self.reject)
        actions.addWidget(close)
        self.list.currentItemChanged.connect(self.update_detail)
        self.reload()

    def reload(self, selected=None):
        default, users = self.registry.load()
        self.application_default = default
        self.profiles = BUILTIN_PROFILES | users
        self.list.clear()
        for group, profiles in (
            ("내장", BUILTIN_PROFILES.values()), ("사용자", sorted(users.values(), key=lambda p: p.name.casefold())),
        ):
            if not profiles:
                continue
            heading = QListWidgetItem(group, self.list)
            heading.setFlags(Qt.ItemFlag.NoItemFlags)
            for profile in profiles:
                item = QListWidgetItem(profile.name, self.list)
                item.setData(ROLE, profile.id)
                item.setToolTip(profile.name)
                if profile.id == (selected or default):
                    self.list.setCurrentItem(item)
        self.update_detail()

    def selected(self):
        item = self.list.currentItem()
        return self.profiles.get(item.data(ROLE)) if item else None

    def update_detail(self, *_):
        profile = self.selected()
        user = bool(profile and profile.origin == "user")
        self.clone.setEnabled(profile is not None)
        self.preview.setEnabled(profile is not None)
        self.edit.setEnabled(user)
        self.delete_button.setEnabled(user)
        if profile is None:
            self.title.clear()
            self.details.clear()
            return
        self.title.setText(profile.name)
        scope = []
        if profile.id == self.application_default:
            scope.append("앱 기본값")
        if profile.id == self.project_default:
            scope.append("현재 프로젝트 기본값")
        if not user:
            scope.append("내장 프로파일 · 복제 후 편집")
        self.details.setText("\n".join([
            *scope, f"이동 시간  {profile.duration_ms:g} ms", f"샘플링  {profile.sampling_hz} Hz",
            f"속도 변화 강도  {profile.speed_sigma:g}", f"횡방향 흔들림  {profile.tremor_amplitude_px:g} px",
        ]))

    def create(self):
        self.edit_profile(self.registry.draft(), "새 프로파일")

    def duplicate(self):
        if profile := self.selected():
            self.edit_profile(self.registry.draft(profile, name=f"{profile.name} 복사본"), "프로파일 복제")

    def edit_selected(self):
        if profile := self.selected():
            if profile.origin == "user":
                self.edit_profile(profile, "프로파일 수정")

    def preview_selected(self):
        if profile := self.selected():
            dialog = MotionPreviewDialog(self, profile)
            dialog.exec()
            dialog.deleteLater()

    def edit_profile(self, profile, title):
        dialog = ProfileEditor(self, profile, title, self.registry.save)
        if dialog.exec():
            try:
                self.reload(dialog.result_profile.id)
            except (ValueError, OSError) as error:
                QMessageBox.warning(self, "프로파일 목록을 읽을 수 없습니다", str(error))
        dialog.deleteLater()

    def delete_selected(self):
        profile = self.selected()
        if not profile or profile.origin != "user":
            return
        if QMessageBox.question(self, "프로파일 삭제", f"‘{profile.name}’ 프로파일을 삭제할까요?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.registry.delete(profile.id, project_default=self.project_default, block_references=self.block_references)
            self.reload()
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "프로파일 삭제 실패", str(error))
