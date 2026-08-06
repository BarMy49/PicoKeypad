import os
import queue
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

import slint
from PIL import Image as PILImage, ImageDraw as PILDraw

try:
    from tkinter import filedialog

    HAS_FILEDIALOG = True
except Exception:
    HAS_FILEDIALOG = False

try:
    import pystray
    from PIL import Image

    HAS_PYSTRAY = True
except Exception:
    HAS_PYSTRAY = False

from ..core import protocol
from ..core.actions import (
    ACTION_DISPLAY_TEXT,
    ACTION_FUNCTION,
    ACTION_HOTKEY,
    ACTION_MACRO,
    ACTION_TYPES,
    Action,
    FUNCTIONS,
    KEY_GROUPS,
    display_lines_from_value,
)
from ..core.display_rules import (
    DISPLAY_IMAGE,
    DISPLAY_MEDIA,
    DISPLAY_NONE,
    DISPLAY_TEXT,
    DISPLAY_TYPES,
    DISPLAY_VOLUME,
    DisplayRule,
)
from ..core.bindings import EVENT_LABELS, EVENTS, LABEL_EVENTS
from ..core.engine import PicoKeypadEngine
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
        self._engine = PicoKeypadEngine(
            initial_port=initial_port,
            bindings_path=bindings_path,
            display_rules_path=display_rules_path,
        )
        self._msg_queue: queue.Queue[Any] = queue.Queue()
        self._tray_icon = None
        self._tray_quit_requested = False

        self._encoder_direction: str | None = None
        self._encoder_button_down = False
        self._encoder_direction_timer: threading.Timer | None = None
        self._preview_buffer: bytes | None = None
        self._splash_buffer: bytes | None = self._engine.load_splash()

        self._preview_count = 0

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

        self._init_properties()
        self._wire_callbacks()
        self._wire_engine_callbacks()

        self._refresh_ports()

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

        if self._splash_buffer is not None:
            self._update_oled_preview_from_buffer(self._splash_buffer)

    def _wire_callbacks(self) -> None:
        w = self._w
        w.toggle_connection = self._on_toggle_connection
        w.refresh_ports = self._refresh_ports
        w.key_clicked = self._on_key_clicked
        w.encoder_ccw_clicked = self._on_encoder_ccw
        w.encoder_cw_clicked = self._on_encoder_cw
        w.encoder_btn_clicked = self._on_encoder_btn

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

        w.poll_messages = self._poll_messages
        w.watchdog_tick = lambda: None

    def _wire_engine_callbacks(self) -> None:
        events = self._engine.events

        def on_key_state(key: int, pressed: bool) -> None:
            self._msg_queue.put(("key_state", key, pressed))

        def on_encoder_state(direction: str | None, btn_pressed: bool | None, delta: int = 0) -> None:
            self._msg_queue.put(("encoder_state", direction, btn_pressed, delta))

        events.set("on_key_state", on_key_state)
        events.set("on_encoder_state", on_encoder_state)
        events.set("on_status", lambda text, conn: self._msg_queue.put(("status", text, conn)))
        events.set("on_log", lambda text: self._msg_queue.put(("log", text)))
        events.set("on_display_lines", lambda lines: self._msg_queue.put(("display_lines", lines)))
        events.set("on_display_buffer", lambda buf: self._msg_queue.put(("display_buffer", buf)))
        events.set("on_connecting", lambda: None)
        events.set("on_device_busy", lambda busy: None)

    def _poll_messages(self) -> None:
        while True:
            try:
                item = self._msg_queue.get_nowait()
            except queue.Empty:
                break
            self._process_item(item)

    def _process_item(self, item: tuple) -> None:
        kind = item[0]
        if kind == "key_state":
            self._do_key_state(item[1], item[2])
        elif kind == "encoder_state":
            self._do_encoder_state(item[1], item[2], item[3])
        elif kind == "encoder_clear":
            self._w.encoder_direction = ""
        elif kind == "status":
            self._do_status(item[1], item[2])
        elif kind == "log":
            self._do_log(item[1])
        elif kind == "display_lines":
            self._do_display_lines(item[1])
        elif kind == "display_buffer":
            self._do_display_buffer(item[1])

    def _do_key_state(self, key: int, pressed: bool) -> None:
        key_props = {
            1: "key_1_pressed", 2: "key_2_pressed", 3: "key_3_pressed",
            4: "key_4_pressed", 5: "key_5_pressed", 6: "key_6_pressed",
            7: "key_7_pressed", 8: "key_8_pressed", 9: "key_9_pressed",
            10: "key_10_pressed", 11: "key_11_pressed", 12: "key_12_pressed",
        }
        if key in key_props:
            try:
                setattr(self._w, key_props[key], pressed)
            except Exception:
                pass

    def _do_encoder_state(self, direction: str | None, btn_pressed: bool | None, delta: int = 0) -> None:
        if direction is not None:
            self._encoder_direction = direction
            self._w.encoder_direction = direction
            self._schedule_encoder_direction_clear()
        if btn_pressed is not None:
            self._encoder_button_down = bool(btn_pressed)
            self._w.encoder_btn_pressed = self._encoder_button_down

    def _do_status(self, text: str, connected: bool) -> None:
        self._w.status_text = text
        self._w.connected = connected
        if connected:
            self._w.port_text = self._engine.connected_port()

    def _do_log(self, text: str) -> None:
        current = list(self._w.log_lines)
        current.insert(0, text)
        if len(current) > 500:
            current = current[:500]
        self._w.log_lines = slint.ListModel(current)

    def _do_display_lines(self, lines: list[str]) -> None:
        self._update_oled_preview_from_lines(lines)

    def _do_display_buffer(self, buffer: bytes) -> None:
        self._preview_buffer = buffer
        self._update_oled_preview_from_buffer(buffer)

    def run(self) -> None:
        self._tray_quit_requested = False

        while not self._tray_quit_requested:
            self._w.show()
            self._w.msg_timer_running = True
            self._w.wd_timer_running = False
            slint.run_event_loop()

            if not HAS_PYSTRAY or self._tray_quit_requested:
                break

            self._log("Application hidden to system tray")
            self._create_tray_icon()

        self._engine.stop()

    def _create_tray_icon(self) -> None:
        icon_path = os.path.join(self._app_dir, "icon.ico")
        if os.path.exists(icon_path):
            img = Image.open(icon_path)
        else:
            img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
            draw = PILDraw.Draw(img)
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
                self._engine.disconnect()
            except Exception:
                pass

    def _on_toggle_connection(self) -> None:
        if self._engine.is_connected():
            self._engine.disconnect()
        else:
            port = self._w.port_text.strip() or None
            self._w.status_text = "Connecting..."
            self._engine.connect(port)

    def _schedule_encoder_direction_clear(self) -> None:
        if self._encoder_direction_timer is not None:
            self._encoder_direction_timer.cancel()
        self._encoder_direction_timer = threading.Timer(0.35, self._clear_encoder_direction)
        self._encoder_direction_timer.daemon = True
        self._encoder_direction_timer.start()

    def _clear_encoder_direction(self) -> None:
        self._encoder_direction = None
        self._msg_queue.put(("encoder_clear",))

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
        bindings = sorted(self._engine.get_all_bindings().keys())
        for i, event_item in enumerate(bindings):
            if event_item == event_id:
                self._w.selected_binding_index = i
                break

    def _refresh_ports(self) -> None:
        ports = self._engine.refresh_ports()
        values = [device for device, _ in ports]
        self._w.port_list = slint.ListModel(values)

        if not self._w.port_text and values:
            detected = PicoKeypadEngine.autodetect_port()
            self._w.port_text = detected or values[0]

    def _splash_status_text(self) -> str:
        path = self._engine.splash_path()
        if self._splash_buffer is not None:
            return f"Splash ready: {path.name} ({len(self._splash_buffer)} bytes)"
        if path.exists():
            return f"Splash file present: {path.name}"
        return f"No splash saved at {path.name}"

    def _update_splash_status(self) -> None:
        self._w.splash_status = self._splash_status_text()

    def _on_load_splash_preview(self) -> None:
        buffer = self._engine.load_splash()
        if buffer is None:
            self._splash_buffer = None
            self._update_splash_status()
            self._log(f"No valid splash file at {self._engine.splash_path()}")
            return
        self._splash_buffer = buffer
        self._preview_buffer = buffer
        self._update_splash_status()
        self._update_oled_preview_from_buffer(buffer)
        self._log(f"Loaded splash preview from {self._engine.splash_path()}")

    def _on_save_current_splash(self) -> None:
        buffer = self._preview_buffer or self._splash_buffer
        if buffer is None:
            self._log("Load an image first")
            return
        try:
            self._engine.save_splash(buffer)
            self._splash_buffer = buffer
            self._update_splash_status()
            self._log(f"Saved splash to {self._engine.splash_path()}")
        except Exception as exc:
            self._log(f"Splash save failed: {exc}")

    def _on_send_splash_to_device(self) -> None:
        if not self._engine.is_connected():
            self._log("Not connected to device")
            return
        success = self._engine.send_splash_to_device()
        if success:
            self._splash_buffer = self._engine.load_splash()
            if self._splash_buffer:
                self._update_oled_preview_from_buffer(self._splash_buffer)
            self._update_splash_status()

    def _on_binding_event_selected(self, label: str) -> None:
        event_id = LABEL_EVENTS.get(label, label)
        action = self._engine.get_binding(event_id)
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
        bindings = sorted(self._engine.get_all_bindings().items())
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
        self._engine.set_binding(event_id, action)
        self._refresh_bindings_list()
        self._log(f"Saved binding {label}")

    def _on_clear_binding(self) -> None:
        label = self._w.binding_event
        event_id = LABEL_EVENTS.get(label, label)
        self._engine.clear_binding(event_id)
        self._w.binding_kind = ACTION_TYPES[0]
        self._w.binding_value = ""
        self._refresh_bindings_list()
        self._log(f"Cleared binding {label}")

    def _on_test_binding(self) -> None:
        action = Action(kind=self._w.binding_kind, value=self._w.binding_value)
        if not action.enabled():
            self._log("No action to test")
            return
        self._engine.run_action(action)

    def _refresh_bindings_list(self) -> None:
        items = []
        for event_id, action in sorted(self._engine.get_all_bindings().items()):
            label = EVENT_LABELS.get(event_id, event_id)
            items.append(f"{label}  |  {action.kind}  |  {self._short_value(action.value)}")
        self._w.bindings_model = slint.ListModel(items)

    def _on_display_event_selected(self, label: str) -> None:
        event_id = LABEL_EVENTS.get(label, label)
        rule = self._engine.get_display_rule(event_id)
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
        rules = sorted(self._engine.get_all_display_rules().items())
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
        self._engine.set_display_rule(event_id, rule)
        self._refresh_display_rules_list()
        self._log(f"Saved display rule {label}")

    def _on_clear_display_rule(self) -> None:
        label = self._w.display_event
        event_id = LABEL_EVENTS.get(label, label)
        self._engine.clear_display_rule(event_id)
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

    def _apply_display_rule(self, rule: DisplayRule) -> None:
        if rule.kind == DISPLAY_TEXT:
            lines = display_lines_from_value(rule.value)
            if self._engine.is_connected():
                self._engine.send_text_to_display(lines)
            else:
                self._update_oled_preview_from_lines(lines)
        elif rule.kind == DISPLAY_IMAGE:
            if self._engine.is_connected():
                self._engine.send_image_to_display(rule.value)
            else:
                try:
                    pil_img = PILImage.open(rule.value.strip())
                    buffer = self._engine.pil_image_to_oled_buffer(pil_img)
                    self._update_oled_preview_from_buffer(buffer)
                except Exception as exc:
                    self._log(f"Display image failed: {exc}")
        elif rule.kind == DISPLAY_VOLUME:
            lines = volume_display_lines(get_volume_status())
            if self._engine.is_connected():
                try:
                    self._engine.client.send_text(lines)
                except Exception:
                    pass
            self._update_oled_preview_from_lines(lines)
        elif rule.kind == DISPLAY_MEDIA:
            lines = media_display_lines(get_media_status())
            if self._engine.is_connected():
                try:
                    self._engine.client.send_text(lines)
                except Exception:
                    pass
            self._update_oled_preview_from_lines(lines)

    def _on_browse_display_image(self) -> None:
        if not HAS_FILEDIALOG:
            self._log("File dialog not available (tkinter missing)")
            return
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
        for event_id, rule in sorted(self._engine.get_all_display_rules().items()):
            label = EVENT_LABELS.get(event_id, event_id)
            items.append(f"{label}  |  {rule.kind}  |  {self._short_value(rule.value)}")
        self._w.display_rules_model = slint.ListModel(items)

    def _update_oled_preview_from_lines(self, lines: list[str]) -> None:
        try:
            img = self._engine.lines_to_pil_image(lines)
            self._set_oled_preview_image(img)
        except Exception:
            self._clear_oled_preview()

    def _update_oled_preview_from_buffer(self, buffer: bytes) -> None:
        try:
            img = self._engine.oled_buffer_to_pil_image(buffer)
            self._set_oled_preview_image(img)
        except Exception:
            self._clear_oled_preview()

    def _set_oled_preview_image(self, pil_image: Any) -> None:
        try:
            self._preview_count += 1
            scaled = pil_image.resize(
                (protocol.DISPLAY_WIDTH * SCALE, protocol.DISPLAY_HEIGHT * SCALE),
                PILImage.NEAREST,
            )
            rgba = scaled.convert("RGBA")
            tmp = os.path.join(tempfile.gettempdir(), f"pico_keypad_oled_{self._preview_count % 2}.png")
            rgba.save(tmp)
            self._w.oled_preview_image = slint.Image.load_from_path(tmp)
        except Exception:
            pass

    def _clear_oled_preview(self) -> None:
        try:
            self._preview_count += 1
            img = PILImage.new("RGBA", (protocol.DISPLAY_WIDTH * SCALE, protocol.DISPLAY_HEIGHT * SCALE), (0, 0, 0, 255))
            tmp = os.path.join(tempfile.gettempdir(), f"pico_keypad_oled_{self._preview_count % 2}.png")
            img.save(tmp)
            self._w.oled_preview_image = slint.Image.load_from_path(tmp)
        except Exception:
            pass

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
