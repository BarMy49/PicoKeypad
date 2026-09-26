import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import protocol
from .actions import (
    ACTION_DISABLED,
    ACTION_MACRO,
    ACTION_MEDIA,
    ACTION_TOGGLE,
    ACTION_VOLUME,
    Action,
    ActionRunner,
    display_lines_from_value,
    split_macro_line,
    DISPLAY_IMAGE,
    DISPLAY_MEDIA,
    DISPLAY_NONE,
    DISPLAY_TEXT,
    DISPLAY_VOLUME,
)
from .actions_store import ActionStore, event_id_from_message
from .serial_transport import PicoKeypadClient, SerialConnectionError
from .splash_store import (
    DEFAULT_SPLASH_CONFIG_PATH,
    DEFAULT_SPLASH_PATH,
    SPLASH_MODE_CLOCK,
    SPLASH_MODE_CUSTOM,
    SPLASH_MODE_STATIC,
    SPLASH_MODE_TEXT,
    SplashConfig,
    load_splash_binary,
    load_splash_config,
    save_splash_binary,
    save_splash_config,
)
from .splash_renderer import (
    clock_to_lines,
    clock_to_oled_buffer,
    get_time_info,
    render_template,
    text_buffer_from_lines,
)
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
        "on_toggle_state",
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
        actions_path: str | Path | None = None,
        splash_config_path: str | Path | None = None,
    ):
        self.client = PicoKeypadClient(port=initial_port)
        self.action_store = ActionStore(actions_path)
        self.action_runner = ActionRunner(
            on_error=lambda text: self._events.fire("on_log", f"ACTION ERROR: {text}"),
            on_display=lambda lines: self._fire_display_from_action(lines),
        )

        self._splash_config_path = Path(splash_config_path) if splash_config_path is not None else DEFAULT_SPLASH_CONFIG_PATH
        self.splash_config = load_splash_config(self._splash_config_path)

        self._events = EngineCallback()
        self._stop_reader = threading.Event()
        self._reader_thread: threading.Thread | None = None
        self._bg_pump_stop = False

        self._last_rx_time = 0.0
        self._last_ping_time = 0.0
        self._dev_busy = False
        self._ready_status: str | None = None
        self._splash_timer: threading.Timer | None = None
        self._splash_loop_timer: threading.Timer | None = None
        self._in_splash_mode = False
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
        self._cancel_splash_loop()
        self._in_splash_mode = False
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
        self._in_splash_mode = True
        self.send_splash_to_device()
        self._arm_splash_loop_timer()

    def _arm_splash_loop_timer(self) -> None:
        self._cancel_splash_loop()
        if self.splash_config.mode == SPLASH_MODE_STATIC:
            return
        if self.splash_config.interval <= 0:
            return
        self._splash_loop_timer = threading.Timer(self.splash_config.interval, self._splash_loop_tick)
        self._splash_loop_timer.daemon = True
        self._splash_loop_timer.start()

    def _cancel_splash_loop(self) -> None:
        if self._splash_loop_timer is not None:
            self._splash_loop_timer.cancel()
            self._splash_loop_timer = None

    def _splash_loop_tick(self) -> None:
        if not self._in_splash_mode:
            return
        self._splash_loop_timer = None
        self.send_splash_to_device()
        self._arm_splash_loop_timer()

    def _exit_splash_mode(self) -> None:
        self._in_splash_mode = False
        self._cancel_splash_loop()

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
            self._exit_splash_mode()
            self._events.fire("on_key_state", int(message.get("key", 0)),
                              message.get("event") == "down")
            self._run_action_for_message(message)
        elif message_type == "encoder":
            self._arm_splash_timer()
            self._exit_splash_mode()
            event = message.get("event")
            if event == "turn":
                delta = int(message.get("delta", 0))
                direction = "cw" if delta > 0 else "ccw"
                self._events.fire("on_encoder_state", direction, None, delta)
            elif event in ("button_down", "button_up"):
                self._events.fire("on_encoder_state", None,
                                  bool(message.get("pressed")))
            self._run_action_for_message(message)
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

    def _run_action_for_message(self, message: dict[str, Any]) -> None:
        event_id = event_id_from_message(message)
        if event_id is None:
            return

        action = self.action_store.get(event_id)
        if not action.enabled():
            return

        if action.kind == ACTION_TOGGLE:
            self._run_toggle_action(event_id, action)
        else:
            self._run_regular_action(action)

    def _run_regular_action(self, action: Action) -> None:
        if action.kind != ACTION_DISABLED and bool(action.value.strip()):
            threading.Thread(target=self.action_runner.run, args=(action,), daemon=True).start()

        display = action.display
        if display == DISPLAY_NONE:
            display = self.default_display_for_kind(action.kind)
        if display != DISPLAY_NONE:
            self.apply_display_response(display, action.display_value)

    def _run_toggle_action(self, event_id: str, action: Action) -> None:
        new_state = self.action_store.toggle_state(event_id)
        action.state = new_state
        self._events.fire("on_toggle_state", event_id, new_state)

        branch = action.action_on if new_state else action.action_off
        if branch.strip():
            self._run_string_action(branch)

        image = action.image_on if new_state else action.image_off
        if image.strip():
            self.send_image_to_display(image)

    def _run_string_action(self, action_str: str) -> None:
        command, value = split_macro_line(action_str)

        def worker() -> None:
            try:
                if command in ("hotkey", "combo"):
                    self.action_runner.keyboard.press_hotkey(value)
                elif command in ("key", "press", "function", "volume", "media"):
                    self.action_runner.keyboard.press_key(value)
                elif command in ("text", "type"):
                    self.action_runner.keyboard.type_text(value)
                elif command == "macro":
                    self.action_runner.run(Action(kind=ACTION_MACRO, value=value))
                else:
                    # Bare value like "f13" or "ctrl+shift+a" → treat as a hotkey combo
                    self.action_runner.keyboard.press_hotkey(action_str.strip())
            except Exception as exc:
                self._events.fire("on_log", f"ACTION ERROR: {exc}")

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def default_display_for_kind(kind: str) -> str:
        if kind == ACTION_VOLUME:
            return DISPLAY_VOLUME
        if kind == ACTION_MEDIA:
            return DISPLAY_MEDIA
        return DISPLAY_NONE

    def apply_display_response(self, display: str, value: str) -> None:
        if display == DISPLAY_TEXT:
            self.send_text_to_display(display_lines_from_value(value))
        elif display == DISPLAY_IMAGE:
            self.send_image_to_display(value)
        elif display == DISPLAY_VOLUME:
            self._send_system_status_display(DISPLAY_VOLUME)
        elif display == DISPLAY_MEDIA:
            self._send_system_status_display(DISPLAY_MEDIA)

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
        self._cancel_splash_loop()
        if self.client.is_open:
            self.disconnect()
        self._stop_reader.set()

    def is_device_busy(self) -> bool:
        return self._dev_busy

    # ---- action store access ----

    def get_action(self, event_id: str) -> Action:
        return self.action_store.get(event_id)

    def set_action(self, event_id: str, action: Action) -> None:
        self.action_store.set(event_id, action)

    def clear_action(self, event_id: str) -> None:
        self.action_store.clear(event_id)

    def get_all_actions(self) -> dict[str, Action]:
        return dict(self.action_store.actions)

    def toggle_binding(self, event_id: str) -> bool:
        action = self.action_store.get(event_id)
        if action.kind != ACTION_TOGGLE:
            return False
        new_state = self.action_store.toggle_state(event_id)
        self._events.fire("on_toggle_state", event_id, new_state)
        return new_state

    def get_toggle_state(self, event_id: str) -> bool:
        return bool(self.action_store.get(event_id).state)

    # ---- splash config ----

    def get_splash_config(self) -> SplashConfig:
        return self.splash_config

    def set_splash_config(self, config: SplashConfig, persist: bool = True) -> None:
        self.splash_config = config
        self.SPLASH_INACTIVITY_S = config.idle_timeout
        if persist:
            save_splash_config(config, self._splash_config_path)

    def splash_config_path(self) -> Path:
        return self._splash_config_path

    # ---- actions execution ----

    def run_action(self, action: Action) -> None:
        if not action.enabled():
            return
        threading.Thread(target=self.action_runner.run, args=(action,), daemon=True).start()

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

    def send_image_to_display(self, path: str, invert: bool = True) -> None:
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
        config = self.splash_config

        buffer: bytes | None = None

        try:
            if config.mode == SPLASH_MODE_STATIC:
                buffer = load_splash_binary(DEFAULT_SPLASH_PATH)
                if buffer is None:
                    return False
            elif config.mode == SPLASH_MODE_CLOCK:
                buffer = clock_to_oled_buffer(get_time_info())
            elif config.mode == SPLASH_MODE_TEXT:
                buffer = text_buffer_from_lines(
                    clock_to_lines(get_time_info()),
                    font_size=config.font_size,
                    alignment=config.alignment,
                    line_spacing=config.line_spacing,
                )
            elif config.mode == SPLASH_MODE_CUSTOM:
                buffer = text_buffer_from_lines(
                    render_template(config.template, get_time_info()),
                    font_size=config.font_size,
                    alignment=config.alignment,
                    line_spacing=config.line_spacing,
                )
            else:
                return False
        except Exception:
            return False

        self._events.fire("on_display_buffer", buffer)
        if self.client.is_open:
            try:
                self.client.send_image(buffer)
            except Exception:
                pass
        return True

    def load_splash(self) -> bytes | None:
        return load_splash_binary(DEFAULT_SPLASH_PATH)

    def save_splash(self, buffer: bytes) -> None:
        save_splash_binary(DEFAULT_SPLASH_PATH, buffer)

    def splash_path(self) -> Path:
        return DEFAULT_SPLASH_PATH

    # ---- system status display ----

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

    # ---- image helpers ----

    @staticmethod
    def pil_image_to_oled_buffer(pil_img: Any, invert: bool = True) -> bytes:
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
