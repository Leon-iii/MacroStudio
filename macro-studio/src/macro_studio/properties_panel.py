import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton, QStackedWidget,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from .ir import PARAM_PREFIX

ITEM_VALUE = Qt.ItemDataRole.UserRole
VALUE_TYPES = ("숫자", "불리언", "문자열")
CALL_TYPES = {"ms_call": "호출", "ms_call_store": "결과 저장", "ms_call_if": "조건 분기"}


def workspace_properties(workspace):
    variables, events, workflows = set(), set(), {}

    def visit(block):
        kind = block.get("type")
        fields = block.get("fields", {})
        name = fields.get("NAME")
        variable = fields.get("RESULT") if kind == "ms_call_store" else name
        if kind in {"ms_variable", "ms_set", "ms_call_store"} and isinstance(variable, str) and variable:
            variables.add(variable)
        if kind in {"ms_custom", "ms_emit_event"} and isinstance(name, str) and name.strip():
            events.add(name.strip())
        if kind == "ms_state":
            name = fields.get("WORKFLOW")
        if isinstance(name, str) and name and (kind == "ms_workflow" or kind in CALL_TYPES or kind == "ms_state"):
            label = CALL_TYPES.get(kind, "정의" if kind == "ms_workflow" else "상태 진입")
            if kind == "ms_state" and isinstance(fields.get("NAME"), str) and fields["NAME"]:
                label += " · " + fields["NAME"]
            if block.get("id"):
                workflows.setdefault(name, []).append((label, block["id"]))
        for connection in (*block.get("inputs", {}).values(), block.get("next", {})):
            for child in connection.values():
                if isinstance(child, dict):
                    visit(child)

    for block in workspace.get("blocks", {}).get("blocks", []):
        visit(block)
    return variables, events, workflows


def number_value(text):
    try:
        return int(text)
    except ValueError:
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("숫자는 유한한 값이어야 합니다.")
        return value


