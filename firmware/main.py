from time import sleep_ms, ticks_add, ticks_diff, ticks_ms

import config
from display_control import apply_display_command, draw_boot
from inputs import Encoder, MatrixKeyboard
from protocol import SerialProtocol
from ssd1306 import init_display


class HoldModeToggle:
    def __init__(self, key, hold_ms):
        self.key = key
        self.hold_ms = hold_ms
        self.reset()

    def reset(self, ignore_until_release=False):
        self.down_at = None
        self.pending_down = None
        self.long_sent = False
        self.ignore_until_release = ignore_until_release

    def handle_event(self, event, now):
        if event.get("type") != "key" or event.get("key") != self.key:
            return (event,)

        if self.ignore_until_release:
            if event.get("event") == "up":
                self.ignore_until_release = False
            return ()

        if event.get("event") == "down":
            self.down_at = now
            self.pending_down = event
            self.long_sent = False
            return ()

        if event.get("event") == "up":
            if self.long_sent:
                self.reset()
                return ()

            if self.pending_down is not None:
                pending_down = self.pending_down
                self.reset()
                return (pending_down, event)

            self.reset()
            return (event,)

        return (event,)

    def triggered(self, now):
        if self.down_at is None or self.long_sent:
            return False

        if ticks_diff(now, self.down_at) < self.hold_ms:
            return False

        self.long_sent = True
        self.pending_down = None
        return True


def init_ready_display(previous_display=None):
    i2c = previous_display.i2c if previous_display is not None else None
    display, display_error = init_display(i2c)

    if display is None:
        return None, display_error

    try:
        draw_boot(display)
    except OSError as exc:
        return None, str(exc)

    return display, None


def key_is_down(keyboard, key):
    index = key - 1
    return 0 <= index < len(keyboard.stable_state) and keyboard.stable_state[index]


def run_secret_mode(protocol, display, display_error, keyboard):
    if display is None:
        display, display_error = init_ready_display()

    if display is None:
        protocol.send({
            "type": "error",
            "where": "secret",
            "message": display_error or "display is not available",
        })
        return display, display_error

    try:
        display.configure_bus(config.SECRET_I2C_FREQ, config.SECRET_I2C_DATA_CHUNK)
        from secret import main as secret_main
        secret_main.main(
            display=display,
            keyboard=keyboard,
            exit_key=config.SECRET_TOGGLE_KEY,
            action_key=config.SECRET_ACTION_KEY,
            hold_ms=config.SECRET_HOLD_MS,
        )
    except Exception as exc:
        protocol.send({
            "type": "error",
            "where": "secret",
            "message": str(exc),
        })
    finally:
        keyboard.reset()

    try:
        display.configure_bus(config.I2C_FREQ, config.I2C_DATA_CHUNK)
        draw_boot(display)
        return display, None
    except OSError as exc:
        return None, str(exc)


def hello_message(display_ready, display_error):
    return {
        "type": "hello",
        "device": config.DEVICE_NAME,
        "protocol": config.PROTOCOL_VERSION,
        "display": {
            "ready": display_ready,
            "width": config.WIDTH,
            "height": config.HEIGHT,
            "buffer_bytes": config.DISPLAY_BUFFER_SIZE,
            "error": display_error,
        },
        "matrix": {
            "rows": len(config.ROW_PINS),
            "cols": len(config.COL_PINS),
            "row_pins": list(config.ROW_PINS),
            "col_pins": list(config.COL_PINS),
        },
        "encoder": {
            "a_pin": config.ENCODER_A_PIN,
            "b_pin": config.ENCODER_B_PIN,
            "button_pin": config.ENCODER_BUTTON_PIN,
        },
    }


def handle_command(protocol, display, display_error, message):
    message_type = message.get("type")

    if message_type == "ping":
        protocol.send({"type": "pong"})
        return display, display_error

    if message_type != "display":
        protocol.send({
            "type": "error",
            "where": "command",
            "message": "unknown command type: {}".format(message_type),
        })
        return display, display_error

    if display is None:
        display, display_error = init_ready_display()

    if display is None:
        protocol.send({
            "type": "error",
            "where": "display",
            "message": display_error or "display is not available",
        })
        return display, display_error

    try:
        result = apply_display_command(display, message)
        protocol.send({
            "type": "ack",
            "command": "display",
            "result": result,
        })
    except OSError as exc:
        display_error = str(exc)
        display, reinit_error = init_ready_display(display)

        if display is not None:
            try:
                result = apply_display_command(display, message)
                protocol.send({
                    "type": "ack",
                    "command": "display",
                    "result": result,
                })
                return display, None
            except OSError as retry_exc:
                display_error = str(retry_exc)
            except Exception as retry_exc:
                protocol.send({
                    "type": "error",
                    "where": "display",
                    "message": str(retry_exc),
                })
                return display, display_error

        if reinit_error:
            display_error = reinit_error

        protocol.send({
            "type": "error",
            "where": "display",
            "message": display_error,
        })
        return None, display_error
    except Exception as exc:
        protocol.send({
            "type": "error",
            "where": "display",
            "message": str(exc),
        })
        return display, display_error

    return display, display_error


def main():
    protocol = SerialProtocol()
    display, display_error = init_ready_display()
    next_display_reinit = ticks_add(ticks_ms(), config.DISPLAY_REINIT_MS)

    keyboard = MatrixKeyboard()
    encoder = Encoder()
    secret_toggle = HoldModeToggle(config.SECRET_TOGGLE_KEY, config.SECRET_HOLD_MS)

    protocol.send(hello_message(display is not None, display_error))

    while True:
        now = ticks_ms()

        if display is None and ticks_diff(now, next_display_reinit) >= 0:
            display, display_error = init_ready_display()
            next_display_reinit = ticks_add(ticks_ms(), config.DISPLAY_REINIT_MS)

        for message in protocol.read_commands():
            display, display_error = handle_command(
                protocol,
                display,
                display_error,
                message,
            )
            if display is None:
                next_display_reinit = ticks_add(ticks_ms(), config.DISPLAY_REINIT_MS)

        for event in keyboard.poll(now):
            for output_event in secret_toggle.handle_event(event, now):
                protocol.send(output_event)

        if secret_toggle.triggered(now):
            display, display_error = run_secret_mode(
                protocol,
                display,
                display_error,
                keyboard,
            )
            now = ticks_ms()
            secret_toggle.reset(key_is_down(keyboard, config.SECRET_TOGGLE_KEY))
            next_display_reinit = ticks_add(ticks_ms(), config.DISPLAY_REINIT_MS)

        for event in encoder.poll(now):
            protocol.send(event)

        sleep_ms(config.LOOP_MS)


main()
