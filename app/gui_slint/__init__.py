import json
import os
import queue
import sys
import tempfile
import threading
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
    ACTION_DISABLED,
    ACTION_HOTKEY,
    ACTION_MACRO,
    ACTION_MEDIA,
    ACTION_TOGGLE,
    ACTION_TYPES,
    ACTION_VOLUME,
    DISPLAY_IMAGE,
    DISPLAY_MEDIA,
    DISPLAY_NONE,
    DISPLAY_TEXT,
    DISPLAY_TYPES,
    DISPLAY_VOLUME,
    MEDIA_VALUES,
    VOLUME_VALUES,
    Action,
    KEY_GROUPS,
)
from ..core.actions_store import EVENT_LABELS, EVENTS, LABEL_EVENTS
from ..core.engine import PicoKeypadEngine
from ..core.splash_store import (
    SPLASH_MODE_STATIC,
    SPLASH_MODES,
    SplashConfig,
)

SCALE = 2

_EVENT_LABELS_LIST = [label for _, label in EVENTS]


class SlintKeypadApp:
    def __init__(
        self,
        initial_port: str | None = None,
        actions_path: str | None = None,
        start_minimized: bool | None = None,
    ):
        if getattr(sys, "frozen", False):
            self._app_dir = os.path.dirname(sys.executable)
        else:
            self._app_dir = os.path.dirname(os.path.dirname(__file__))

        settings = self._load_settings()
        if initial_port is None:
            initial_port = settings.get("port")
        if start_minimized is None:
            start_minimized = settings.get("start_minimized", False)
        self._start_minimized = start_minimized
        self._connect_on_start = settings.get("connect_on_start", True)

        self._engine = PicoKeypadEngine(
            initial_port=initial_port,
            actions_path=actions_path,
        )
        self._engine.set_auto_reconnect(self._connect_on_start)
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
            slint_dir = os.path.join(sys._MEIPASS, "app", "gui_slint")
        else:
            slint_dir = os.path.dirname(__file__)
        slint_file = os.path.join(slint_dir, "main_window.slint")
        comps = slint.load_file(slint_file)
        self._window = comps.MainWindow()
        self._w = self._window

        if initial_port:
            self._w.port_text = initial_port

        self._w.connect_on_start = self._connect_on_start
        self._w.start_minimized = self._start_minimized

        self._init_properties()
        self._wire_callbacks()
        self._wire_engine_callbacks()

        self._refresh_ports()

        if self._connect_on_start and initial_port:
            self._w.status_text = "Connecting..."
            self._engine.connect(initial_port)

    def _settings_path(self) -> str:
        return os.path.join(self._app_dir, "settings.json")

    def _load_settings(self) -> dict:
        path = self._settings_path()
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_settings(self, updates: dict) -> None:
        settings = self._load_settings()
        settings.update(updates)
        path = self._settings_path()
        try:
            with open(path, "w") as f:
                json.dump(settings, f, indent=2)
        except Exception:
            pass

    def _on_settings_changed(self) -> None:
        self._connect_on_start = self._w.connect_on_start
        self._start_minimized = self._w.start_minimized
        self._save_settings({
            "connect_on_start": self._connect_on_start,
            "start_minimized": self._start_minimized,
        })
        self._engine.set_auto_reconnect(self._connect_on_start)

    def _init_properties(self) -> None:
        w = self._w
        w.action_events = slint.ListModel(_EVENT_LABELS_LIST)
        w.action_kinds = slint.ListModel(list(ACTION_TYPES))
        w.volume_values = slint.ListModel(list(VOLUME_VALUES))
        w.media_values = slint.ListModel(list(MEDIA_VALUES))
        w.display_kinds = slint.ListModel(list(DISPLAY_TYPES))
        w.splash_modes = slint.ListModel(list(SPLASH_MODES))
        w.splash_alignments = slint.ListModel(["left", "center"])

        w.action_event = _EVENT_LABELS_LIST[0]
        w.action_kind = ACTION_DISABLED
        w.action_value = ""
        w.volume_value = VOLUME_VALUES[0]
        w.media_value = MEDIA_VALUES[0]
        w.display_kind = DISPLAY_NONE
        w.display_value = ""

        keys_list: list[str] = []
        for group_name, keys in KEY_GROUPS:
            keys_list.append(f"--- {group_name} ---")
            keys_list.extend(keys)
        w.available_keys = slint.ListModel(keys_list)
        w.selected_key = ""

        w.log_lines = slint.ListModel([])
        w.port_list = slint.ListModel([])
        w.actions_model = slint.ListModel([])

        self._load_splash_config_widgets()

        self._on_action_event_selected(_EVENT_LABELS_LIST[0])
        self._refresh_actions_list()
        self._update_splash_status()

        if self._splash_buffer is not None:
            self._update_oled_preview_from_buffer(self._splash_buffer)

    def _load_splash_config_widgets(self) -> None:
        config = self._engine.get_splash_config()
        self._w.splash_mode = config.mode
        self._w.splash_interval = self._format_float(config.interval)
        self._w.splash_idle = self._format_float(config.idle_timeout)
        self._w.splash_template = config.template
        self._w.splash_font_size = str(config.font_size)
        self._w.splash_line_spacing = str(config.line_spacing)
        self._w.splash_alignment = config.alignment if config.alignment in ("left", "center") else "left"

    @staticmethod
    def _format_float(value: float) -> str:
        text = f"{value:.1f}"
        return text[:-2] if text.endswith(".0") else text

    def _wire_callbacks(self) -> None:
        w = self._w
        w.toggle_connection = self._on_toggle_connection
        w.refresh_ports = self._refresh_ports
        w.key_clicked = self._on_key_clicked
        w.encoder_ccw_clicked = self._on_encoder_ccw
        w.encoder_cw_clicked = self._on_encoder_cw
        w.encoder_btn_clicked = self._on_encoder_btn

        w.save_action = self._on_save_action
        w.test_action = self._on_test_action
        w.clear_action = self._on_clear_action
        w.insert_key = self._on_insert_key
        w.browse_display_image = self._on_browse_display_image
        w.browse_toggle_image_on = self._on_browse_toggle_image_on
        w.browse_toggle_image_off = self._on_browse_toggle_image_off

        w.action_event_changed = self._on_action_event_selected
        w.action_kind_changed = self._on_action_kind_changed
        w.action_item_selected = self._on_action_item_selected
        w.display_kind_changed = self._on_display_kind_changed

        w.load_splash_image = self._on_load_splash_image
        w.splash_save = self._on_splash_save
        w.splash_preview = self._on_splash_preview_button

        w.poll_messages = self._poll_messages
        w.watchdog_tick = lambda: None
        w.settings_changed = self._on_settings_changed

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
        events.set("on_toggle_state", lambda event_id, state: self._msg_queue.put(("toggle_state", event_id, state)))

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
        elif kind == "toggle_state":
            self._refresh_actions_list()

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
            port = self._engine.connected_port()
            self._w.port_text = port
            if self._connect_on_start:
                self._save_settings({"port": port})

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
        first = True

        while not self._tray_quit_requested:
            if first and self._start_minimized and HAS_PYSTRAY:
                self._log("Application started minimized to system tray")
                self._create_tray_icon()
                first = False
                continue

            first = False
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
        self._select_action_event(f"key:{key}:down")

    def _on_encoder_ccw(self) -> None:
        self._select_action_event("encoder:ccw")

    def _on_encoder_cw(self) -> None:
        self._select_action_event("encoder:cw")

    def _on_encoder_btn(self) -> None:
        self._select_action_event("encoder:button_down")

    def _select_action_event(self, event_id: str) -> None:
        label = EVENT_LABELS.get(event_id, event_id)
        self._w.action_event = label
        self._on_action_event_selected(label)

        self._w.selected_action_index = -1
        actions = sorted(self._engine.get_all_actions().keys())
        for i, event_item in enumerate(actions):
            if event_item == event_id:
                self._w.selected_action_index = i
                break

    def _refresh_ports(self) -> None:
        ports = self._engine.refresh_ports()
        values = [device for device, _ in ports]
        self._w.port_list = slint.ListModel(values)

        if not self._w.port_text and values:
            detected = PicoKeypadEngine.autodetect_port()
            self._w.port_text = detected or values[0]

    def _splash_status_text(self) -> str:
        config = self._engine.get_splash_config()
        if config.mode == SPLASH_MODE_STATIC:
            path = self._engine.splash_path()
            if self._splash_buffer is not None:
                return f"Splash ready: {path.name} ({len(self._splash_buffer)} bytes)"
            if path.exists():
                return f"Splash file present: {path.name}"
            return f"No splash saved at {path.name}"
        return f"Splash mode: {config.mode} (auto-refresh)"

    def _update_splash_status(self) -> None:
        self._w.splash_status = self._splash_status_text()

    def _on_load_splash_image(self) -> None:
        if not HAS_FILEDIALOG:
            self._log("File dialog not available (tkinter missing)")
            return
        path = filedialog.askopenfilename(
            filetypes=(
                ("PNG images", "*.png"),
                ("All files", "*.*"),
            )
        )
        if not path:
            return
        try:
            pil_img = PILImage.open(path)
            buffer = self._engine.pil_image_to_oled_buffer(pil_img)
            self._splash_buffer = buffer
            self._preview_buffer = buffer
            self._engine.save_splash(buffer)
            self._update_splash_status()
            self._update_oled_preview_from_buffer(buffer)
            if self._engine.is_connected():
                self._engine.send_splash_to_device()
            self._log(f"Loaded and saved splash from {path}")
        except Exception as exc:
            self._log(f"Failed to load splash image: {exc}")

    def _on_splash_save(self) -> None:
        try:
            interval = max(0.2, float(self._w.splash_interval))
        except (TypeError, ValueError):
            interval = 2.0
        try:
            idle = max(0.2, float(self._w.splash_idle))
        except (TypeError, ValueError):
            idle = 2.0
        try:
            font_size = min(24, max(8, int(float(self._w.splash_font_size))))
        except (TypeError, ValueError):
            font_size = 12
        try:
            line_spacing = min(8, max(0, int(float(self._w.splash_line_spacing))))
        except (TypeError, ValueError):
            line_spacing = 2

        mode = self._w.splash_mode
        if mode not in SPLASH_MODES:
            mode = SPLASH_MODE_STATIC

        alignment = self._w.splash_alignment if self._w.splash_alignment in ("left", "center") else "left"

        config = SplashConfig(
            mode=mode,
            interval=interval,
            idle_timeout=idle,
            template=self._w.splash_template,
            font_size=font_size,
            alignment=alignment,
            line_spacing=line_spacing,
        )
        self._engine.set_splash_config(config, persist=True)
        self._load_splash_config_widgets()
        self._update_splash_status()
        self._log(f"Saved splash config: mode={config.mode} interval={config.interval}s idle={config.idle_timeout}s")
        self._preview_splash()

    def _preview_splash(self) -> None:
        if not self._engine.is_connected():
            self._engine.send_splash_to_device()

    def _on_splash_preview_button(self) -> None:
        self._preview_splash()

    # ---- action editor ----

    def _on_action_event_selected(self, label: str) -> None:
        event_id = LABEL_EVENTS.get(label, label)
        action = self._engine.get_action(event_id)
        self._load_action_into_widgets(action)

    def _load_action_into_widgets(self, action: Action) -> None:
        w = self._w
        w.action_kind = action.kind if action.kind in ACTION_TYPES else ACTION_DISABLED
        w.action_value = action.value if action.kind in (ACTION_HOTKEY, ACTION_MACRO) else ""
        if action.kind == ACTION_VOLUME and action.value in VOLUME_VALUES:
            w.volume_value = action.value
        if action.kind == ACTION_MEDIA and action.value in MEDIA_VALUES:
            w.media_value = action.value
        w.toggle_action_on = action.action_on
        w.toggle_action_off = action.action_off
        w.toggle_image_on = action.image_on
        w.toggle_image_off = action.image_off
        w.display_kind = action.display if action.display in DISPLAY_TYPES else DISPLAY_NONE
        w.display_value = action.display_value

    def _on_action_kind_changed(self, kind: str) -> None:
        w = self._w
        if kind == ACTION_VOLUME:
            if not w.volume_value:
                w.volume_value = VOLUME_VALUES[0]
            if w.display_kind == DISPLAY_NONE:
                w.display_kind = DISPLAY_VOLUME
        elif kind == ACTION_MEDIA:
            if not w.media_value:
                w.media_value = MEDIA_VALUES[0]
            if w.display_kind == DISPLAY_NONE:
                w.display_kind = DISPLAY_MEDIA
        elif kind == ACTION_MACRO and not w.action_value:
            w.action_value = (
                "hotkey: ctrl+c\nsleep: 100\nhotkey: ctrl+v\ndisplay: COPIED | TO CLIPBOARD"
            )

    def _on_display_kind_changed(self, kind: str) -> None:
        if kind == DISPLAY_TEXT and not self._w.display_value:
            self._w.display_value = "Button {volume}%\n{mute}"

    def _on_action_item_selected(self, idx: int) -> None:
        actions = sorted(self._engine.get_all_actions().items())
        if 0 <= idx < len(actions):
            event_id = EVENT_LABELS.get(actions[idx][0], actions[idx][0])
            self._w.action_event = event_id
            self._on_action_event_selected(event_id)

    def _on_insert_key(self) -> None:
        key_name = self._w.selected_key
        if not key_name or key_name.startswith("---"):
            return

        if self._w.action_kind == ACTION_HOTKEY:
            current = self._w.action_value
            separator = "+" if current and not current.endswith(("+", " ", "\n")) else ""
            self._w.action_value = current + separator + key_name
            return

        if self._w.action_kind == ACTION_MACRO:
            self._w.action_value = self._w.action_value + key_name
            return

        self._w.action_kind = ACTION_HOTKEY
        self._w.action_value = key_name

    def _build_action_from_widgets(self) -> Action:
        w = self._w
        kind = w.action_kind if w.action_kind in ACTION_TYPES else ACTION_DISABLED

        if kind == ACTION_VOLUME:
            value = w.volume_value if w.volume_value in VOLUME_VALUES else VOLUME_VALUES[0]
        elif kind == ACTION_MEDIA:
            value = w.media_value if w.media_value in MEDIA_VALUES else MEDIA_VALUES[0]
        elif kind == ACTION_TOGGLE:
            value = ""
        else:
            value = w.action_value

        display = w.display_kind if w.display_kind in DISPLAY_TYPES else DISPLAY_NONE
        display_value = w.display_value if display in (DISPLAY_TEXT, DISPLAY_IMAGE) else ""

        return Action(
            kind=kind,
            value=value,
            display=display,
            display_value=display_value,
            action_on=w.toggle_action_on,
            action_off=w.toggle_action_off,
            image_on=w.toggle_image_on,
            image_off=w.toggle_image_off,
        )

    def _on_save_action(self) -> None:
        label = self._w.action_event
        event_id = LABEL_EVENTS.get(label, label)
        action = self._build_action_from_widgets()

        existing = self._engine.get_action(event_id)
        if existing.kind == ACTION_TOGGLE and action.kind == ACTION_TOGGLE:
            action.state = existing.state

        self._engine.set_action(event_id, action)
        self._refresh_actions_list()
        self._log(f"Saved action {label}")

    def _on_clear_action(self) -> None:
        label = self._w.action_event
        event_id = LABEL_EVENTS.get(label, label)
        self._engine.clear_action(event_id)
        self._w.action_kind = ACTION_DISABLED
        self._w.action_value = ""
        self._w.toggle_action_on = ""
        self._w.toggle_action_off = ""
        self._w.toggle_image_on = ""
        self._w.toggle_image_off = ""
        self._w.display_kind = DISPLAY_NONE
        self._w.display_value = ""
        self._refresh_actions_list()
        self._log(f"Cleared action {label}")

    def _on_test_action(self) -> None:
        action = self._build_action_from_widgets()
        if not action.enabled():
            self._log("No action to test")
            return

        self._engine.run_action(action)

        display = action.display
        if display == DISPLAY_NONE:
            display = self._engine.default_display_for_kind(action.kind)
        if display != DISPLAY_NONE:
            self._engine.apply_display_response(display, action.display_value)

    def _refresh_actions_list(self) -> None:
        items = []
        for event_id, action in sorted(self._engine.get_all_actions().items()):
            label = EVENT_LABELS.get(event_id, event_id)
            detail = self._short_value(action.value)
            if action.kind == ACTION_TOGGLE:
                detail = "ON" if action.state else "OFF"
            items.append(f"{label}  |  {action.kind}  |  {detail}")
        self._w.actions_model = slint.ListModel(items)

    # ---- image browsing ----

    def _on_browse_display_image(self) -> None:
        path = self._ask_image_path()
        if not path:
            return
        self._w.display_kind = DISPLAY_IMAGE
        self._w.display_value = path

    def _on_browse_toggle_image_on(self) -> None:
        path = self._ask_image_path()
        if path:
            self._w.toggle_image_on = path

    def _on_browse_toggle_image_off(self) -> None:
        path = self._ask_image_path()
        if path:
            self._w.toggle_image_off = path

    def _ask_image_path(self) -> str:
        if not HAS_FILEDIALOG:
            self._log("File dialog not available (tkinter missing)")
            return ""
        return filedialog.askopenfilename(
            filetypes=(
                ("Images", "*.png *.gif *.ppm *.pgm *.jpg *.jpeg *.bmp"),
                ("All files", "*.*"),
            )
        )

    # ---- OLED preview ----

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
    actions_path: str | None = None,
    start_minimized: bool | None = None,
) -> None:
    app = SlintKeypadApp(
        initial_port=port,
        actions_path=actions_path,
        start_minimized=start_minimized,
    )
    app.run()
