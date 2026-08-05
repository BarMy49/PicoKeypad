import json
import os
import queue
import sys
import tempfile
import threading
import time
from pathlib import Path
from tkinter import filedialog
from typing import Any

import slint
from PIL import Image as PILImage, ImageDraw as PILDraw

try:
    import pystray
    from PIL import Image, ImageDraw

    HAS_PYSTRAY = True
except Exception:
    HAS_PYSTRAY = False

from ..core.actions import (
    ACTION_DISPLAY_TEXT,
    ACTION_FUNCTION,
    ACTION_HOTKEY,
    ACTION_MACRO,
    ACTION_TYPES,
    Action,
    ActionRunner,
    FUNCTIONS,
    KEY_GROUPS,
    display_lines_from_value,
    split_macro_line,
)
from ..core.bindings import BindingStore, EVENT_LABELS, EVENTS, LABEL_EVENTS, event_id_from_message
from ..core.display_rules import (
    DISPLAY_IMAGE,
    DISPLAY_MEDIA,
    DISPLAY_NONE,
    DISPLAY_TEXT,
    DISPLAY_TYPES,
    DISPLAY_VOLUME,
    DisplayRule,
    DisplayRuleStore,
)
from ..core import protocol
from ..core.serial_transport import PicoKeypadClient, SerialConnectionError
from ..core.splash_store import DEFAULT_SPLASH_PATH, load_splash_binary, save_splash_binary
from ..core.system_status import get_media_status, get_volume_status, media_display_lines, volume_display_lines

SCALE = 2

_EVENT_LABELS_LIST = [label for _, label in EVENTS]


