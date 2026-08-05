import base64
import json
from typing import Any


DISPLAY_WIDTH = 128
DISPLAY_HEIGHT = 32
DISPLAY_BUFFER_SIZE = (DISPLAY_WIDTH * DISPLAY_HEIGHT) // 8


def parse_serial_line(line: bytes | str) -> dict[str, Any]:
    if isinstance(line, bytes):
        text = line.decode("utf-8", errors="replace")
    else:
        text = line

    text = text.strip()
    if not text:
        return {"type": "empty"}

    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        return {"type": "log", "message": text}

    if isinstance(message, dict):
        return message

    return {"type": "log", "message": repr(message)}


def encode_command(message: dict[str, Any]) -> bytes:
    return (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")


def build_ping() -> dict[str, Any]:
    return {"type": "ping"}


def build_display_clear(color: int = 0) -> dict[str, Any]:
    return {
        "type": "display",
        "mode": "clear",
        "color": 1 if color else 0,
    }


def build_display_text(lines: list[str], clear: bool = True) -> dict[str, Any]:
    return {
        "type": "display",
        "mode": "text",
        "lines": lines,
        "clear": clear,
    }


def build_display_image(buffer: bytes) -> dict[str, Any]:
    if len(buffer) != DISPLAY_BUFFER_SIZE:
        raise ValueError(f"image buffer must be {DISPLAY_BUFFER_SIZE} bytes")

    return {
        "type": "display",
        "mode": "image",
        "encoding": "base64",
        "data": base64.b64encode(buffer).decode("ascii"),
    }

