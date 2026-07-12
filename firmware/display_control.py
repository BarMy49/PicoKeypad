try:
    import ubinascii as binascii
except ImportError:
    import binascii

import config


def _ascii(value):
    text = str(value)
    result = []
    for char in text:
        code = ord(char)
        result.append(char if 32 <= code < 127 else "?")
    return "".join(result)


def _coerce_lines(message):
    if "lines" in message and isinstance(message["lines"], list):
        raw_lines = message["lines"]
    else:
        raw_lines = str(message.get("text", "")).split("\n")

    lines = []
    for raw_line in raw_lines:
        line = _ascii(raw_line)
        if message.get("wrap", True):
            while len(line) > 16:
                lines.append(line[:16])
                line = line[16:]
        lines.append(line)

    return lines


def _show_changed(display):
    if hasattr(display, "show_changed"):
        display.show_changed()
    else:
        display.show()


def draw_boot(display):
    display.fill(0)
    display.rect(0, 0, config.WIDTH, config.HEIGHT, 1)
    display.text("PICO KEYPAD", 20, 5, 1)
    display.text("USB READY", 28, 18, 1)
    display.show()


def apply_display_command(display, message):
    mode = message.get("mode", "text")

    if mode == "clear":
        color = 1 if message.get("color", 0) else 0
        display.fill(color)
        _show_changed(display)
        return {"mode": mode}

    if mode == "text":
        clear = message.get("clear", True)
        x = int(message.get("x", 0))
        y = int(message.get("y", 0))
        line_height = int(message.get("line_height", 8))
        color = 1 if message.get("color", 1) else 0

        if clear:
            display.fill(0)

        for index, line in enumerate(_coerce_lines(message)):
            line_y = y + index * line_height
            if line_y > config.HEIGHT - 8:
                break
            display.text(line[:16], x, line_y, color)

        _show_changed(display)
        return {"mode": mode}

    if mode == "image":
        data = message.get("data", "")
        if isinstance(data, str):
            data = data.encode()

        raw = binascii.a2b_base64(data)
        if len(raw) != config.DISPLAY_BUFFER_SIZE:
            raise ValueError("image buffer must be {} bytes".format(
                config.DISPLAY_BUFFER_SIZE
            ))

        for index, value in enumerate(raw):
            display.buffer[index] = value

        _show_changed(display)
        return {"mode": mode, "bytes": len(raw)}

    raise ValueError("unsupported display mode: {}".format(mode))
