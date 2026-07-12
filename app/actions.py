import platform
import re
import threading
import time
from dataclasses import dataclass


ACTION_DISABLED = "disabled"
ACTION_HOTKEY = "hotkey"
ACTION_TEXT = "text"
ACTION_FUNCTION = "function"
ACTION_MACRO = "macro"
ACTION_DISPLAY_TEXT = "display_text"

ACTION_TYPES = (
    ACTION_DISABLED,
    ACTION_HOTKEY,
    ACTION_TEXT,
    ACTION_FUNCTION,
    ACTION_MACRO,
    ACTION_DISPLAY_TEXT,
)

FUNCTIONS = {
    "volume_up": "volume_up",
    "volume_down": "volume_down",
    "volume_mute": "volume_mute",
    "media_play_pause": "media_play_pause",
    "media_next": "media_next",
    "media_previous": "media_previous",
    "browser_back": "browser_back",
    "browser_forward": "browser_forward",
    "browser_refresh": "browser_refresh",
}

MODIFIER_KEYS = (
    "ctrl",
    "shift",
    "alt",
    "win",
)

LETTER_KEYS = tuple(chr(code) for code in range(ord("a"), ord("z") + 1))
DIGIT_KEYS = tuple(str(number) for number in range(10))
FUNCTION_KEYS = tuple("f{}".format(number) for number in range(1, 25))

NAVIGATION_KEYS = (
    "left",
    "right",
    "up",
    "down",
    "home",
    "end",
    "pageup",
    "pagedown",
)

EDITING_KEYS = (
    "backspace",
    "tab",
    "enter",
    "esc",
    "space",
    "insert",
    "delete",
    "printscreen",
    "pause",
    "capslock",
    "menu",
)

NUMPAD_KEYS = (
    "numpad0",
    "numpad1",
    "numpad2",
    "numpad3",
    "numpad4",
    "numpad5",
    "numpad6",
    "numpad7",
    "numpad8",
    "numpad9",
    "add",
    "subtract",
    "multiply",
    "divide",
    "decimal",
)

MEDIA_KEYS = tuple(FUNCTIONS)

KEY_GROUPS = (
    ("Modifiers", MODIFIER_KEYS),
    ("Letters", LETTER_KEYS),
    ("Digits", DIGIT_KEYS),
    ("Function keys", FUNCTION_KEYS),
    ("Navigation", NAVIGATION_KEYS),
    ("Editing", EDITING_KEYS),
    ("Numpad", NUMPAD_KEYS),
    ("Media / browser", MEDIA_KEYS),
)

KEY_NAMES = tuple(
    key
    for _, group in KEY_GROUPS
    for key in group
)


class ActionError(RuntimeError):
    pass


@dataclass
class Action:
    kind: str = ACTION_DISABLED
    value: str = ""

    def enabled(self) -> bool:
        return self.kind != ACTION_DISABLED and bool(self.value.strip())

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str] | None) -> "Action":
        if not isinstance(data, dict):
            return cls()

        kind = str(data.get("kind", ACTION_DISABLED))
        if kind not in ACTION_TYPES:
            kind = ACTION_DISABLED

        return cls(kind=kind, value=str(data.get("value", "")))


class KeyboardController:
    def __init__(self):
        self.backend = _WinKeyboardBackend() if platform.system() == "Windows" else None

    def supported(self) -> bool:
        return self.backend is not None

    def press_key(self, key: str) -> None:
        self._require_backend()
        self.backend.press_key(key)

    def press_hotkey(self, hotkey: str) -> None:
        self._require_backend()
        keys = parse_hotkey(hotkey)
        if not keys:
            raise ActionError("Hotkey is empty")
        self.backend.press_hotkey(keys)

    def type_text(self, text: str) -> None:
        self._require_backend()
        self.backend.type_text(text)

    def _require_backend(self) -> None:
        if self.backend is None:
            raise ActionError("Keyboard output is only implemented for Windows")


