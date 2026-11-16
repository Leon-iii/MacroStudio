from base64 import b64decode, b64encode
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog,
    QLabel, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QVBoxLayout,
)


MAX_IMAGE_EDGE = 1024
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_ASSETS = 256


def validate_assets(assets):
    if not isinstance(assets, dict) or len(assets) > MAX_ASSETS:
        raise ValueError("이미지 에셋 저장 형식이 올바르지 않습니다.")
    for key, asset in assets.items():
        if (not isinstance(key, str) or not key.startswith("asset:") or not isinstance(asset, dict)
                or not isinstance(asset.get("name"), str) or not asset["name"].strip()
                or not isinstance(asset.get("png"), str)
                or set(asset) - {"name", "png", "source", "capture_metadata", "recognition_profile_id", "sha256"}
                or ("source" in asset and asset["source"] not in ("import", "screen_selection"))
                or ("capture_metadata" in asset and not isinstance(asset["capture_metadata"], dict))
                or ("recognition_profile_id" in asset and not isinstance(asset["recognition_profile_id"], str))
                or ("sha256" in asset and (not isinstance(asset["sha256"], str)
                    or len(asset["sha256"]) != 64 or any(char not in "0123456789abcdef" for char in asset["sha256"])) )):
            raise ValueError("이미지 에셋 저장 형식이 올바르지 않습니다.")
        try:
            data = b64decode(asset["png"], validate=True)
        except ValueError as error:
            raise ValueError(f"이미지 에셋 데이터를 읽을 수 없습니다. {asset['name']}") from error
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"이미지 파일은 {MAX_IMAGE_BYTES // (1024 * 1024)} MiB 이하여야 합니다. {asset['name']}")
        if "sha256" in asset and sha256(data).hexdigest() != asset["sha256"]:
            raise ValueError(f"이미지 파일 해시가 일치하지 않습니다. {asset['name']}")
        raw = QByteArray(data)
        buffer = QBuffer(raw)
        if not buffer.open(QIODevice.OpenModeFlag.ReadOnly):
            raise ValueError(f"이미지 에셋을 열 수 없습니다. {asset['name']}")
        reader = QImageReader(buffer)
        size = reader.size()
        if (bytes(reader.format()).lower() != b"png" or not size.isValid()
                or max(size.width(), size.height()) > MAX_IMAGE_EDGE or reader.read().isNull()):
            raise ValueError(f"이미지 에셋은 읽을 수 있는 PNG이며 각 변이 {MAX_IMAGE_EDGE}픽셀 이하여야 합니다. {asset['name']}")
        buffer.close()


def store_asset(assets, name, png, *, source="import", capture_metadata=None, replace=None):
    """Insert or replace a project PNG and return its stable ID and whether it changed."""
    data = b64decode(png, validate=True)
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError(f"이미지 파일은 {MAX_IMAGE_BYTES // (1024 * 1024)} MiB 이하여야 합니다.")
    digest = sha256(data).hexdigest()
    for asset_id, asset in assets.items():
        if asset_id != replace and sha256(b64decode(asset["png"], validate=True)).hexdigest() == digest:
            if replace is not None:
                raise ValueError(f"‘{asset['name']}’ 템플릿과 같은 이미지입니다.")
            return asset_id, False
    if replace is not None:
        asset = assets[replace]
        asset["png"] = png
        asset["source"] = source
        asset["sha256"] = digest
        if capture_metadata is None:
            asset.pop("capture_metadata", None)
        else:
            asset["capture_metadata"] = capture_metadata
        return replace, True
    used = {asset["name"] for asset in assets.values()}
    base = name.strip() or "가져온 이미지"
    unique = base
    suffix = 2
    while unique in used:
        unique = f"{base} ({suffix})"
        suffix += 1
    if len(assets) >= MAX_ASSETS:
        raise ValueError(f"프로젝트에는 이미지 에셋을 최대 {MAX_ASSETS}개까지 등록할 수 있습니다.")
    asset_id = "asset:" + uuid4().hex
    asset = {"name": unique, "png": png, "source": source, "sha256": digest}
    if capture_metadata is not None:
        asset["capture_metadata"] = capture_metadata
    assets[asset_id] = asset
    return asset_id, True


