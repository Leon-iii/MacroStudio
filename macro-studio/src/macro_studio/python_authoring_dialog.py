"""Local review and registration controls for external authoring."""

from copy import deepcopy
import difflib
import json
from pathlib import Path

from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox,
    QPlainTextEdit, QPushButton, QVBoxLayout,
)

from .python_authoring import RESOURCES
from .python_services import MANIFEST_LIMIT, ServiceRegistry, validate_manifest


class ReviewPythonDialog(QDialog):
    def __init__(self, parent, previous, proposed):
        super().__init__(parent)
        self.setWindowTitle("Python 변경 검토")
        self.resize(820, 570)
        layout = QVBoxLayout(self)
        label = QLabel("변경 내용을 확인한 뒤 초안에 적용하세요. 적용 후 검증이 필요합니다.")
        label.setWordWrap(True)
        layout.addWidget(label)
        self.diff = QPlainTextEdit(readOnly=True)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(10)
        self.diff.setFont(font)
        self.diff.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.diff.setAccessibleName("Python 변경 비교")
        self.diff.setPlainText("".join(difflib.unified_diff(previous.splitlines(keepends=True),
                                                          proposed.splitlines(keepends=True),
                                                          fromfile="current.py", tofile="proposed.py")))
        layout.addWidget(self.diff)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Apply).setText("초안에 적용")
        buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)


class ServicePacksDialog(QDialog):
    def __init__(self, parent, project):
        super().__init__(parent)
        self.setWindowTitle("Application Pack · 서비스 Manifest")
        self.resize(650, 420)
        self.project = deepcopy(project)
        self.manifests = deepcopy(project.get("service_manifests", []))
        layout = QVBoxLayout(self)
        explanation = QLabel("등록한 액션은 기존 Runner에서 실행됩니다. 지원 명령은 키 누르기·마우스 클릭·대기입니다.")
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        self.packs = QListWidget()
        self.packs.setAccessibleName("등록된 Application Pack")
        layout.addWidget(self.packs)
        row = QHBoxLayout()
        for text, handler in (("데스크톱 예제 팩 추가", self.add_desktop),
                              ("Manifest 가져오기…", self.import_manifest), ("선택 팩 삭제", self.remove_pack)):
            button = QPushButton(text)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        self.detail = QPlainTextEdit(readOnly=True)
        self.detail.setAccessibleName("팩 액션 목록")
        layout.addWidget(self.detail)
        self.packs.currentRowChanged.connect(self.show_pack)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh()

    def refresh(self):
        self.packs.clear()
        for manifest in self.manifests:
            pack = manifest["application_pack"]
            self.packs.addItem(f"{pack['name']} · {pack['id']} · v{pack['version']}")
        if self.manifests:
            self.packs.setCurrentRow(0)
        else:
            self.detail.setPlainText("등록된 팩이 없습니다. 예제 팩을 추가하거나 Manifest를 가져오세요.")

    def show_pack(self, index):
        if index >= 0:
            actions = self.manifests[index]["services"][0]["actions"]
            self.detail.setPlainText("actions.run v1\n" + "\n".join(f"{item['name']} · {item['id']}" for item in actions))

    def add_manifest(self, raw):
        candidate = [*self.manifests, validate_manifest(raw)]
        registry = ServiceRegistry({**self.project, "service_manifests": candidate, "service_requirements": {}})
        self.manifests = registry.manifests
        self.refresh()

    def add_desktop(self):
        try:
            self.add_manifest(json.loads((RESOURCES / "desktop-actions.json").read_text(encoding="utf-8")))
        except (ValueError, OSError) as error:
            QMessageBox.warning(self, "팩 등록 실패", str(error))

    def import_manifest(self):
        path, _ = QFileDialog.getOpenFileName(self, "서비스 Manifest 가져오기", "", "JSON (*.json)")
        if not path:
            return
        try:
            file = Path(path)
            if file.stat().st_size > MANIFEST_LIMIT:
                raise ValueError("Manifest가 128 KiB를 초과했습니다.")
            self.add_manifest(json.loads(file.read_text(encoding="utf-8-sig")))
        except (ValueError, OSError, RecursionError) as error:
            QMessageBox.warning(self, "팩 등록 실패", str(error))

    def remove_pack(self):
        index = self.packs.currentRow()
        if index >= 0:
            self.manifests.pop(index)
            self.refresh()