class ActionRunner:
    def __init__(self, on_error=None, on_display=None):
        self.keyboard = KeyboardController()
        self.on_error = on_error
        self.on_display = on_display
        self._lock = threading.Lock()

    def run(self, action: Action) -> None:
        if not action.enabled():
            return

        thread = threading.Thread(target=self._run_locked, args=(action,), daemon=True)
        thread.start()

    def _run_locked(self, action: Action) -> None:
        try:
            with self._lock:
                self._run(action)
        except Exception as exc:
            if self.on_error is not None:
                self.on_error(str(exc))

    def _run(self, action: Action) -> None:
        if action.kind == ACTION_HOTKEY:
            self.keyboard.press_hotkey(action.value)
        elif action.kind == ACTION_TEXT:
            self.keyboard.type_text(action.value)
        elif action.kind == ACTION_FUNCTION:
            self.keyboard.press_key(action.value)
        elif action.kind == ACTION_MACRO:
            self._run_macro(action.value)
        elif action.kind == ACTION_DISPLAY_TEXT:
            self._emit_display(action.value)
        else:
            raise ActionError("Unsupported action kind: {}".format(action.kind))

    def _run_macro(self, script: str) -> None:
        for line_number, raw_line in enumerate(script.splitlines(), start=1):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            command, value = split_macro_line(line)
            if command in ("hotkey", "combo"):
                self.keyboard.press_hotkey(value)
            elif command in ("key", "press", "function"):
                self.keyboard.press_key(value)
            elif command in ("text", "type"):
                self.keyboard.type_text(value)
            elif command in ("display", "oled", "screen"):
                self._emit_display(value)
            elif command in ("clear_display", "clear_oled", "clear_screen"):
                self._emit_display("")
            elif command in ("sleep", "wait", "delay"):
                time.sleep(parse_delay(value) / 1000.0)
            else:
                raise ActionError("Macro line {} has unknown command: {}".format(
                    line_number,
                    command,
                ))

    def _emit_display(self, value: str) -> None:
        if self.on_display is None:
            raise ActionError("Display action is not connected to the application")

        self.on_display(display_lines_from_value(value))


def parse_hotkey(value: str) -> list[str]:
    return [part.strip().lower() for part in re.split(r"[+,\s]+", value) if part.strip()]


def split_macro_line(line: str) -> tuple[str, str]:
    if ":" in line:
        command, value = line.split(":", 1)
    else:
        pieces = line.split(None, 1)
        if len(pieces) == 1:
            command, value = pieces[0], ""
        else:
            command, value = pieces

    return command.strip().lower(), value.strip()


def parse_delay(value: str) -> int:
    value = value.strip().lower()
    if value.endswith("ms"):
        value = value[:-2].strip()
    elif value.endswith("s"):
        seconds = float(value[:-1].strip())
        return max(0, int(seconds * 1000))

    return max(0, int(float(value)))


def display_lines_from_value(value: str) -> list[str]:
    text = value.replace("\\n", "\n")
    if "\n" not in text and "|" in text:
        lines = text.split("|")
    else:
        lines = text.splitlines()

    return [line.strip() for line in lines[:4]]


