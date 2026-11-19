"""Python editing controls and project metadata, independent of the Runner."""

import keyword
import re

from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QColor, QFontDatabase, QPainter, QSyntaxHighlighter, QTextCharFormat, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QTextEdit, QVBoxLayout, QWidget

from .python_dsl import MAX_AST_NODES, MAX_SOURCE_CHARS


def validate_editor(project):
    version = project.get("schema_version")
    if type(version) is not int or version not in (1, 2):
        raise ValueError("지원하지 않는 프로젝트 형식입니다.")
    editor = project.get("editor")
    if version == 1:
        if editor is not None or "service_manifests" in project or "service_requirements" in project:
            raise ValueError("Python 편집 자료에는 프로젝트 스키마 2가 필요합니다.")
        return
    from .python_services import ServiceRegistry
    ServiceRegistry({**project, "service_requirements": {}})
    requirements = project.get("service_requirements", {})
    if (not isinstance(requirements, dict) or len(requirements) > 32 or
            any(not isinstance(name, str) or not isinstance(version, str) or not name or not version
                for name, version in requirements.items())):
        raise ValueError("필수 서비스의 ID와 버전 형식이 잘못되었습니다.")
    if not isinstance(editor, dict) or editor.get("active_mode") not in ("blocks", "python"):
        raise ValueError("편집 모드가 잘못되었습니다.")
    python = editor.get("python")
    if not isinstance(python, dict):
        raise ValueError("Python 편집 자료가 없습니다.")
    for key in ("source", "previous_source"):
        source = python.get(key, "")
        if not isinstance(source, str) or len(source) > MAX_SOURCE_CHARS:
            raise ValueError("Python 소스는 64 KiB 문자 한도 안의 텍스트여야 합니다.")
    if "previous_workspace" in editor and not isinstance(editor["previous_workspace"], dict):
        raise ValueError("보존한 블록 편집 자료가 잘못되었습니다.")
    generated = python.get("generated")
    if generated is not None:
        if (not isinstance(generated, dict) or not isinstance(generated.get("source"), str)
                or len(generated["source"]) > MAX_SOURCE_CHARS):
            raise ValueError("생성된 Python 소스 자료가 잘못되었습니다.")
        mapping = generated.get("block_ids_by_line")
        if (not isinstance(mapping, list) or len(mapping) > MAX_AST_NODES or
                any(not isinstance(item, list) or len(item) != 2 or type(item[0]) is not int or
                    not 1 <= item[0] <= MAX_SOURCE_CHARS or not isinstance(item[1], str) or
                    not item[1] or len(item[1]) > MAX_SOURCE_CHARS for item in mapping)):
            raise ValueError("Python 소스맵 자료가 잘못되었습니다.")


class PythonHighlight(QSyntaxHighlighter):
    def highlightBlock(self, text):
        tokens = r"#[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|\b\w+\b"
        for match in re.finditer(tokens, text):
            token = match.group()
            color = None
            if token.startswith("#"):
                color = "#61746a"
            elif token.startswith(("'", '"')):
                color = "#8a4b12"
            elif keyword.iskeyword(token):
                color = "#6d28a8"
            elif token.isdecimal():
                color = "#075f9b"
            if color:
                style = QTextCharFormat()
                style.setForeground(QColor(color))
                # QSyntaxHighlighter offsets are UTF-16 code units.
                start = len(text[:match.start()].encode("utf-16-le")) // 2
                size = len(token.encode("utf-16-le")) // 2
                self.setFormat(start, size, style)


class LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def sizeHint(self):
        return QSize(self.editor.gutter_width(), 0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor("#f1f5f9"))
        painter.setPen(QColor("#617186"))
        block = self.editor.firstVisibleBlock()
        top = int(self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top())
        while block.isValid() and top <= event.rect().bottom():
            height = int(self.editor.blockBoundingRect(block).height())
            if block.isVisible():
                painter.drawText(0, top, self.width() - 8, height,
                                 Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            top += height
            block = block.next()


class CodeEdit(QPlainTextEdit):
    def __init__(self):
        super().__init__()
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        font.setPointSize(11)
        self.setFont(font)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self.setAccessibleName("Python 소스")
        self.gutter = LineNumbers(self)
        self.highlight = PythonHighlight(self.document())
        self.blockCountChanged.connect(self.update_gutter)
        self.updateRequest.connect(lambda *_: self.gutter.update())
        self.update_gutter()

    def gutter_width(self):
        return 16 + self.fontMetrics().horizontalAdvance("9") * len(str(max(1, self.blockCount())))

    def update_gutter(self, *_):
        self.setViewportMargins(self.gutter_width(), 0, 0, 0)
        self.gutter.setGeometry(QRect(self.contentsRect().topLeft(),
                                     QSize(self.gutter_width(), self.contentsRect().height())))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_gutter()

    def show_source(self, span, *, error=False):
        block = self.document().findBlockByNumber(max(0, span.line - 1))
        if not block.isValid():
            return
        text = block.text()
        # AST columns are UTF-8 byte offsets, Qt positions are UTF-16 units.
        prefix = text.encode("utf-8")[:span.column].decode("utf-8", errors="ignore")
        cursor = QTextCursor(block)
        cursor.setPosition(block.position() + len(prefix.encode("utf-16-le")) // 2)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.cursor.clearSelection()
        selection.format.setBackground(QColor("#fee2e2" if error else "#e0efff"))
        selection.format.setProperty(QTextCharFormat.Property.FullWidthSelection, True)
        self.setExtraSelections([selection])


class PythonEditor(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        tools = QHBoxLayout()
        self.import_button = QPushButton("소스 가져오기…")
        self.export_button = QPushButton("소스 내보내기…")
        self.restore_button = QPushButton("이전 Python 초안 복원")
        self.feedback_button = QPushButton("진단 복사")
        self.feedback_button.setEnabled(False)
        for button in (self.import_button, self.export_button, self.restore_button, self.feedback_button):
            tools.addWidget(button)
        tools.addStretch()
        layout.addLayout(tools)
        self.code = CodeEdit()
        self.code.setPlaceholderText("@on_start()\nasync def start():\n    await macro.sleep(ms=1000)")
        self.language = QLabel("Python DSL v1.4")
        layout.addWidget(self.language)
        layout.addWidget(self.code, 1)
        self.status = QLabel("Python 소스를 검증한 뒤 실행하세요.")
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Python 검증 결과")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.status)
        self.cursor_label = QLabel()
        self.code.cursorPositionChanged.connect(self.update_position)
        layout.addWidget(self.cursor_label)
        self.update_position()

    def update_position(self):
        cursor = self.code.textCursor()
        self.cursor_label.setText(f"줄 {cursor.blockNumber() + 1} · 열 {cursor.positionInBlock() + 1}")
