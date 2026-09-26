from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from . import protocol


SPLASH_MODE_STATIC = "static"
SPLASH_MODE_TEXT = "text"
SPLASH_MODE_CLOCK = "clock"
SPLASH_MODE_CUSTOM = "custom"

SPLASH_MODES = (
    SPLASH_MODE_STATIC,
    SPLASH_MODE_TEXT,
    SPLASH_MODE_CLOCK,
    SPLASH_MODE_CUSTOM,
)


def _default_splash_path() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / "splash.bin"
    return Path(__file__).resolve().parent.parent / "splash.bin"


def _default_splash_config_path() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / "splash_config.json"
    return Path(__file__).resolve().parent.parent / "splash_config.json"


DEFAULT_SPLASH_PATH = _default_splash_path()
DEFAULT_SPLASH_CONFIG_PATH = _default_splash_config_path()


def _validate_buffer(buffer: bytes) -> bytes:
    if not isinstance(buffer, (bytes, bytearray, memoryview)):
        raise TypeError("splash buffer must be bytes-like")

    data = bytes(buffer)
    if len(data) != protocol.DISPLAY_BUFFER_SIZE:
        raise ValueError(f"splash buffer must be {protocol.DISPLAY_BUFFER_SIZE} bytes")
    return data


@dataclass
class SplashConfig:
    mode: str = SPLASH_MODE_STATIC
    interval: float = 2.0
    idle_timeout: float = 2.0
    template: str = ""
    font_size: int = 12
    alignment: str = "left"
    line_spacing: int = 2

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "interval": self.interval,
            "idle_timeout": self.idle_timeout,
            "template": self.template,
            "font_size": self.font_size,
            "alignment": self.alignment,
            "line_spacing": self.line_spacing,
        }

    @classmethod
    def from_dict(cls, data: dict[str, object] | None) -> "SplashConfig":
        if not isinstance(data, dict):
            return cls()

        mode = str(data.get("mode", SPLASH_MODE_STATIC))
        if mode not in SPLASH_MODES:
            mode = SPLASH_MODE_STATIC

        try:
            interval = max(0.2, float(data.get("interval", 2.0)))
        except (TypeError, ValueError):
            interval = 2.0

        try:
            idle_timeout = max(0.2, float(data.get("idle_timeout", 2.0)))
        except (TypeError, ValueError):
            idle_timeout = 2.0

        try:
            font_size = min(24, max(8, int(data.get("font_size", 12))))
        except (TypeError, ValueError):
            font_size = 12

        alignment = str(data.get("alignment", "left"))
        if alignment not in ("left", "center"):
            alignment = "left"

        try:
            line_spacing = min(8, max(0, int(data.get("line_spacing", 2))))
        except (TypeError, ValueError):
            line_spacing = 2

        return cls(
            mode=mode,
            interval=interval,
            idle_timeout=idle_timeout,
            template=str(data.get("template", "")),
            font_size=font_size,
            alignment=alignment,
            line_spacing=line_spacing,
        )


def save_splash_binary(path: str | Path, buffer: bytes) -> Path:
    data = _validate_buffer(buffer)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = target.with_name(target.name + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(target)
    return target


def load_splash_binary(path: str | Path = DEFAULT_SPLASH_PATH) -> bytes | None:
    target = Path(path)
    try:
        data = target.read_bytes()
    except OSError:
        return None

    if len(data) != protocol.DISPLAY_BUFFER_SIZE:
        return None
    return data


def save_splash_config(config: SplashConfig, path: str | Path = DEFAULT_SPLASH_CONFIG_PATH) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp_path = target.with_name(target.name + ".tmp")
    tmp_path.write_text(json.dumps(config.to_dict(), indent=2), encoding="utf-8")
    tmp_path.replace(target)
    return target


def load_splash_config(path: str | Path = DEFAULT_SPLASH_CONFIG_PATH) -> SplashConfig:
    target = Path(path)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return SplashConfig()

    return SplashConfig.from_dict(data)