class _WinKeyboardBackend:
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    KEYEVENTF_EXTENDEDKEY = 0x0001
    INPUT_KEYBOARD = 1

    VK = {
        "backspace": 0x08,
        "tab": 0x09,
        "enter": 0x0D,
        "return": 0x0D,
        "shift": 0x10,
        "ctrl": 0x11,
        "control": 0x11,
        "alt": 0x12,
        "pause": 0x13,
        "capslock": 0x14,
        "esc": 0x1B,
        "escape": 0x1B,
        "space": 0x20,
        "pageup": 0x21,
        "pgup": 0x21,
        "pagedown": 0x22,
        "pgdn": 0x22,
        "end": 0x23,
        "home": 0x24,
        "left": 0x25,
        "up": 0x26,
        "right": 0x27,
        "down": 0x28,
        "printscreen": 0x2C,
        "insert": 0x2D,
        "ins": 0x2D,
        "delete": 0x2E,
        "del": 0x2E,
        "win": 0x5B,
        "windows": 0x5B,
        "cmd": 0x5B,
        "menu": 0x5D,
        "numpad0": 0x60,
        "numpad1": 0x61,
        "numpad2": 0x62,
        "numpad3": 0x63,
        "numpad4": 0x64,
        "numpad5": 0x65,
        "numpad6": 0x66,
        "numpad7": 0x67,
        "numpad8": 0x68,
        "numpad9": 0x69,
        "multiply": 0x6A,
        "add": 0x6B,
        "subtract": 0x6D,
        "decimal": 0x6E,
        "divide": 0x6F,
        "volume_mute": 0xAD,
        "volume_down": 0xAE,
        "volume_up": 0xAF,
        "media_next": 0xB0,
        "media_previous": 0xB1,
        "media_stop": 0xB2,
        "media_play_pause": 0xB3,
        "browser_back": 0xA6,
        "browser_forward": 0xA7,
        "browser_refresh": 0xA8,
    }

    def __init__(self):
        import ctypes
        from ctypes import wintypes

        self.ctypes = ctypes
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)

        ULONG_PTR = ctypes.c_size_t

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = (
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            )

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = (
                ("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR),
            )

        class HARDWAREINPUT(ctypes.Structure):
            _fields_ = (
                ("uMsg", wintypes.DWORD),
                ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD),
            )

        class INPUT_UNION(ctypes.Union):
            _fields_ = (
                ("mi", MOUSEINPUT),
                ("ki", KEYBDINPUT),
                ("hi", HARDWAREINPUT),
            )

        class INPUT(ctypes.Structure):
            _fields_ = (
                ("type", wintypes.DWORD),
                ("union", INPUT_UNION),
            )

        self.INPUT = INPUT
        self.KEYBDINPUT = KEYBDINPUT
        self.input_size = ctypes.sizeof(INPUT)
        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT

    def press_key(self, key: str) -> None:
        vk = self._vk_for_key(key)
        self._key_down(vk)
        self._key_up(vk)

    def press_hotkey(self, keys: list[str]) -> None:
        vk_codes = [self._vk_for_key(key) for key in keys]

        for vk in vk_codes:
            self._key_down(vk)

        for vk in reversed(vk_codes):
            self._key_up(vk)

    def type_text(self, text: str) -> None:
        for char in text:
            self._unicode_char(char)

    def _vk_for_key(self, key: str) -> int:
        normalized = key.strip().lower()
        if not normalized:
            raise ActionError("Key name is empty")

        if normalized in self.VK:
            return self.VK[normalized]

        if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", normalized):
            return 0x70 + int(normalized[1:]) - 1

        if len(normalized) == 1 and "a" <= normalized <= "z":
            return ord(normalized.upper())

        if len(normalized) == 1 and "0" <= normalized <= "9":
            return ord(normalized)

        raise ActionError("Unknown key/function: {}".format(key))

    def _key_down(self, vk: int) -> None:
        self._send(vk, 0, self._extra_flags(vk))

    def _key_up(self, vk: int) -> None:
        self._send(vk, 0, self._extra_flags(vk) | self.KEYEVENTF_KEYUP)

    def _unicode_char(self, char: str) -> None:
        code = ord(char)
        if code > 0xFFFF:
            raise ActionError("Only BMP unicode characters can be typed")

        self._send(0, code, self.KEYEVENTF_UNICODE)
        self._send(0, code, self.KEYEVENTF_UNICODE | self.KEYEVENTF_KEYUP)

    def _send(self, vk: int, scan: int, flags: int) -> None:
        event = self.INPUT()
        event.type = self.INPUT_KEYBOARD
        event.union.ki = self.KEYBDINPUT(vk, scan, flags, 0, 0)
        sent = self.user32.SendInput(1, self.ctypes.byref(event), self.input_size)
        if sent != 1:
            error_code = self.ctypes.get_last_error()
            if error_code:
                error_text = self.ctypes.FormatError(error_code).strip()
                raise ActionError("SendInput failed: {} ({})".format(error_text, error_code))

            raise ActionError(
                "SendInput failed without a Windows error code. "
                "If the target app runs as administrator, run this app as administrator too."
            )

    def _extra_flags(self, vk: int) -> int:
        if vk in (
            self.VK["insert"],
            self.VK["delete"],
            self.VK["home"],
            self.VK["end"],
            self.VK["pageup"],
            self.VK["pagedown"],
            self.VK["left"],
            self.VK["right"],
            self.VK["up"],
            self.VK["down"],
            self.VK["divide"],
        ):
            return self.KEYEVENTF_EXTENDEDKEY

        return 0