class PropertiesPanel(QWidget):
    addVariable = Signal(str, object)
    removeVariable = Signal(str)
    addEvent = Signal(str)
    removeEvent = Signal(str)
    focusBlock = Signal(str)

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 16, 12, 12)
        layout.setSpacing(8)
        self.initial_values = {}
        self.event_names = ()
        self.workflow_locations = None
        self.variables = QListWidget(objectName="propertyVariables")
        self.events = QListWidget(objectName="propertyEvents")
        self.workflows = QTreeWidget(objectName="propertyWorkflows", headerHidden=True)
        self.workflows.setAccessibleName("워크플로 정의 및 호출 위치")
        self.workflows.itemClicked.connect(self.jump)
        self.workflows.itemActivated.connect(self.jump)

        for title, items, add, edit, remove in (
            ("변수", self.variables, self.new_variable, self.edit_variable, self.removeVariable),
            ("이벤트", self.events, self.new_event, None, self.removeEvent),
        ):
            layout.addWidget(QLabel(title, objectName="section"))
            items.setAccessibleName(title + " 목록")
            items.setFixedHeight(96)
            layout.addWidget(items)
            row = QHBoxLayout()
            add_button = QPushButton("추가")
            add_button.setAccessibleName(title + " 추가")
            add_button.clicked.connect(add)
            row.addWidget(add_button)
            if edit:
                edit_button = QPushButton("초기값")
                edit_button.setAccessibleName("변수 초기값 편집")
                edit_button.setEnabled(False)
                edit_button.clicked.connect(edit)
                items.itemDoubleClicked.connect(edit)
                items.currentItemChanged.connect(lambda item, _old, button=edit_button:
                                                 button.setEnabled(bool(item and item.data(ITEM_VALUE))))
                row.addWidget(edit_button)
            remove_button = QPushButton("삭제")
            remove_button.setAccessibleName(title + " 삭제")
            remove_button.setEnabled(False)
            items.currentItemChanged.connect(lambda item, _old, button=remove_button:
                                             button.setEnabled(bool(item and item.data(ITEM_VALUE))))
            remove_button.clicked.connect(lambda _checked, items=items, remove=remove:
                                          remove.emit(items.currentItem().data(ITEM_VALUE)))
            row.addWidget(remove_button)
            layout.addLayout(row)

        layout.addWidget(QLabel("워크플로", objectName="section"))
        self.workflows.setMinimumHeight(114)
        layout.addWidget(self.workflows, 1)

    def set_options(self, initial_values, events):
        self.initial_values = dict(initial_values)
        self.event_names = tuple(events)
        for widget, rows in (
            (self.variables, [(name, f"{name}  ·  {self.describe(value)}")
                              for name, value in sorted(initial_values.items())]),
            (self.events, [(name, name) for name in sorted(events)]),
        ):
            current = widget.currentItem()
            selected = current.data(ITEM_VALUE) if current else None
            widget.clear()
            if not rows:
                item = QListWidgetItem("등록된 항목 없음", widget)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
            for name, text in rows:
                item = QListWidgetItem(text, widget)
                item.setToolTip(text)
                item.setData(ITEM_VALUE, name)
                if name == selected:
                    widget.setCurrentItem(item)

    @staticmethod
    def describe(value):
        if type(value) is bool:
            return "참" if value else "거짓"
        return repr(value) if type(value) is str else str(value)

    def set_workflows(self, locations):
        if locations == self.workflow_locations:
            return
        self.workflow_locations = locations
        self.workflows.clear()
        if not locations:
            item = QTreeWidgetItem(self.workflows, ["정의된 워크플로 없음"])
            item.setFlags(Qt.ItemFlag.NoItemFlags)
        for name, entries in sorted(locations.items()):
            has_definition = any(label == "정의" for label, _ in entries)
            root = QTreeWidgetItem(self.workflows, [name if has_definition else f"{name} (정의 없음)"])
            counts = {}
            for label, block_id in sorted(entries, key=lambda entry: entry[0] != "정의"):
                counts[label] = counts.get(label, 0) + 1
                text = label if label == "정의" else f"{label} {counts[label]}"
                item = QTreeWidgetItem(root, [text])
                item.setData(0, ITEM_VALUE, block_id)
                item.setToolTip(0, text)
            root.setExpanded(True)

    def jump(self, item, _column):
        if block_id := item.data(0, ITEM_VALUE):
            self.focusBlock.emit(block_id)

    def new_variable(self):
        self.variable_dialog()

    def edit_variable(self):
        item = self.variables.currentItem()
        if item and item.data(ITEM_VALUE):
            self.variable_dialog(item.data(ITEM_VALUE))

    def variable_dialog(self, existing=None):
        dialog = QDialog(self, windowTitle="변수 추가" if existing is None else "변수 초기값")
        form = QFormLayout(dialog)
        name = QLineEdit(existing or "")
        name.setReadOnly(existing is not None)
        form.addRow("이름", name)
        kind = QComboBox()
        kind.addItems(list(VALUE_TYPES))
        form.addRow("유형", kind)
        number, boolean, string = QLineEdit("0"), QComboBox(), QLineEdit()
        boolean.addItems(["거짓", "참"])
        values = QStackedWidget()
        for widget in (number, boolean, string):
            widget.setAccessibleName("초기값")
            values.addWidget(widget)
        kind.currentIndexChanged.connect(values.setCurrentIndex)
        form.addRow("초기값", values)
        if existing is not None:
            value = self.initial_values[existing]
            if type(value) is bool:
                kind.setCurrentText("불리언")
                boolean.setCurrentIndex(int(value))
            elif type(value) is str:
                kind.setCurrentText("문자열")
                string.setText(value)
            else:
                number.setText(str(value))

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)

        def accept():
            variable = existing if existing is not None else name.text().strip()
            if not variable or variable.startswith(PARAM_PREFIX) or (
                    existing is None and variable in self.initial_values):
                QMessageBox.warning(dialog, "변수 이름", "비어 있거나 이미 사용 중인 이름과 예약 접두사는 사용할 수 없습니다.")
                return
            try:
                if kind.currentIndex() == 0:
                    value = number_value(number.text())
                elif kind.currentIndex() == 1:
                    value = boolean.currentIndex() == 1
                else:
                    value = string.text()
            except ValueError:
                QMessageBox.warning(dialog, "초기값", "유한한 숫자를 입력하세요.")
                return
            self.addVariable.emit(variable, value)
            dialog.accept()

        buttons.accepted.connect(accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        dialog.exec()
        dialog.deleteLater()

    def new_event(self):
        name, ok = QInputDialog.getText(self, "이벤트 추가", "이름")
        if not ok:
            return
        name = name.strip()
        if not name or len(name) > 64 or name in self.event_names:
            QMessageBox.warning(self, "이벤트 이름", "1~64자의 사용하지 않은 이름을 입력하세요.")
            return
        self.addEvent.emit(name)