def asset_references(value, asset_id):
    if isinstance(value, dict):
        if value.get("type") in ("ms_region_image", "ms_vision_find", "ms_vision_wait", "ms_vision_visible",
                                  "ms_on_image_appeared", "ms_on_image_disappeared") and value.get(
                "fields", {}).get("IMAGE") == asset_id:
            return 1
        return sum(asset_references(child, asset_id) for child in value.values())
    if isinstance(value, list):
        return sum(asset_references(child, asset_id) for child in value)
    return 0


def asset_files(assets, ids, directory):
    paths = {}
    for index, asset_id in enumerate(dict.fromkeys(ids)):
        path = Path(directory) / f"image-{index}.png"
        try:
            data = b64decode(assets[asset_id]["png"], validate=True)
        except (KeyError, ValueError) as error:
            raise ValueError("등록된 이미지 데이터를 읽을 수 없습니다.") from error
        path.write_bytes(data)
        paths[asset_id] = str(path)
    return paths


def encode_image(image):
    if image.isNull():
        raise ValueError("이미지를 읽을 수 없습니다.")
    if max(image.width(), image.height()) > MAX_IMAGE_EDGE:
        raise ValueError("기준 이미지는 가로와 세로가 각각 1024픽셀 이하여야 합니다.")
    data = QByteArray()
    buffer = QBuffer(data)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly) or not image.save(buffer, "PNG"):
        raise ValueError("이미지를 PNG로 저장할 수 없습니다.")
    return b64encode(bytes(data)).decode("ascii")


