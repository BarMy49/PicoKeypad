from __future__ import annotations

import sys
from pathlib import Path

from . import protocol


def _default_splash_path() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent / "splash.bin"
    return Path(__file__).resolve().parent.parent / "splash.bin"


DEFAULT_SPLASH_PATH = _default_splash_path()


def _validate_buffer(buffer: bytes) -> bytes:
    if not isinstance(buffer, (bytes, bytearray, memoryview)):
        raise TypeError("splash buffer must be bytes-like")

    data = bytes(buffer)
    if len(data) != protocol.DISPLAY_BUFFER_SIZE:
        raise ValueError(f"splash buffer must be {protocol.DISPLAY_BUFFER_SIZE} bytes")
    return data


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

