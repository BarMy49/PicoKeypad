import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import protocol
from .actions import (
    ACTION_DISPLAY_TEXT,
    ACTION_FUNCTION,
    ACTION_HOTKEY,
    ACTION_MACRO,
    Action,
    ActionRunner,
    display_lines_from_value,
    split_macro_line,
)
from .bindings import BindingStore, event_id_from_message
from .display_rules import (
    DISPLAY_IMAGE,
    DISPLAY_MEDIA,
    DISPLAY_NONE,
    DISPLAY_TEXT,
    DISPLAY_VOLUME,
    DisplayRule,
    DisplayRuleStore,
)
from .serial_transport import PicoKeypadClient, SerialConnectionError
from .splash_store import DEFAULT_SPLASH_PATH, load_splash_binary, save_splash_binary
from .system_status import get_media_status, get_volume_status, media_display_lines, volume_display_lines


class EngineCallback:
    _names = (
        "on_key_state",
        "on_encoder_state",
        "on_status",
        "on_log",
        "on_display_lines",
        "on_display_buffer",
        "on_connecting",
        "on_device_busy",
    )

    def __init__(self):
        self._callbacks: dict[str, Callable | None] = {name: None for name in self._names}

    def set(self, name: str, callback: Callable | None) -> None:
        if name not in self._callbacks:
            raise ValueError(f"Unknown callback name: {name}")
        self._callbacks[name] = callback

    def fire(self, name: str, *args: Any) -> None:
        cb = self._callbacks.get(name)
        if cb:
            try:
                cb(*args)
            except Exception:
                pass


