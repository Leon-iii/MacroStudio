from collections.abc import Iterable
import ctypes as c
from ctypes import wintypes as w
from contextlib import contextmanager
from dataclasses import dataclass
import sys

from .ir import normalize_hotkey

WM_HOTKEY = 0x0312
PM_REMOVE = 0x0001
MOD_NOREPEAT = 0x4000
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
INPUT_KEYBOARD = 1
MOUSE_BUTTON_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010),
                      "middle": (0x0020, 0x0040)}
MAPVK_VSC_TO_VK_EX = 3
MAX_HOTKEY_MESSAGES = 64

_MODIFIERS = {
    "ALT": (0x01, (0x12,)),
    "CTRL": (0x02, (0x11,)),
    "SHIFT": (0x04, (0x10,)),
    "WIN": (0x08, (0x5B, 0x5C)),
}
_VIRTUAL_KEYS = {name: ord(name) for name in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"}
_VIRTUAL_KEYS.update({f"F{i}": 0x6F + i for i in range(1, 13)})
_VIRTUAL_KEYS.update(ENTER=0x0D, SPACE=0x20, TAB=0x09, ESC=0x1B)


class KeyboardInput(c.Structure):
    _fields_ = [
        ("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD),
        ("time", w.DWORD), ("dwExtraInfo", c.c_size_t),
    ]


class MouseInput(c.Structure):
    _fields_ = [
        ("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD),
        ("dwFlags", w.DWORD), ("time", w.DWORD), ("dwExtraInfo", c.c_size_t),
    ]


class HardwareInput(c.Structure):
    _fields_ = [("uMsg", w.DWORD), ("wParamL", w.WORD), ("wParamH", w.WORD)]


class InputData(c.Union):
    _fields_ = [("keyboard", KeyboardInput), ("mouse", MouseInput), ("hardware", HardwareInput)]


class Input(c.Structure):
    _fields_ = [("type", w.DWORD), ("data", InputData)]


@dataclass(frozen=True, slots=True)
class WindowTarget:
    hwnd: int
    thread_id: int
    process_id: int


def _hotkey(hotkey: str) -> tuple[int, int]:
    *modifiers, key = normalize_hotkey(hotkey).split("+")
    return sum(_MODIFIERS[mod][0] for mod in modifiers), _VIRTUAL_KEYS[key]


def _scan_code(key: str) -> int:
    prefix, sep, code = key.partition(":")
    if prefix != "scan" or not sep or len(code) not in (2, 4):
        raise ValueError(f"invalid normalized scan code {key!r}")
    value = int(code, 16)
    if not 1 <= (value & 0xFF) <= 0x7F or value >> 8 not in (0, 0xE0):
        raise ValueError(f"invalid normalized scan code {key!r}")
    return value


class WindowsDesktop:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise OSError("Windows desktop input requires Windows")
        layout = (c.sizeof(Input), Input.data.offset, c.sizeof(KeyboardInput))
        expected = (40, 8, 24) if c.sizeof(c.c_void_p) == 8 else (28, 4, 16)
        if layout != expected:
            raise OSError(f"unexpected Win32 INPUT ABI: {layout}")
        user32 = c.WinDLL("user32", use_last_error=True)
        self._enum_proc = c.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
        self._enum = self._bind(user32.EnumWindows, w.BOOL, self._enum_proc, w.LPARAM)
        self._visible = self._bind(user32.IsWindowVisible, w.BOOL, w.HWND)
        self._foreground = self._bind(user32.GetForegroundWindow, w.HWND)
        self._iconic = self._bind(user32.IsIconic, w.BOOL, w.HWND)
        self._cursor = self._bind(user32.GetCursorPos, w.BOOL, c.POINTER(w.POINT))
        self._set_cursor = self._bind(user32.SetCursorPos, w.BOOL, c.c_int, c.c_int)
        self._window_at = self._bind(user32.WindowFromPoint, w.HWND, w.POINT)
        self._ancestor = self._bind(user32.GetAncestor, w.HWND, w.HWND, w.UINT)
        self._window_thread = self._bind(
            user32.GetWindowThreadProcessId, w.DWORD, w.HWND, c.POINTER(w.DWORD),
        )
        self._window_title = self._bind(
            user32.GetWindowTextW, c.c_int, w.HWND, w.LPWSTR, c.c_int,
        )
        self._register = self._bind(
            user32.RegisterHotKey, w.BOOL, w.HWND, c.c_int, w.UINT, w.UINT,
        )
        self._unregister = self._bind(user32.UnregisterHotKey, w.BOOL, w.HWND, c.c_int)
        self._peek = self._bind(
            user32.PeekMessageW, w.BOOL, c.POINTER(w.MSG), w.HWND, w.UINT, w.UINT, w.UINT,
        )
        self._send = self._bind(user32.SendInput, w.UINT, w.UINT, c.POINTER(Input), c.c_int)
        self._get_key_state = self._bind(user32.GetAsyncKeyState, w.SHORT, c.c_int)
        self._layout = self._bind(user32.GetKeyboardLayout, w.HKL, w.DWORD)
        self._map_key = self._bind(
            user32.MapVirtualKeyExW, w.UINT, w.UINT, w.UINT, w.HKL,
        )

    @staticmethod
    def _bind(function, return_type, *argument_types):
        function.argtypes = argument_types
        function.restype = return_type
        return function

    def windows(self) -> list[dict]:
        windows = []
        title = c.create_unicode_buffer(1024)

        @self._enum_proc
        def collect(hwnd, _):
            if self._visible(hwnd) and self._window_title(hwnd, title, len(title)):
                windows.append({"hwnd": hwnd, "title": title.value})
            return True

        if not self._enum(collect, 0):
            raise c.WinError(c.get_last_error())
        return windows

    def target(self, hwnd: int) -> WindowTarget:
        process_id = w.DWORD()
        if type(hwnd) is not int or hwnd <= 0 or hwnd > c.c_size_t(-1).value:
            raise ValueError(f"invalid window handle: {hwnd!r}")
        thread_id = self._window_thread(hwnd, c.byref(process_id))
        if not thread_id:
            raise ValueError(f"window handle does not exist: {hwnd:#x}")
        return WindowTarget(hwnd, thread_id, process_id.value)

    def alive(self, target: WindowTarget) -> bool:
        try:
            return self.target(target.hwnd) == target
        except ValueError:
            return False

    def focused(self, target: WindowTarget) -> bool:
        return self._foreground() == target.hwnd and self.alive(target)

    def window_state(self, target: WindowTarget, state: str) -> bool:
        if not self.alive(target):
            return False
        if state == "foreground":
            return self._foreground() == target.hwnd
        if state == "minimized":
            return bool(self._iconic(target.hwnd))
        return state == "exists"

    def cursor_over(self, target: WindowTarget) -> bool:
        try:
            return self.point_over(target, self.mouse_position())
        except OSError:
            return False

    def mouse_position(self) -> tuple[int, int]:
        point = w.POINT()
        if not self._cursor(c.byref(point)):
            raise c.WinError(c.get_last_error())
        return point.x, point.y

    def point_over(self, target: WindowTarget, position: tuple[int, int]) -> bool:
        hwnd = self._window_at(w.POINT(*position))
        return bool(hwnd and (hwnd == target.hwnd or self._ancestor(hwnd, 2) == target.hwnd))

    def send_mouse_move(self, position: tuple[int, int]) -> None:
        if not self._set_cursor(*position):
            raise c.WinError(c.get_last_error())

    @contextmanager
    def hotkeys(self, mapping: dict[int, str]):
        registered = []
        failed = []
        try:
            for identifier, key in mapping.items():
                modifiers, virtual_key = _hotkey(key)
                if not 0 <= identifier <= 0xBFFF:
                    raise ValueError("hotkey IDs must be between 0 and 0xBFFF")
                if virtual_key != _VIRTUAL_KEYS["F12"] and self._register(
                    None, identifier, modifiers | MOD_NOREPEAT, virtual_key,
                ):
                    registered.append(identifier)
                else:
                    failed.append(identifier)
            yield failed
        finally:
            for identifier in registered:
                self._unregister(None, identifier)
            while len(self.poll_hotkeys()) == MAX_HOTKEY_MESSAGES:
                pass

    def poll_hotkeys(self) -> list[int]:
        message = w.MSG()
        identifiers = []
        for _ in range(MAX_HOTKEY_MESSAGES):
            if not self._peek(c.byref(message), None, WM_HOTKEY, WM_HOTKEY, PM_REMOVE):
                break
            identifiers.append(message.wParam)
        return identifiers

    def _virtual_key(self, key: str) -> int:
        hwnd = self._foreground()
        thread_id = self._window_thread(hwnd, None) if hwnd else 0
        return self._map_key(_scan_code(key), MAPVK_VSC_TO_VK_EX, self._layout(thread_id))

    def hotkey_conflict(self, key: str, hotkeys: Iterable[str]) -> bool:
        virtual_key = self._virtual_key(key)
        matching_flags = [flags for flags, trigger in map(_hotkey, hotkeys) if trigger == virtual_key]
        if not matching_flags:
            return False
        modifiers = sum(
            flag for flag, keys in _MODIFIERS.values()
            if any(self._get_key_state(vk) & 0x8000 for vk in keys)
        )
        return any(flags & modifiers == flags for flags in matching_flags)

    def unmodified_hotkey_conflict(self, key: str, hotkeys: Iterable[str]) -> bool:
        virtual_key = self._virtual_key(key)
        return any(_hotkey(combo) == (0, virtual_key) for combo in hotkeys)

    def send_key_down(self, key: str) -> None:
        self._send_key(key, release=False)

    def send_key_up(self, key: str) -> None:
        self._send_key(key, release=True)

    def send_mouse_down(self, button: str) -> None:
        self._send_mouse(button, 0)

    def send_mouse_up(self, button: str) -> None:
        self._send_mouse(button, 1)

    def _send_mouse(self, button: str, edge: int) -> None:
        flags = MOUSE_BUTTON_FLAGS[button][edge]
        self._insert(Input(0, InputData(mouse=MouseInput(0, 0, 0, flags, 0, 0))))

    def _send_key(self, key: str, *, release: bool) -> None:
        scan = _scan_code(key)
        flags = KEYEVENTF_SCANCODE
        if scan >> 8:
            flags |= KEYEVENTF_EXTENDEDKEY
        if release:
            flags |= KEYEVENTF_KEYUP
        packet = Input(INPUT_KEYBOARD, InputData(keyboard=KeyboardInput(0, scan & 0xFF, flags, 0, 0)))
        self._insert(packet)

    def _insert(self, packet: Input) -> None:
        c.set_last_error(0)
        if self._send(1, c.byref(packet), c.sizeof(packet)) != 1:
            error = c.get_last_error()
            raise OSError(error, "SendInput inserted no events")