class ImageAssetsDialog(QDialog):
    def __init__(self, parent, assets, references, changed, profiles=None):
        super().__init__(parent)
        self.assets = assets
        self.references = references
        self.changed = changed
        self.profiles = profiles or {"builtin.balanced": {"name": "Balanced"}}
        self.setWindowTitle("이미지 에셋 관리")
        self.resize(780, 530)
        layout = QVBoxLayout(self)
        content = QHBoxLayout()
        layout.addLayout(content, 1)
        self.asset_list = QListWidget()
        self.asset_list.setAccessibleName("이미지 에셋 목록")
        content.addWidget(self.asset_list, 1)
        right = QVBoxLayout()
        content.addLayout(right, 2)
        self.preview = QLabel(alignment=Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumSize(360, 320)
        self.preview.setFrameShape(QLabel.Shape.StyledPanel)
        right.addWidget(self.preview, 1)
        self.details = QLabel()
        right.addWidget(self.details)
        profile_row = QHBoxLayout()
        profile_row.addWidget(QLabel("인식 프로파일"))
        self.profile = QComboBox()
        self.profile.addItem("프로젝트 기본값 사용", "inherit")
        for profile_id, value in self.profiles.items():
            self.profile.addItem(value["name"], profile_id)
        profile_row.addWidget(self.profile, 1)
        self.save_profile = QPushButton("적용")
        self.save_profile.clicked.connect(self.apply_profile)
        profile_row.addWidget(self.save_profile)
        right.addLayout(profile_row)
        add = QHBoxLayout()
        layout.addLayout(add)
        self.button(add, "클립보드에서 붙여넣기", lambda: self.import_image(clipboard=True)).setShortcut("Ctrl+V")
        self.button(add, "이미지 파일 가져오기…", self.import_image)
        add.addStretch()
        edit = QHBoxLayout()
        layout.addLayout(edit)
        self.edit_buttons = [
            self.button(edit, "이름 변경…", self.rename),
            self.button(edit, "파일로 교체…", lambda: self.import_image(replace=True)),
            self.button(edit, "클립보드로 교체", lambda: self.import_image(clipboard=True, replace=True)),
            self.button(edit, "삭제…", self.delete),
        ]
        edit.addStretch()
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)
        self.asset_list.currentItemChanged.connect(self.show_preview)
        self.refresh()

    @staticmethod
    def button(layout, label, action):
        button = QPushButton(label)
        button.clicked.connect(lambda: action())
        layout.addWidget(button)
        return button

    def selected_id(self):
        item = self.asset_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def refresh(self, selected=None):
        self.asset_list.clear()
        for asset_id, asset in sorted(self.assets.items(), key=lambda entry: entry[1]["name"].casefold()):
            item = QListWidgetItem(asset["name"])
            item.setData(Qt.ItemDataRole.UserRole, asset_id)
            self.asset_list.addItem(item)
            if asset_id == selected:
                self.asset_list.setCurrentItem(item)
        if self.selected_id() is None and self.asset_list.count():
            self.asset_list.setCurrentRow(0)
        self.show_preview()

    def show_preview(self, *_):
        asset_id = self.selected_id()
        for button in self.edit_buttons:
            button.setEnabled(asset_id is not None)
        self.details.clear()
        self.profile.setEnabled(asset_id is not None)
        self.save_profile.setEnabled(asset_id is not None)
        if asset_id is None:
            self.preview.setText("등록한 이미지가 없습니다.")
            return
        asset = self.assets[asset_id]
        try:
            pixmap = QPixmap()
            if not pixmap.loadFromData(b64decode(asset["png"], validate=True), "PNG"):
                raise ValueError()
        except ValueError:
            self.preview.setText("미리보기를 불러올 수 없습니다.")
            return
        metadata = asset.get("capture_metadata")
        origin = metadata.get("capture_origin", {}) if isinstance(metadata, dict) else {}
        source = asset.get("source", "import")
        source_text = (f"화면 캡처 · {metadata.get('source_id', '소스')} · "
                       f"({origin.get('x', 0)}, {origin.get('y', 0)})" if source == "screen_selection" else "가져온 이미지")
        self.details.setText(f"{pixmap.width()} × {pixmap.height()} px   ·   연결된 블록 {self.references(asset_id)}개\n{source_text}")
        profile_id = asset.get("recognition_profile_id", "inherit")
        self.profile.setCurrentIndex(max(0, self.profile.findData(profile_id)))
        self.preview.setPixmap(pixmap.scaled(360, 320, Qt.AspectRatioMode.KeepAspectRatio,
                                             Qt.TransformationMode.FastTransformation))

    def import_image(self, *, clipboard=False, replace=False):
        asset_id = self.selected_id() if replace else None
        if replace and asset_id is None:
            return
        try:
            if clipboard:
                name = "붙여넣은 이미지"
                image = QApplication.clipboard().image()
                if image.isNull():
                    raise ValueError("클립보드에 이미지가 없습니다.")
            else:
                path, _ = QFileDialog.getOpenFileName(
                    self, "기준 이미지 선택", "", "이미지 (*.png *.bmp *.jpg *.jpeg)",
                )
                if not path:
                    return
                name = Path(path).stem.strip() or "가져온 이미지"
                reader = QImageReader(path)
                reader.setAutoTransform(True)
                size = reader.size()
                if max(size.width(), size.height()) > MAX_IMAGE_EDGE:
                    raise ValueError("기준 이미지는 가로와 세로가 각각 1024픽셀 이하여야 합니다.")
                image = reader.read()
            png = encode_image(image)
            asset_id, changed = store_asset(self.assets, name, png, replace=asset_id)
        except ValueError as error:
            QMessageBox.warning(self, "이미지 등록 실패", str(error))
            return
        if not changed:
            QMessageBox.information(self, "이미지 등록", f"‘{self.assets[asset_id]['name']}’ 템플릿과 같은 이미지입니다.")
        if changed:
            self.changed()
        self.refresh(asset_id)

    def rename(self):
        asset_id = self.selected_id()
        asset = self.assets[asset_id]
        name, accepted = QInputDialog.getText(self, "이미지 이름 변경", "이름", text=asset["name"])
        name = name.strip()
        if not accepted or name == asset["name"]:
            return
        if not name or any(name == item["name"] for item in self.assets.values()):
            QMessageBox.warning(self, "이름 변경 실패", "비어 있지 않은 고유한 이름을 입력하세요.")
            return
        asset["name"] = name
        self.changed()
        self.refresh(asset_id)

    def apply_profile(self):
        asset_id = self.selected_id()
        if asset_id is None:
            return
        profile_id = self.profile.currentData()
        if profile_id == "inherit":
            self.assets[asset_id].pop("recognition_profile_id", None)
        else:
            self.assets[asset_id]["recognition_profile_id"] = profile_id
        self.changed()
        self.refresh(asset_id)

    def delete(self):
        asset_id = self.selected_id()
        references = self.references(asset_id)
        if references:
            QMessageBox.information(self, "사용 중인 이미지", f"블록 {references}개에서 이 이미지를 사용 중입니다. 해당 블록에서 이미지 선택을 변경하세요.")
            return
        name = self.assets[asset_id]["name"]
        if QMessageBox.question(self, "이미지 삭제", f"‘{name}’ 이미지를 삭제할까요?") == QMessageBox.StandardButton.Yes:
            del self.assets[asset_id]
            self.changed()
            self.refresh()