class PicoKeypadEngine:
    def __init__(
        self,
        initial_port: str | None = None,
        bindings_path: str | Path | None = None,
        display_rules_path: str | Path | None = None,
    ):
        self.client = PicoKeypadClient(port=initial_port)
        self.binding_store = BindingStore(bindings_path)
        self.display_store = DisplayRuleStore(display_rules_path)
        self.action_runner = ActionRunner(
            on_error=lambda text: self._events.fire("on_log", f"ACTION ERROR: {text}"),
            on_display=lambda lines: self._fire_display_from_action(lines),
        )

        self._events = EngineCallback()
        self._stop_reader = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._bg_pump_stop = False

        self._last_rx_time = 0.0
        self._last_ping_time = 0.0
        self._dev_busy = False
        self._ready_status: str | None = None
        self._splash_timer: threading.Timer | None = None
        self._reconnect_timer: threading.Timer | None = None
        self._reconnect_port: str | None = None
        self._auto_reconnect = False

        self._start_background_pump()

    @property
    def events(self) -> EngineCallback:
        return self._events

    def _fire_display_from_action(self, lines: list[str]) -> None:
        rendered = self._render_display_lines(lines)
        if not rendered:
            rendered = [""]
        try:
            if self.client.is_open:
                self.client.send_text(rendered)
        except Exception:
            pass
        self._events.fire("on_display_lines", rendered)
        self._arm_splash_timer()

    def connect(self, port: str | None = None) -> None:
        if self.client.is_open:
            self.disconnect()

        self._stop_reconnect()
        self._reconnect_port = port

        def worker() -> None:
            try:
                self.client.open(port)
            except Exception as exc:
                self._events.fire("on_log", f"Connection failed: {exc}")
                self._start_reconnect()
                return

            self._last_rx_time = time.monotonic()
            self._last_ping_time = 0.0

            self._stop_reader.clear()
            self._reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
            self._reader_thread.start()

            self._events.fire("on_status", f"Connected to {self.client.port}", True)

            self._arm_splash_timer()

            try:
                self.client.ping()
            except Exception as exc:
                self._events.fire("on_log", f"Ping failed: {exc}")

        self._events.fire("on_connecting")
        threading.Thread(target=worker, daemon=True).start()

    def disconnect(self) -> None:
        self._stop_reconnect()
        self._cancel_splash_timer()
        try:
            if self.client.is_open:
                self.client.send_disconnect()
        except Exception:
            pass

        self._stop_reader.set()
        if self._reader_thread and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.5)
        self._reader_thread = None
        try:
            self.client.close()
        except Exception:
            pass
        self._dev_busy = False
        self._events.fire("on_device_busy", False)
        self._events.fire("on_status", "Disconnected", False)
        self._events.fire("on_log", "Disconnected")

    def is_connected(self) -> bool:
        return self.client.is_open

    def connected_port(self) -> str:
        return self.client.port or ""

    def refresh_ports(self) -> list[tuple[str, str]]:
        try:
            ports = PicoKeypadClient.list_ports()
        except SerialConnectionError as exc:
            self._events.fire("on_log", str(exc))
            return []

        result = [(p.device, p.label) for p in ports]
        for _, label in result:
            self._events.fire("on_log", label)
        return result

    @staticmethod
    def list_ports() -> list[tuple[str, str]]:
        try:
            ports = PicoKeypadClient.list_ports()
        except SerialConnectionError:
            return []
        return [(p.device, p.label) for p in ports]

    @staticmethod
    def autodetect_port() -> str | None:
        return PicoKeypadClient.autodetect_port()

    SPLASH_INACTIVITY_S = 2.0

    def _arm_splash_timer(self) -> None:
        self._cancel_splash_timer()
        self._splash_timer = threading.Timer(self.SPLASH_INACTIVITY_S, self._on_splash_inactivity)
        self._splash_timer.daemon = True
        self._splash_timer.start()

    def _cancel_splash_timer(self) -> None:
        if self._splash_timer is not None:
            self._splash_timer.cancel()
            self._splash_timer = None

    def _on_splash_inactivity(self) -> None:
        self._splash_timer = None
        self.send_splash_to_device()

    def _reader_loop(self) -> None:
        while not self._stop_reader.is_set():
            try:
                message = self.client.read_message()
            except Exception as exc:
                self._events.fire("on_log", f"Connection error: {exc}")
                self._trigger_disconnect()
                break
            if message:
                self._last_rx_time = time.monotonic()
                self._handle_message(message)

    def _trigger_disconnect(self) -> None:
        self._stop_reader.set()
        try:
            self.client.close()
        except Exception:
            pass
        self._dev_busy = False
        self._events.fire("on_device_busy", False)
        self._events.fire("on_status", "Disconnected", False)
        self._start_reconnect()

    def set_auto_reconnect(self, enabled: bool) -> None:
        self._auto_reconnect = enabled
        if enabled and not self.client.is_open and self._reconnect_port:
            self._start_reconnect()
        else:
            self._stop_reconnect()

    def _start_reconnect(self) -> None:
        if not self._auto_reconnect:
            return
        if self._reconnect_timer is not None:
            return
        if not self._reconnect_port:
            return
        self._reconnect_timer = threading.Timer(5.0, self._reconnect_tick)
        self._reconnect_timer.daemon = True
        self._reconnect_timer.start()

    def _stop_reconnect(self) -> None:
        if self._reconnect_timer is not None:
            self._reconnect_timer.cancel()
            self._reconnect_timer = None

    def _reconnect_tick(self) -> None:
        self._reconnect_timer = None
        if self.client.is_open:
            return
        if not self._auto_reconnect:
            return
        self._events.fire("on_log", f"Retry connecting to {self._reconnect_port}...")
        self.connect(self._reconnect_port)

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")

        if message_type == "hello":
            display = message.get("display", {})
            ready = "ready" if display.get("ready") else "not ready"
            self._ready_status = f"Connected, display {ready}"
            if not self._dev_busy:
                self._events.fire("on_status", self._ready_status, True)
            self._events.fire("on_log", json.dumps(message))
        elif message_type == "key":
            self._arm_splash_timer()
            self._events.fire("on_key_state", int(message.get("key", 0)),
                              message.get("event") == "down")
            self._run_binding_for_message(message)
            self._run_display_rule_for_message(message)
        elif message_type == "encoder":
            self._arm_splash_timer()
            event = message.get("event")
            if event == "turn":
                delta = int(message.get("delta", 0))
                direction = "cw" if delta > 0 else "ccw"
                self._events.fire("on_encoder_state", direction, None, delta)
            elif event in ("button_down", "button_up"):
                self._events.fire("on_encoder_state", None,
                                  bool(message.get("pressed")))
            self._run_binding_for_message(message)
            self._run_display_rule_for_message(message)
        elif message_type == "ack":
            self._events.fire("on_log", f"ACK {message.get('result')}")
        elif message_type == "error":
            self._events.fire("on_log",
                              f"ERROR {message.get('where')}: {message.get('message')}")
        elif message_type == "pong":
            pass
        elif message_type == "mode":
            state = message.get("state")
            if state == "secret":
                self._dev_busy = True
                self._events.fire("on_device_busy", True)
                self._events.fire("on_status", "Game mode active - keypad offline", False)
                self._events.fire("on_log", "Keypad entered game mode (K1 held)")
            elif state == "normal":
                self._dev_busy = False
                self._events.fire("on_device_busy", False)
                self._events.fire("on_status",
                                  self._ready_status or f"Connected to {self.client.port}",
                                  True)
                self._events.fire("on_log", "Keypad returned to normal mode")
        elif message_type == "empty":
            pass
        else:
            self._events.fire("on_log", message.get("message") or json.dumps(message))

    def _start_background_pump(self) -> None:
        def pump() -> None:
            while not self._bg_pump_stop:
                try:
                    time.sleep(0.05)
                except Exception:
                    pass
                if not self.client.is_open:
                    continue
                try:
                    self.client.ping()
                except Exception:
                    pass
                now = time.monotonic()
                self._dev_busy = now - self._last_rx_time > 2.5

        self._bg_thread = threading.Thread(target=pump, daemon=True)
        self._bg_thread.start()

    def stop(self) -> None:
        self._bg_pump_stop = True
        self._cancel_splash_timer()
        if self.client.is_open:
            self.disconnect()
        self._stop_reader.set()

    def is_device_busy(self) -> bool:
        return self._dev_busy

    def get_binding(self, event_id: str) -> Action:
        return self.binding_store.get(event_id)

    def set_binding(self, event_id: str, action: Action) -> None:
        self.binding_store.set(event_id, action)

    def clear_binding(self, event_id: str) -> None:
        self.binding_store.clear(event_id)

    def get_all_bindings(self) -> dict[str, Action]:
        return dict(self.binding_store.bindings)

    def get_display_rule(self, event_id: str) -> DisplayRule:
        return self.display_store.get(event_id)

    def set_display_rule(self, event_id: str, rule: DisplayRule) -> None:
        self.display_store.set(event_id, rule)

    def clear_display_rule(self, event_id: str) -> None:
        self.display_store.clear(event_id)

    def get_all_display_rules(self) -> dict[str, DisplayRule]:
        return dict(self.display_store.rules)

    def run_action(self, action: Action) -> None:
        if not action.enabled():
            return
        self.action_runner.run(action)

    def send_text_to_display(self, lines: list[str]) -> None:
        lines = self._render_display_lines(lines)
        if not lines:
            lines = [""]
        try:
            self.client.send_text(lines)
            self._events.fire("on_display_lines", lines)
            self._arm_splash_timer()
        except Exception:
            pass

    def send_image_to_display(self, path: str, invert: bool = False) -> None:
        path = path.strip()
        if not path:
            return
        try:
            from PIL import Image as PILImage
            img = PILImage.open(path)
            buffer = self.pil_image_to_oled_buffer(img, invert=invert)
        except Exception:
            return
        try:
            self.client.send_image(buffer)
            self._events.fire("on_display_buffer", buffer)
            self._arm_splash_timer()
        except Exception:
            pass

    def clear_display(self) -> None:
        try:
            self.client.send_clear()
            self._events.fire("on_display_lines", [])
            self._arm_splash_timer()
        except Exception:
            pass

    def send_splash_to_device(self) -> bool:
        buffer = load_splash_binary(DEFAULT_SPLASH_PATH)
        if buffer is None:
            return False
        try:
            self.client.send_image(buffer)
            self._events.fire("on_display_buffer", buffer)
            return True
        except Exception:
            return False

    def load_splash(self) -> bytes | None:
        return load_splash_binary(DEFAULT_SPLASH_PATH)

    def save_splash(self, buffer: bytes) -> None:
        save_splash_binary(DEFAULT_SPLASH_PATH, buffer)

    def splash_path(self) -> Path:
        return DEFAULT_SPLASH_PATH

    def _run_binding_for_message(self, message: dict[str, Any]) -> None:
        event_id = event_id_from_message(message)
        if event_id is None:
            return

        action = self.binding_store.get(event_id)
        if not action.enabled():
            return

        repeats = 1
        if event_id in ("encoder:cw", "encoder:ccw"):
            repeats = min(20, abs(int(message.get("delta", 1))))

        self._apply_volume_indicator(action, repeats)

        action_repeats = 1 if action.kind == ACTION_DISPLAY_TEXT else repeats
        for _ in range(action_repeats):
            self.action_runner.run(action)

    def _run_display_rule_for_message(self, message: dict[str, Any]) -> None:
        event_id = event_id_from_message(message)
        if event_id is None:
            return

        rule = self.display_store.get(event_id)
        if not rule.enabled():
            return

        self._apply_display_rule(rule)

    def _apply_display_rule(self, rule: DisplayRule) -> None:
        if rule.kind == DISPLAY_TEXT:
            self.send_text_to_display(display_lines_from_value(rule.value))
        elif rule.kind == DISPLAY_IMAGE:
            self.send_image_to_display(rule.value)
        elif rule.kind == DISPLAY_VOLUME:
            self._send_system_status_display(DISPLAY_VOLUME)
        elif rule.kind == DISPLAY_MEDIA:
            self._send_system_status_display(DISPLAY_MEDIA)

    def _send_system_status_display(self, kind: str) -> None:
        def worker() -> None:
            if kind == DISPLAY_MEDIA:
                time.sleep(0.3)
            if kind == DISPLAY_VOLUME:
                lines = volume_display_lines(get_volume_status())
            else:
                lines = media_display_lines(get_media_status())
            self._events.fire("on_display_lines", lines)
            try:
                self.client.send_text(lines)
            except Exception:
                pass
            self._arm_splash_timer()

        threading.Thread(target=worker, daemon=True).start()

    def _render_display_lines(self, lines: list[str]) -> list[str]:
        rendered = []
        needs_volume = any("{volume}" in str(line) or "{mute}" in str(line)
                          for line in lines)
        volume = get_volume_status() if needs_volume else None

        for line in lines:
            text = str(line)
            if volume is not None and volume.available:
                text = text.replace("{volume}", str(volume.level))
                text = text.replace("{mute}", "MUTE" if volume.muted else "")
            else:
                text = text.replace("{volume}", "?")
                text = text.replace("{mute}", "")
            rendered.append(text)
        return rendered

    def _apply_volume_indicator(self, action: Action, repeats: int) -> None:
        volume_action = self._volume_action_from_action(action)
        if volume_action is None:
            return
        self._send_system_status_display(DISPLAY_VOLUME)

    @staticmethod
    def _volume_action_from_action(action: Action) -> str | None:
        if action.kind == ACTION_FUNCTION:
            value = action.value.strip().lower()
            if value in ("volume_up", "volume_down", "volume_mute"):
                return value
        if action.kind == ACTION_MACRO:
            for raw_line in action.value.splitlines():
                command, value = split_macro_line(raw_line.strip())
                value = value.strip().lower()
                if command in ("key", "press", "function") and value in (
                    "volume_up", "volume_down", "volume_mute",
                ):
                    return value
        return None

    @staticmethod
    def pil_image_to_oled_buffer(pil_img: Any, invert: bool = False) -> bytes:
        src_width, src_height = pil_img.size
        if src_width <= 0 or src_height <= 0:
            raise ValueError("Image is empty")
        if pil_img.mode not in ("RGB", "RGBA"):
            pil_img = pil_img.convert("RGB")

        buffer = bytearray(protocol.DISPLAY_BUFFER_SIZE)
        for y in range(protocol.DISPLAY_HEIGHT):
            source_y = (y * src_height) // protocol.DISPLAY_HEIGHT
            for x in range(protocol.DISPLAY_WIDTH):
                source_x = (x * src_width) // protocol.DISPLAY_WIDTH
                pixel = pil_img.getpixel((source_x, source_y))
                if isinstance(pixel, int):
                    luminance = pixel
                else:
                    r, g, b = pixel[:3]
                    luminance = (r * 299 + g * 587 + b * 114) // 1000
                lit = luminance >= 128 if invert else luminance < 128
                if lit:
                    buffer[x + (y // 8) * protocol.DISPLAY_WIDTH] |= 1 << (y & 7)
        return bytes(buffer)

    @staticmethod
    def oled_buffer_to_pil_image(buffer: bytes) -> Any:
        from PIL import Image as PILImage
        img = PILImage.new("1", (protocol.DISPLAY_WIDTH, protocol.DISPLAY_HEIGHT), 0)
        for y in range(protocol.DISPLAY_HEIGHT):
            for x in range(protocol.DISPLAY_WIDTH):
                value = buffer[x + (y // 8) * protocol.DISPLAY_WIDTH] & (1 << (y & 7))
                if value:
                    img.putpixel((x, y), 1)
        return img

    @staticmethod
    def lines_to_pil_image(lines: list[str]) -> Any:
        from PIL import Image as PILImage, ImageDraw, ImageFont
        img = PILImage.new("1", (protocol.DISPLAY_WIDTH, protocol.DISPLAY_HEIGHT), 0)
        draw = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("consola.ttf", 8)
        except Exception:
            try:
                font = ImageFont.truetype("cour.ttf", 8)
            except Exception:
                font = ImageFont.load_default()
        for idx, line in enumerate(lines[:4]):
            draw.text((0, idx * 8), line[:16], fill=1, font=font)
        return img