class SlintKeypadApp:
    def __init__(
        self,
        initial_port: str | None = None,
        bindings_path: str | None = None,
        display_rules_path: str | None = None,
    ):
        self.client = PicoKeypadClient(port=initial_port)
        self.messages: queue.Queue[dict[str, Any]] = queue.Queue()
        self.binding_store = BindingStore(bindings_path)
        self.display_store = DisplayRuleStore(display_rules_path)
        self.action_runner = ActionRunner(
            on_error=lambda text: self.messages.put({"type": "action_error", "message": text}),
            on_display=lambda lines: self.messages.put({"type": "display_action", "lines": lines}),
        )
        self.reader_thread = None
        self.stop_reader = threading.Event()
        self._tray_icon = None

        self.encoder_direction: str | None = None
        self.encoder_button_down = False
        self.encoder_direction_timer: threading.Timer | None = None
        self.splash_inactivity_timer: threading.Timer | None = None
        self.preview_buffer: bytes | None = None
        self.splash_buffer: bytes | None = load_splash_binary(DEFAULT_SPLASH_PATH)

        self._dev_busy = False
        self._ready_status: str | None = None
        self._last_rx_time = 0.0
        self._last_ping_time = 0.0

        if getattr(sys, "frozen", False):
            self._app_dir = os.path.join(sys._MEIPASS, "app")
        else:
            self._app_dir = os.path.dirname(os.path.dirname(__file__))

        if getattr(sys, "frozen", False):
            slint_dir = os.path.join(sys._MEIPASS, "app", "gui_slint")
        else:
            slint_dir = os.path.dirname(__file__)
        slint_file = os.path.join(slint_dir, "main_window.slint")
        comps = slint.load_file(slint_file)
        self._window = comps.MainWindow()
        self._w = self._window

        self.preview_count = 0

        self._init_properties()
        self._wire_callbacks()

        self.refresh_ports()

    def _init_properties(self) -> None:
        w = self._w
        w.binding_events = slint.ListModel(_EVENT_LABELS_LIST)
        w.binding_kinds = slint.ListModel(list(ACTION_TYPES))
        w.binding_event = _EVENT_LABELS_LIST[0]
        w.binding_kind = ACTION_TYPES[0]
        w.binding_value = ""

        w.display_events = slint.ListModel(_EVENT_LABELS_LIST)
        w.display_kinds = slint.ListModel(list(DISPLAY_TYPES))
        w.display_event = _EVENT_LABELS_LIST[0]
        w.display_kind = DISPLAY_TYPES[0]
        w.display_value = ""

        keys_list: list[str] = []
        for group_name, keys in KEY_GROUPS:
            keys_list.append(f"--- {group_name} ---")
            keys_list.extend(keys)
        w.available_keys = slint.ListModel(keys_list)
        w.selected_key = ""

        w.log_lines = slint.ListModel([])
        w.port_list = slint.ListModel([])
        w.bindings_model = slint.ListModel([])
        w.display_rules_model = slint.ListModel([])

        self._on_binding_event_selected(_EVENT_LABELS_LIST[0])
        self._on_display_event_selected(_EVENT_LABELS_LIST[0])
        self._refresh_bindings_list()
        self._refresh_display_rules_list()
        self._update_splash_status()

        if self.splash_buffer is not None:
            self._update_oled_preview_from_buffer(self.splash_buffer)

    def _wire_callbacks(self) -> None:
        w = self._w
        w.connect_clicked = self._on_connect
        w.disconnect_clicked = self._on_disconnect
        w.toggle_connection = self._on_toggle_connection
        w.refresh_ports = self.refresh_ports
        w.key_clicked = self._on_key_clicked
        w.encoder_ccw_clicked = self._on_encoder_ccw
        w.encoder_cw_clicked = self._on_encoder_cw
        w.encoder_btn_clicked = self._on_encoder_btn

        w.send_text = self._on_send_text
        w.load_image = self._on_load_image
        w.clear_display = self._on_clear_display
        w.load_splash_preview = self._on_load_splash_preview
        w.save_current_splash = self._on_save_current_splash
        w.send_splash_to_device = self._on_send_splash_to_device

        w.save_binding = self._on_save_binding
        w.test_binding = self._on_test_binding
        w.clear_binding = self._on_clear_binding
        w.insert_key = self._on_insert_key

        w.save_display_rule = self._on_save_display_rule
        w.test_display_rule = self._on_test_display_rule
        w.clear_display_rule = self._on_clear_display_rule
        w.browse_display_image = self._on_browse_display_image

        w.binding_event_changed = self._on_binding_event_selected
        w.binding_kind_changed = self._on_binding_kind_changed
        w.binding_item_selected = self._on_binding_item_selected

        w.display_event_changed = self._on_display_event_selected
        w.display_kind_changed = self._on_display_kind_changed
        w.display_item_selected = self._on_display_item_selected

        w.poll_messages = self._process_messages
        w.watchdog_tick = self._watchdog_tick

    def run(self) -> None:
        self._tray_quit_requested = False

        while not self._tray_quit_requested:
            self._w.show()
            self._w.msg_timer_running = True
            self._w.wd_timer_running = True
            self._arm_splash_inactivity_timer()
            slint.run_event_loop()

            if not HAS_PYSTRAY or self._tray_quit_requested:
                break

            self._log("Application hidden to system tray")
            self._create_tray_icon()

    def _create_tray_icon(self) -> None:
        icon_path = os.path.join(self._app_dir, "icon.ico")
        if os.path.exists(icon_path):
            img = Image.open(icon_path)
        else:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.ellipse((4, 4, 60, 60), fill=(31, 157, 85, 255), outline=(0, 0, 0, 255))

        def _on_show(icon, item):
            icon.stop()

        def _on_quit(icon, item):
            self._tray_quit_requested = True
            icon.stop()

        menu = pystray.Menu(
            pystray.MenuItem("Show", _on_show),
            pystray.MenuItem("Quit", _on_quit),
        )
        icon = pystray.Icon("pico_keypad", img, "Pico Keypad", menu)
        self._tray_icon = icon
        icon.run()
        self._tray_icon = None

        if self._tray_quit_requested:
            try:
                if self.client.is_open:
                    self._on_disconnect()
                else:
                    self._cancel_splash_inactivity_timer()
            except Exception:
                pass

    def _on_toggle_connection(self) -> None:
        if self.client.is_open:
            self._on_disconnect()
        else:
            self._on_connect()

    def _on_connect(self) -> None:
        try:
            self.client.open(self._w.port_text.strip() or None)
        except Exception as exc:
            self._log(f"Connection failed: {exc}")
            return

        self._w.port_text = self.client.port or ""
        self.stop_reader.clear()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        self._set_connected(True)
        self._log(f"Connected to {self.client.port}")
        self._last_rx_time = time.monotonic()
        self._last_ping_time = 0.0
        self._arm_splash_inactivity_timer()

        try:
            self.client.ping()
        except Exception as exc:
            self._log(f"Ping failed: {exc}")

    def _on_disconnect(self) -> None:
        try:
            if self.client.is_open:
                self.client.send_disconnect()
        except Exception:
            pass

        self.stop_reader.set()
        self._cancel_splash_inactivity_timer()
        if self.reader_thread and self.reader_thread.is_alive():
            self.reader_thread.join(timeout=0.5)
        self.reader_thread = None
        try:
            self.client.close()
        except Exception:
            pass
        self._set_connected(False)
        self._log("Disconnected")

    def _reader_loop(self) -> None:
        while not self.stop_reader.is_set():
            try:
                message = self.client.read_message()
            except Exception as exc:
                self.messages.put({"type": "connection_error", "message": str(exc)})
                break
            if message:
                self.messages.put(message)

    def _process_messages(self) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break

            self._last_rx_time = time.monotonic()
            self._handle_message(message)

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")

        if message_type == "hello":
            display = message.get("display", {})
            ready = "ready" if display.get("ready") else "not ready"
            self._ready_status = f"Connected, display {ready}"
            if not self._dev_busy:
                self._w.status_text = self._ready_status
            self._log(json.dumps(message))
        elif message_type == "key":
            self._handle_key(message)
            self._run_binding_for_message(message)
            self._run_display_rule_for_message(message)
        elif message_type == "encoder":
            self._handle_encoder(message)
            self._run_binding_for_message(message)
            self._run_display_rule_for_message(message)
        elif message_type == "ack":
            self._log(f"ACK {message.get('result')}")
        elif message_type == "error":
            self._log(f"ERROR {message.get('where')}: {message.get('message')}")
        elif message_type == "action_error":
            self._log(f"ACTION ERROR: {message.get('message')}")
        elif message_type == "display_action":
            self._send_display_lines(message.get("lines", []), warn_if_disconnected=False)
        elif message_type == "display_image_action":
            self._send_display_image_path(str(message.get("path", "")), warn_if_disconnected=False)
        elif message_type == "pong":
            self._log("PONG")
        elif message_type == "mode":
            self._handle_mode(message)
        elif message_type == "connection_error":
            self._log(f"Connection error: {message.get('message')}")
            self._on_disconnect()
        elif message_type == "encoder_direction_clear":
            self._w.encoder_direction = ""
        elif message_type == "splash_inactivity":
            self.splash_inactivity_timer = None
            self._send_saved_splash_if_present()
        elif message_type not in ("empty",):
            self._log(message.get("message") or json.dumps(message))

    def _handle_mode(self, message: dict[str, Any]) -> None:
        state = message.get("state")
        if state == "secret":
            self._set_dev_busy(True)
        elif state == "normal":
            self._set_dev_busy(False)

    def _set_dev_busy(self, busy: bool) -> None:
        if busy == self._dev_busy:
            return
        self._dev_busy = busy
        if busy:
            self._w.status_text = "Game mode active - keypad offline"
            self._cancel_splash_inactivity_timer()
            self._log("Keypad entered game mode (K1 held)")
        else:
            self._w.status_text = self._ready_status or f"Connected to {self.client.port}"
            if self.client.is_open:
                self._arm_splash_inactivity_timer()
            self._log("Keypad returned to normal mode")

    def _watchdog_tick(self) -> None:
        if not self.client.is_open:
            return
        try:
            now = time.monotonic()
            if now - self._last_ping_time >= 1.0:
                self._last_ping_time = now
                try:
                    self.client.ping()
                except Exception:
                    pass
            self._set_dev_busy(now - self._last_rx_time > 2.5)
        except Exception:
            pass

    def _handle_key(self, message: dict[str, Any]) -> None:
        key = int(message.get("key", 0))
        pressed = message.get("event") == "down"

        key_props = {
            1: "key_1_pressed", 2: "key_2_pressed", 3: "key_3_pressed",
            4: "key_4_pressed", 5: "key_5_pressed", 6: "key_6_pressed",
            7: "key_7_pressed", 8: "key_8_pressed", 9: "key_9_pressed",
            10: "key_10_pressed", 11: "key_11_pressed", 12: "key_12_pressed",
        }
        if key in key_props:
            setattr(self._w, key_props[key], pressed)

        self._reset_splash_inactivity_timer()
        self._log(f"KEY {message.get('event')} {key}")

    def _handle_encoder(self, message: dict[str, Any]) -> None:
        event = message.get("event")
        if event == "turn":
            delta = int(message.get("delta", 0))
            self.encoder_direction = "cw" if delta > 0 else "ccw"
            self._w.encoder_direction = self.encoder_direction
            self._schedule_encoder_direction_clear()
            self._reset_splash_inactivity_timer()
            self._log(f"ENC {message.get('delta')} pos={message.get('position')}")
        elif event in ("button_down", "button_up"):
            self.encoder_button_down = bool(message.get("pressed"))
            self._w.encoder_btn_pressed = self.encoder_button_down
            self._reset_splash_inactivity_timer()
            self._log(f"ENC {event}")

    def _schedule_encoder_direction_clear(self) -> None:
        if self.encoder_direction_timer is not None:
            self.encoder_direction_timer.cancel()
        self.encoder_direction_timer = threading.Timer(0.35, self._clear_encoder_direction)
        self.encoder_direction_timer.daemon = True
        self.encoder_direction_timer.start()

    def _clear_encoder_direction(self) -> None:
        self.encoder_direction = None
        self.messages.put({"type": "encoder_direction_clear"})

    def _arm_splash_inactivity_timer(self) -> None:
        self._cancel_splash_inactivity_timer()
        self.splash_inactivity_timer = threading.Timer(2.0, self._on_splash_inactivity_timer_fired)
        self.splash_inactivity_timer.daemon = True
        self.splash_inactivity_timer.start()

    def _on_splash_inactivity_timer_fired(self) -> None:
        self.messages.put({"type": "splash_inactivity"})

    def _reset_splash_inactivity_timer(self) -> None:
        if self.client.is_open:
            self._arm_splash_inactivity_timer()

    def _cancel_splash_inactivity_timer(self) -> None:
        if self.splash_inactivity_timer is not None:
            self.splash_inactivity_timer.cancel()
            self.splash_inactivity_timer = None

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

        label = EVENT_LABELS.get(event_id, event_id)
        self._log(f"ACTION {label}: {action.kind} {self._short_value(action.value)}")

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
            self._send_display_lines(display_lines_from_value(rule.value), warn_if_disconnected=False)
        elif rule.kind == DISPLAY_IMAGE:
            self._send_display_image_path(rule.value, warn_if_disconnected=False)
        elif rule.kind == DISPLAY_VOLUME:
            self._send_system_status_display(DISPLAY_VOLUME)
        elif rule.kind == DISPLAY_MEDIA:
            self._send_system_status_display(DISPLAY_MEDIA)

    def _send_system_status_display(self, kind: str) -> None:
        def worker() -> None:
            if kind == DISPLAY_VOLUME:
                lines = volume_display_lines(get_volume_status())
            else:
                lines = media_display_lines(get_media_status())
            self.messages.put({"type": "display_action", "lines": lines})

        threading.Thread(target=worker, daemon=True).start()

    def _on_send_text(self) -> None:
        lines = self._w.text_input.splitlines() or [""]
        self._send_display_lines(lines, warn_if_disconnected=True)

    def _on_clear_display(self) -> None:
        if not self._can_send():
            return
        try:
            self.client.send_clear()
            self._clear_oled_preview()
        except Exception as exc:
            self._log(f"Clear failed: {exc}")

    def _on_load_image(self) -> None:
        if not self._can_send():
            return

        path = filedialog.askopenfilename(
            filetypes=(
                ("Images", "*.png *.gif *.ppm *.pgm"),
                ("All files", "*.*"),
            )
        )
        if not path:
            return

        self._load_and_send_image_file(path)

    def _load_and_send_image_file(self, path: str) -> None:
        try:
            pil_img = PILImage.open(path)
            buffer = self._pil_image_to_oled_buffer(pil_img)
            self.preview_buffer = buffer
            self.client.send_image(buffer)
            self._update_oled_preview_from_buffer(buffer)
            self._log(f"Image sent: {path}")
        except Exception as exc:
            self._log(f"Image failed: {exc}")

    def _splash_status_text(self) -> str:
        if self.splash_buffer is not None:
            return f"Splash ready: {DEFAULT_SPLASH_PATH.name} ({len(self.splash_buffer)} bytes)"
        if DEFAULT_SPLASH_PATH.exists():
            return f"Splash file present: {DEFAULT_SPLASH_PATH.name}"
        return f"No splash saved at {DEFAULT_SPLASH_PATH.name}"

    def _update_splash_status(self) -> None:
        self._w.splash_status = self._splash_status_text()

    def _on_load_splash_preview(self) -> None:
        buffer = load_splash_binary(DEFAULT_SPLASH_PATH)
        if buffer is None:
            self.splash_buffer = None
            self._update_splash_status()
            self._log(f"No valid splash file at {DEFAULT_SPLASH_PATH}")
            return

        self.splash_buffer = buffer
        self.preview_buffer = buffer
        self._update_splash_status()
        self._update_oled_preview_from_buffer(buffer)
        self._log(f"Loaded splash preview from {DEFAULT_SPLASH_PATH}")

    def _on_save_current_splash(self) -> None:
        buffer = self.preview_buffer or self.splash_buffer
        if buffer is None:
            self._log("Load an image first")
            return

        try:
            save_splash_binary(DEFAULT_SPLASH_PATH, buffer)
            self.splash_buffer = buffer
            self._update_splash_status()
            self._log(f"Saved splash to {DEFAULT_SPLASH_PATH}")
        except Exception as exc:
            self._log(f"Splash save failed: {exc}")

    def _send_saved_splash_if_present(self) -> None:
        buffer = load_splash_binary(DEFAULT_SPLASH_PATH)
        if buffer is None:
            self._update_splash_status()
            self._log("No splash to send")
            return

        try:
            self.client.send_image(buffer)
            self.splash_buffer = buffer
            self._update_splash_status()
            self._update_oled_preview_from_buffer(buffer)
            self._log(f"Sent splash from {DEFAULT_SPLASH_PATH} on connect")
        except Exception as exc:
            self._log(f"Splash send failed: {exc}")

    def _on_send_splash_to_device(self) -> None:
        if not self._can_send():
            return
        buffer = load_splash_binary(DEFAULT_SPLASH_PATH)
        if buffer is None:
            self._log(f"No valid splash file found at {DEFAULT_SPLASH_PATH}")
            return
        try:
            self.client.send_image(buffer)
            self.splash_buffer = buffer
            self._update_splash_status()
            self._update_oled_preview_from_buffer(buffer)
            self._log(f"Sent splash to device")
        except Exception as exc:
            self._log(f"Splash send failed: {exc}")

    def _send_display_lines(self, lines: list[str], warn_if_disconnected: bool) -> None:
        lines = self._render_display_lines(lines)
        if not lines:
            lines = [""]

        if not self.client.is_open:
            self._update_oled_preview_from_lines(lines)
            self._log("Display skipped: serial port is not connected")
            self._arm_splash_inactivity_timer()
            return

        try:
            self.client.send_text(lines)
            self._update_oled_preview_from_lines(lines)
            self._arm_splash_inactivity_timer()
        except Exception as exc:
            self._log(f"Send display failed: {exc}")

    def _send_display_image_path(self, path: str, warn_if_disconnected: bool) -> None:
        path = path.strip()
        if not path:
            self._log("Display image skipped: image path is empty")
            return

        try:
            pil_img = PILImage.open(path)
            buffer = self._pil_image_to_oled_buffer(pil_img)
        except Exception as exc:
            self._log(f"Display image failed: {exc}")
            return

        if not self.client.is_open:
            self._update_oled_preview_from_buffer(buffer)
            self._log("Display image skipped: serial port is not connected")
            self._arm_splash_inactivity_timer()
            return

        try:
            self.client.send_image(buffer)
            self._update_oled_preview_from_buffer(buffer)
            self._arm_splash_inactivity_timer()
        except Exception as exc:
            self._log(f"Send image failed: {exc}")

    def _render_display_lines(self, lines: list[str]) -> list[str]:
        rendered = []
        needs_volume = any("{volume}" in str(line) or "{mute}" in str(line) for line in lines)
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

    def _volume_action_from_action(self, action: Action) -> str | None:
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

    def _pil_image_to_oled_buffer(self, pil_img: Any) -> bytes:
        src_width, src_height = pil_img.size
        if src_width <= 0 or src_height <= 0:
            raise ValueError("Image is empty")

        if pil_img.mode not in ("RGB", "RGBA"):
            pil_img = pil_img.convert("RGB")

        buffer = bytearray(protocol.DISPLAY_BUFFER_SIZE)
        invert = self._w.image_invert

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

    def _update_oled_preview_from_lines(self, lines: list[str]) -> None:
        try:
            from PIL import Image as PILImage, ImageDraw as PILDraw, ImageFont

            img = PILImage.new("1", (protocol.DISPLAY_WIDTH, protocol.DISPLAY_HEIGHT), 0)
            draw = PILDraw.Draw(img)
            try:
                font = ImageFont.truetype("consola.ttf", 8)
            except Exception:
                try:
                    font = ImageFont.truetype("cour.ttf", 8)
                except Exception:
                    font = ImageFont.load_default()
            for idx, line in enumerate(lines[:4]):
                draw.text((0, idx * 8), line[:16], fill=1, font=font)
            self._set_oled_preview_image(img)
        except Exception:
            self._clear_oled_preview()

    def _update_oled_preview_from_buffer(self, buffer: bytes) -> None:
        try:
            from PIL import Image as PILImage

            img = PILImage.new("1", (protocol.DISPLAY_WIDTH, protocol.DISPLAY_HEIGHT), 0)
            for y in range(protocol.DISPLAY_HEIGHT):
                for x in range(protocol.DISPLAY_WIDTH):
                    value = buffer[x + (y // 8) * protocol.DISPLAY_WIDTH] & (1 << (y & 7))
                    if value:
                        img.putpixel((x, y), 1)
            self._set_oled_preview_image(img)
        except Exception:
            self._clear_oled_preview()

    def _set_oled_preview_image(self, pil_image: Any) -> None:
        try:
            import PIL

            self.preview_count += 1
            scaled = pil_image.resize(
                (protocol.DISPLAY_WIDTH * SCALE, protocol.DISPLAY_HEIGHT * SCALE),
                PIL.Image.NEAREST,
            )
            rgba = scaled.convert("RGBA")
            tmp = os.path.join(tempfile.gettempdir(), f"pico_keypad_oled_{self.preview_count % 2}.png")
            rgba.save(tmp)
            self._w.oled_preview_image = slint.Image.load_from_path(tmp)
        except Exception:
            pass

    def _clear_oled_preview(self) -> None:
        try:
            from PIL import Image as PILImage

            self.preview_count += 1
            img = PILImage.new("RGBA", (protocol.DISPLAY_WIDTH * SCALE, protocol.DISPLAY_HEIGHT * SCALE), (0, 0, 0, 255))
            tmp = os.path.join(tempfile.gettempdir(), f"pico_keypad_oled_{self.preview_count % 2}.png")
            img.save(tmp)
            self._w.oled_preview_image = slint.Image.load_from_path(tmp)
        except Exception:
            pass

    def _can_send(self) -> bool:
        if self.client.is_open:
            return True
        self._log("Not connected to device")
        return False

    def _on_key_clicked(self, key: int) -> None:
        self._select_binding_event(f"key:{key}:down")

    def _on_encoder_ccw(self) -> None:
        self._select_binding_event("encoder:ccw")

    def _on_encoder_cw(self) -> None:
        self._select_binding_event("encoder:cw")

    def _on_encoder_btn(self) -> None:
        self._select_binding_event("encoder:button_down")

    def _select_binding_event(self, event_id: str) -> None:
        label = EVENT_LABELS.get(event_id, event_id)
        self._w.binding_event = label
        self._on_binding_event_selected(label)

        self._w.selected_binding_index = -1
        for i, event_item in enumerate(sorted(self.binding_store.bindings.keys())):
            if event_item == event_id:
                self._w.selected_binding_index = i
                break

    def refresh_ports(self) -> None:
        try:
            ports = PicoKeypadClient.list_ports()
        except SerialConnectionError as exc:
            self._log(str(exc))
            self._w.port_list = slint.ListModel([])
            return

        values = [port.device for port in ports]
        labels = {port.device: port.label for port in ports}
        self._w.port_list = slint.ListModel(values)

        if not self._w.port_text and values:
            detected = PicoKeypadClient.autodetect_port()
            self._w.port_text = detected or values[0]

        for port in values:
            self._log(labels[port])

    def _on_binding_event_selected(self, label: str) -> None:
        event_id = LABEL_EVENTS.get(label, label)
        action = self.binding_store.get(event_id)
        self._w.binding_kind = action.kind
        self._w.binding_value = action.value
        self._on_binding_kind_changed(action.kind)

    def _on_binding_kind_changed(self, kind: str) -> None:
        current = self._w.binding_value
        if kind == ACTION_FUNCTION and current not in FUNCTIONS:
            self._w.binding_value = "volume_up"
        elif kind == ACTION_MACRO and not current:
            self._w.binding_value = (
                "hotkey: ctrl+c\nsleep: 100\nhotkey: ctrl+v\ndisplay: COPIED | TO CLIPBOARD"
            )
        elif kind == ACTION_DISPLAY_TEXT and not current:
            self._w.binding_value = "Button action\n{volume}% {mute}"

    def _on_binding_item_selected(self, idx: int) -> None:
        bindings = sorted(self.binding_store.bindings.items())
        if 0 <= idx < len(bindings):
            event_id = EVENT_LABELS.get(bindings[idx][0], bindings[idx][0])
            self._w.binding_event = event_id
            self._on_binding_event_selected(event_id)

    def _on_insert_key(self) -> None:
        key_name = self._w.selected_key
        if not key_name or key_name.startswith("---"):
            return

        kind = self._w.binding_kind
        if kind == ACTION_FUNCTION:
            self._w.binding_value = key_name
            return

        if kind == ACTION_HOTKEY:
            current = self._w.binding_value
            separator = "+" if current and not current.endswith(("+", " ", "\n")) else ""
            self._w.binding_value = current + separator + key_name
            return

        if kind == ACTION_MACRO:
            self._w.binding_value = self._w.binding_value + key_name

    def _on_save_binding(self) -> None:
        label = self._w.binding_event
        event_id = LABEL_EVENTS.get(label, label)
        action = Action(kind=self._w.binding_kind, value=self._w.binding_value)
        self.binding_store.set(event_id, action)
        self._refresh_bindings_list()
        self._log(f"Saved binding {label}")

    def _on_clear_binding(self) -> None:
        label = self._w.binding_event
        event_id = LABEL_EVENTS.get(label, label)
        self.binding_store.clear(event_id)
        self._w.binding_kind = ACTION_TYPES[0]
        self._w.binding_value = ""
        self._refresh_bindings_list()
        self._log(f"Cleared binding {label}")

    def _on_test_binding(self) -> None:
        action = Action(kind=self._w.binding_kind, value=self._w.binding_value)
        if not action.enabled():
            self._log("No action to test")
            return
        self.action_runner.run(action)

    def _refresh_bindings_list(self) -> None:
        items = []
        for event_id, action in sorted(self.binding_store.bindings.items()):
            label = EVENT_LABELS.get(event_id, event_id)
            items.append(f"{label}  |  {action.kind}  |  {self._short_value(action.value)}")
        self._w.bindings_model = slint.ListModel(items)

    def _on_display_event_selected(self, label: str) -> None:
        event_id = LABEL_EVENTS.get(label, label)
        rule = self.display_store.get(event_id)
        self._w.display_kind = rule.kind
        self._w.display_value = rule.value
        self._on_display_kind_changed(rule.kind)

    def _on_display_kind_changed(self, kind: str) -> None:
        current = self._w.display_value
        if kind == DISPLAY_TEXT and not current:
            self._w.display_value = "Button {volume}%\n{mute}"
        elif kind == DISPLAY_VOLUME:
            self._w.display_value = "System master volume"
        elif kind == DISPLAY_MEDIA:
            self._w.display_value = "Current media session"
        elif kind == DISPLAY_IMAGE and current in ("System master volume", "Current media session"):
            self._w.display_value = ""

    def _on_display_item_selected(self, idx: int) -> None:
        rules = sorted(self.display_store.rules.items())
        if 0 <= idx < len(rules):
            event_id = EVENT_LABELS.get(rules[idx][0], rules[idx][0])
            self._w.display_event = event_id
            self._on_display_event_selected(event_id)

    def _on_save_display_rule(self) -> None:
        label = self._w.display_event
        event_id = LABEL_EVENTS.get(label, label)
        kind = self._w.display_kind
        value = "" if kind in (DISPLAY_VOLUME, DISPLAY_MEDIA) else self._w.display_value
        rule = DisplayRule(kind=kind, value=value)
        self.display_store.set(event_id, rule)
        self._refresh_display_rules_list()
        self._log(f"Saved display rule {label}")

    def _on_clear_display_rule(self) -> None:
        label = self._w.display_event
        event_id = LABEL_EVENTS.get(label, label)
        self.display_store.clear(event_id)
        self._w.display_kind = DISPLAY_NONE
        self._w.display_value = ""
        self._refresh_display_rules_list()
        self._log(f"Cleared display rule {label}")

    def _on_test_display_rule(self) -> None:
        kind = self._w.display_kind
        value = "" if kind in (DISPLAY_VOLUME, DISPLAY_MEDIA) else self._w.display_value
        rule = DisplayRule(kind=kind, value=value)
        if not rule.enabled():
            self._log("No display rule to test")
            return
        self._apply_display_rule(rule)

    def _on_browse_display_image(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=(
                ("Images", "*.png *.gif *.ppm *.pgm"),
                ("All files", "*.*"),
            )
        )
        if not path:
            return
        self._w.display_kind = DISPLAY_IMAGE
        self._w.display_value = path

    def _refresh_display_rules_list(self) -> None:
        items = []
        for event_id, rule in sorted(self.display_store.rules.items()):
            label = EVENT_LABELS.get(event_id, event_id)
            items.append(f"{label}  |  {rule.kind}  |  {self._short_value(rule.value)}")
        self._w.display_rules_model = slint.ListModel(items)

    def _set_connected(self, connected: bool) -> None:
        self._w.connected = connected
        self._w.status_text = f"Connected to {self.client.port}" if connected else "Disconnected"

    def _log(self, text: str) -> None:
        current = list(self._w.log_lines)
        current.insert(0, text)
        if len(current) > 500:
            current = current[:500]
        self._w.log_lines = slint.ListModel(current)

    @staticmethod
    def _short_value(value: str, limit: int = 56) -> str:
        value = " ".join(value.splitlines())
        if len(value) <= limit:
            return value
        return value[:limit - 3] + "..."


def run(
    port: str | None = None,
    bindings_path: str | None = None,
    display_rules_path: str | None = None,
) -> None:
    app = SlintKeypadApp(
        initial_port=port,
        bindings_path=bindings_path,
        display_rules_path=display_rules_path,
    )
    app.run()
