from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import QDialog, QLabel, QPushButton, QVBoxLayout


MODIFIERS = (
    (Qt.KeyboardModifier.ControlModifier, "CTRL"),
    (Qt.KeyboardModifier.AltModifier, "ALT"),
    (Qt.KeyboardModifier.ShiftModifier, "SHIFT"),
    (Qt.KeyboardModifier.MetaModifier, "WIN"),
)
MODIFIER_KEYS = {Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_Shift, Qt.Key.Key_Meta, Qt.Key.Key_AltGr}
NAMED_KEYS = {
    Qt.Key.Key_Return: "ENTER", Qt.Key.Key_Enter: "ENTER", Qt.Key.Key_Space: "SPACE",
    Qt.Key.Key_Tab: "TAB", Qt.Key.Key_Backtab: "TAB", Qt.Key.Key_Escape: "ESC",
    Qt.Key.Key_Left: "LEFT", Qt.Key.Key_Up: "UP", Qt.Key.Key_Right: "RIGHT", Qt.Key.Key_Down: "DOWN",
}


def key_name(key):
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z or Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(key)
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
        return f"F{key - Qt.Key.Key_F1 + 1}"
    return NAMED_KEYS.get(key)


class KeyCaptureDialog(QDialog):
    def __init__(self, parent, mode):
        super().__init__(parent)
        self.mode = mode
        self.value = ""
        self.setWindowTitle("키 듣기")
        self.setMinimumWidth(400)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        layout = QVBoxLayout(self)
        self.prompt = QLabel("키를 눌러 주세요")
        self.prompt.setWordWrap(True)
        layout.addWidget(self.prompt)
        cancel = QPushButton("취소")
        cancel.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        cancel.clicked.connect(self.reject)
        layout.addWidget(cancel, alignment=Qt.AlignmentFlag.AlignRight)
        self.setFocus()

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress:
            self.capture(event)
            event.accept()
            return True
        return super().event(event)

    def capture(self, event):
        key = event.key()
        if event.isAutoRepeat() or key in MODIFIER_KEYS:
            return
        name = key_name(key)
        flags = event.modifiers()
        modifiers = [label for flag, label in MODIFIERS if flags & flag]
        if not name or flags & Qt.KeyboardModifier.GroupSwitchModifier:
            self.prompt.setText("영문자, 숫자, F1–F12, 방향키, Enter, Space, Tab, Esc 중 하나를 눌러 주세요.")
        elif self.mode == "hotkey" and not modifiers:
            self.prompt.setText("Ctrl, Alt, Shift, Win 중 하나 이상을 함께 눌러 주세요.")
        elif self.mode == "single" and modifiers:
            self.prompt.setText("키 누르기와 연타에는 단일 키를 사용하세요.")
        else:
            self.value = "+".join([*modifiers, name])
            self.accept()
