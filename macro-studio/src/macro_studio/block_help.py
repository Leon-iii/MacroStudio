from html import escape
import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QTextBrowser, QVBoxLayout


class BlockHelp(QDialog):
    def __init__(self, parent, catalog_path):
        super().__init__(parent)
        self.setWindowTitle("블록 도움말")
        self.resize(530, 550)
        self.setMinimumSize(360, 280)
        self.setSizeGripEnabled(True)
        self.catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        self.history = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 12)
        self.document = QTextBrowser()
        self.document.setAccessibleName("블록 도움말 문서")
        self.document.setOpenLinks(False)
        self.document.setOpenExternalLinks(False)
        self.document.setStyleSheet("""
            QTextBrowser { background: #fff; color: #19283b; border: 1px solid #c7d2df; }
            QTextBrowser:focus { border-color: #2455cb; }
        """)
        self.document.document().setDocumentMargin(20)
        self.document.anchorClicked.connect(lambda url: self.follow(url.toString()))
        layout.addWidget(self.document)

        controls = QHBoxLayout()
        self.back = QPushButton("뒤로")
        self.back.clicked.connect(self.go_back)
        controls.addWidget(self.back)
        controls.addStretch()
        close = QPushButton("닫기")
        close.clicked.connect(self.close)
        controls.addWidget(close)
        layout.addLayout(controls)

    def show_block(self, block_type):
        if block_type not in self.catalog:
            return
        self.history = [block_type]
        self.render_topic()
        if not self.isVisible():
            self.show()
        self.raise_()
        self.activateWindow()
        self.document.setFocus(Qt.FocusReason.OtherFocusReason)

    def follow(self, block_type):
        if block_type in self.catalog and block_type != self.history[-1]:
            self.history.append(block_type)
            self.render_topic()

    def go_back(self):
        if len(self.history) > 1:
            self.history.pop()
            self.render_topic()

    def render_topic(self):
        topic = self.catalog[self.history[-1]]
        parts = [f"<h1>{escape(topic['title'])}</h1>", f"<p>{escape(topic['summary'])}</p>"]
        if details := topic.get("details"):
            items = "".join(f"<li>{escape(detail)}</li>" for detail in details)
            parts.append(f"<h2>설정과 연결</h2><ul>{items}</ul>")
        if example := topic.get("example"):
            parts.append(f"<h2>연결 예시</h2><p>{escape(example)}</p>")
        if related := topic.get("related"):
            links = "".join(f'<li><a href="{escape(key)}">{escape(self.catalog[key]["title"])}</a></li>'
                            for key in related)
            parts.append(f"<h2>관련 블록</h2><ul>{links}</ul>")
        self.document.setHtml("""
            <style>
              h1 { font-size: 22px; margin-bottom: 12px; }
              h2 { font-size: 15px; margin-top: 18px; margin-bottom: 5px; }
              p, li { line-height: 145%; }
              a { color: #234fbf; }
            </style>
        """ + "".join(parts))
        self.document.verticalScrollBar().setValue(0)
        self.back.setEnabled(len(self.history) > 1)
