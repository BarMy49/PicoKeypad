import json
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# Optional tray support using pystray + Pillow. If not available, the app will
# still run but closing will fall back to withdrawing the window (no tray).
try:
    import pystray
    from PIL import Image, ImageDraw
    HAS_PYSTRAY = True
except Exception:
    HAS_PYSTRAY = False
from typing import Any

from .actions import (
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
from .bindings import BindingStore, EVENT_LABELS, EVENTS, LABEL_EVENTS, event_id_from_message
from .display_rules import (
    DISPLAY_IMAGE,
    DISPLAY_MEDIA,
    DISPLAY_NONE,
    DISPLAY_TEXT,
    DISPLAY_TYPES,
    DISPLAY_VOLUME,
    DisplayRule,
    DisplayRuleStore,
)
from . import protocol
from .serial_transport import PicoKeypadClient, SerialConnectionError
from .splash_store import DEFAULT_SPLASH_PATH, load_splash_binary, save_splash_binary
from .system_status import get_media_status, get_volume_status, media_display_lines, volume_display_lines


SCALE = 4


class KeypadApp(tk.Tk):
    def __init__(
        self,
        initial_port: str | None = None,
        bindings_path: str | None = None,
        display_rules_path: str | None = None,
    ):
        super().__init__()
        self.title("Pico Keypad")
        self.minsize(980, 620)

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
        # pystray Icon object (if tray support is available and active)
        self._tray_icon = None

        self.port_var = tk.StringVar(value=initial_port or "")
        self.status_var = tk.StringVar(value="Disconnected")
        self.encoder_position_var = tk.StringVar(value="0")
        self.encoder_delta_var = tk.StringVar(value="0")
        self.encoder_button_var = tk.StringVar(value="up")
        self.image_invert_var = tk.BooleanVar(value=False)
        self.binding_event_var = tk.StringVar()
        self.binding_kind_var = tk.StringVar(value=ACTION_TYPES[0])
        self.binding_value_var = tk.StringVar()
        self.display_event_var = tk.StringVar()
        self.display_kind_var = tk.StringVar(value=DISPLAY_TYPES[0])
        self.encoder_direction: str | None = None
        self.encoder_button_down = False
        self.encoder_direction_after_id: str | None = None
        self.splash_inactivity_after_id: str | None = None
        self.preview_buffer: bytes | None = None
        self.splash_buffer: bytes | None = load_splash_binary(DEFAULT_SPLASH_PATH)
        self.splash_status_var = tk.StringVar(value=self._splash_status_text())

        self.key_widgets: dict[int, tk.Label] = {}

        self._build_ui()
        self.refresh_ports()
        self.after(50, self._process_messages, None)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(root)
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        toolbar.columnconfigure(1, weight=1)

        ttk.Label(toolbar, text="Port").grid(row=0, column=0, padx=(0, 6))
        self.port_combo = ttk.Combobox(toolbar, textvariable=self.port_var, width=36)
        self.port_combo.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(toolbar, text="Refresh", command=self.refresh_ports).grid(row=0, column=2, padx=(0, 6))
        self.connect_button = ttk.Button(toolbar, text="Connect", command=self.connect)
        self.connect_button.grid(row=0, column=3, padx=(0, 6))
        self.disconnect_button = ttk.Button(toolbar, text="Disconnect", command=self.disconnect, state="disabled")
        self.disconnect_button.grid(row=0, column=4, padx=(0, 12))
        ttk.Label(toolbar, textvariable=self.status_var).grid(row=0, column=5, sticky="e")

        input_panel = ttk.LabelFrame(root, text="Input", padding=10)
        input_panel.grid(row=1, column=0, sticky="ns", padx=(0, 12))

        keypad = ttk.Frame(input_panel)
        keypad.grid(row=0, column=0, sticky="n")
        for row in range(3):
            for col in range(4):
                key = row * 4 + col + 1
                label = tk.Label(
                    keypad,
                    text=str(key),
                    width=5,
                    height=2,
                    relief="solid",
                    borderwidth=1,
                    bg="#f2f2f2",
                    fg="#111111",
                )
                label.grid(row=row, column=col, padx=3, pady=3)
                label.bind("<Button-1>", lambda event, key=key: self._show_key_binding(key))
                label.configure(cursor="hand2")
                self.key_widgets[key] = label

        encoder = ttk.LabelFrame(input_panel, text="Encoder", padding=8)
        encoder.grid(row=1, column=0, sticky="ew", pady=(16, 0))
        self.encoder_canvas = tk.Canvas(
            encoder,
            width=150,
            height=112,
            bg="#f7f7f7",
            highlightthickness=1,
            highlightbackground="#cccccc",
        )
        self.encoder_canvas.grid(row=0, column=0, sticky="ew")
        self.encoder_canvas.bind("<Button-1>", self._on_encoder_canvas_click)
        encoder.columnconfigure(0, weight=1)
        self._draw_encoder()

        right_frame = ttk.Frame(root)
        right_frame.grid(row=1, column=1, sticky="nsew", rowspan=2)
        right_frame.columnconfigure(0, weight=1)
        right_frame.rowconfigure(0, weight=1)

        self.right_canvas = tk.Canvas(right_frame, highlightthickness=0)
        self.right_canvas.grid(row=0, column=0, sticky="nsew")
        right_vscroll = ttk.Scrollbar(right_frame, orient="vertical", command=self.right_canvas.yview)
        right_vscroll.grid(row=0, column=1, sticky="ns")
        self.right_canvas.configure(yscrollcommand=right_vscroll.set)

        # Inner frame that will contain the Notebook
        self._right_inner = ttk.Frame(self.right_canvas)
        # Create a window inside the canvas to host the inner frame
        self.right_canvas_window = self.right_canvas.create_window((0, 0), window=self._right_inner, anchor="nw")

        # Make the notebook live inside the inner frame so its full height
        # contributes to the canvas scrollregion.
        self.right_tabs = ttk.Notebook(self._right_inner)
        self.right_tabs.grid(row=0, column=0, sticky="nsew")
        self._right_inner.columnconfigure(0, weight=1)
        self._right_inner.rowconfigure(0, weight=1)

        # Update scrollregion when the inner frame changes size
        def _on_right_inner_config(event: tk.Event) -> None:
            try:
                self.right_canvas.configure(scrollregion=self.right_canvas.bbox("all"))
            except Exception:
                pass

        self._right_inner.bind("<Configure>", _on_right_inner_config)

        # Keep the inner window width in sync with the canvas width so the
        # notebook expands horizontally instead of creating a horizontal scrollbar.
        def _on_right_canvas_config(event: tk.Event) -> None:
            try:
                self.right_canvas.itemconfig(self.right_canvas_window, width=event.width)
            except Exception:
                pass

        self.right_canvas.bind("<Configure>", _on_right_canvas_config)

        display_panel = ttk.Frame(self.right_tabs, padding=10)
        self.right_tabs.add(display_panel, text="Display")
        self.display_tab = display_panel
        self._build_display_tab(display_panel)

        bindings_panel = ttk.Frame(self.right_tabs, padding=10)
        self.right_tabs.add(bindings_panel, text="Bindings")
        self.bindings_tab = bindings_panel
        self._build_bindings_tab(bindings_panel)

        # Put the serial log under the Input panel on the left column only
        log_panel = ttk.LabelFrame(root, text="Serial Log", padding=8)
        log_panel.grid(row=2, column=0, columnspan=1, sticky="nsew", pady=(10, 0))
        log_panel.columnconfigure(0, weight=1)
        log_panel.rowconfigure(0, weight=1)
        root.rowconfigure(2, weight=1)

        self.log = tk.Text(log_panel, height=8, wrap="word", state="disabled", font=("Consolas", 9))
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_panel, orient="vertical", command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.configure(yscrollcommand=scroll.set)

    def _build_display_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        self.canvas = tk.Canvas(
            parent,
            width=protocol.DISPLAY_WIDTH * SCALE,
            height=protocol.DISPLAY_HEIGHT * SCALE,
            bg="black",
            highlightthickness=1,
            highlightbackground="#999999",
        )
        self.canvas.grid(row=0, column=0, sticky="w", pady=(0, 10))

        manual = ttk.Frame(parent)
        manual.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(manual, text="Send Text", command=self.send_text).pack(side="left", padx=(0, 6))
        ttk.Button(manual, text="Load Image", command=self.load_image).pack(side="left", padx=(0, 6))
        ttk.Button(manual, text="Clear", command=self.clear_display).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(manual, text="Invert image", variable=self.image_invert_var).pack(side="left")

        splash = ttk.LabelFrame(parent, text="Splash", padding=8)
        splash.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        splash.columnconfigure(0, weight=1)
        splash_buttons = ttk.Frame(splash)
        splash_buttons.grid(row=0, column=0, sticky="ew")
        ttk.Button(splash_buttons, text="Load saved", command=self._load_saved_splash_preview).pack(side="left", padx=(0, 6))
        ttk.Button(splash_buttons, text="Save current", command=self._save_current_splash).pack(side="left", padx=(0, 6))
        ttk.Button(splash_buttons, text="Send on connect now", command=self._send_saved_splash_to_device).pack(side="left", padx=(0, 6))
        ttk.Label(splash, textvariable=self.splash_status_var).grid(row=1, column=0, sticky="w", pady=(6, 0))

        self.text_input = tk.Text(parent, height=4, wrap="none", font=("Consolas", 10))
        self.text_input.grid(row=3, column=0, sticky="ew", pady=(0, 12))
        self.text_input.insert("1.0", "Pico Keypad\nReady")

        editor = ttk.LabelFrame(parent, text="Display Rules", padding=8)
        editor.grid(row=4, column=0, sticky="nsew")
        editor.columnconfigure(0, weight=1)
        editor.rowconfigure(5, weight=1)
        editor.rowconfigure(7, weight=1)

        event_labels = [label for _, label in EVENTS]

        ttk.Label(editor, text="Input").grid(row=0, column=0, sticky="w")
        self.display_event_combo = ttk.Combobox(
            editor,
            textvariable=self.display_event_var,
            values=event_labels,
            state="readonly",
        )
        self.display_event_combo.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        self.display_event_combo.bind("<<ComboboxSelected>>", self._on_display_event_selected)

        ttk.Label(editor, text="OLED content").grid(row=2, column=0, sticky="w")
        self.display_kind_combo = ttk.Combobox(
            editor,
            textvariable=self.display_kind_var,
            values=DISPLAY_TYPES,
            state="readonly",
        )
        self.display_kind_combo.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        self.display_kind_combo.bind("<<ComboboxSelected>>", self._on_display_kind_changed)

        ttk.Label(editor, text="Text or image path").grid(row=4, column=0, sticky="w")
        self.display_value_text = tk.Text(editor, height=5, wrap="word", font=("Consolas", 10))
        self.display_value_text.grid(row=5, column=0, sticky="nsew", pady=(2, 8))

        buttons = ttk.Frame(editor)
        buttons.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(buttons, text="Save", command=self._save_display_rule).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Test", command=self._test_display_rule).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Image File", command=self._browse_display_image).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Clear", command=self._clear_display_rule).pack(side="left")

        self.display_rules_tree = ttk.Treeview(
            editor,
            columns=("event", "kind", "value"),
            show="headings",
            height=6,
        )
        self.display_rules_tree.grid(row=7, column=0, sticky="nsew")
        self.display_rules_tree.heading("event", text="Input")
        self.display_rules_tree.heading("kind", text="OLED content")
        self.display_rules_tree.heading("value", text="Value")
        self.display_rules_tree.column("event", width=180, anchor="w")
        self.display_rules_tree.column("kind", width=110, anchor="w")
        self.display_rules_tree.column("value", width=320, anchor="w")
        self.display_rules_tree.bind("<<TreeviewSelect>>", self._on_display_tree_selected)

        self.display_event_var.set(event_labels[0])
        self._on_display_event_selected()
        self._refresh_display_rules_tree()
        self._update_splash_status()
        if self.splash_buffer is not None:
            self._draw_buffer_preview(self.splash_buffer)

    def _build_bindings_tab(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=3)
        parent.columnconfigure(1, weight=1)
        parent.rowconfigure(5, weight=1)

        event_labels = [label for _, label in EVENTS]

        ttk.Label(parent, text="Input").grid(row=0, column=0, sticky="w")
        self.binding_event_combo = ttk.Combobox(
            parent,
            textvariable=self.binding_event_var,
            values=event_labels,
            state="readonly",
        )
        self.binding_event_combo.grid(row=1, column=0, sticky="ew", pady=(2, 8))
        self.binding_event_combo.bind("<<ComboboxSelected>>", self._on_binding_event_selected)

        ttk.Label(parent, text="Action").grid(row=2, column=0, sticky="w")
        self.binding_kind_combo = ttk.Combobox(
            parent,
            textvariable=self.binding_kind_var,
            values=ACTION_TYPES,
            state="readonly",
        )
        self.binding_kind_combo.grid(row=3, column=0, sticky="ew", pady=(2, 8))
        self.binding_kind_combo.bind("<<ComboboxSelected>>", self._on_binding_kind_changed)

        ttk.Label(parent, text="Value / macro").grid(row=4, column=0, sticky="w")
        self.binding_value_text = tk.Text(parent, height=7, wrap="word", font=("Consolas", 10))
        self.binding_value_text.grid(row=5, column=0, sticky="nsew", pady=(2, 8))

        key_panel = ttk.Frame(parent)
        key_panel.grid(row=0, column=1, rowspan=8, sticky="nsew", padx=(12, 0))
        key_panel.columnconfigure(0, weight=1)
        key_panel.rowconfigure(1, weight=1)
        ttk.Label(key_panel, text="Available keys").grid(row=0, column=0, sticky="w")

        key_list_frame = ttk.Frame(key_panel)
        key_list_frame.grid(row=1, column=0, sticky="nsew", pady=(2, 6))
        key_list_frame.columnconfigure(0, weight=1)
        key_list_frame.rowconfigure(0, weight=1)
        self.key_listbox = tk.Listbox(key_list_frame, height=16, exportselection=False)
        self.key_listbox.grid(row=0, column=0, sticky="nsew")
        key_scroll = ttk.Scrollbar(key_list_frame, orient="vertical", command=self.key_listbox.yview)
        key_scroll.grid(row=0, column=1, sticky="ns")
        self.key_listbox.configure(yscrollcommand=key_scroll.set)
        self.key_listbox.bind("<Double-Button-1>", self._on_key_list_double_click)
        self._populate_key_list()

        ttk.Button(key_panel, text="Insert", command=self._insert_selected_key).grid(
            row=2,
            column=0,
            sticky="ew",
        )

        buttons = ttk.Frame(parent)
        buttons.grid(row=6, column=0, sticky="ew", pady=(0, 10))
        ttk.Button(buttons, text="Save", command=self._save_binding).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Test", command=self._test_binding).pack(side="left", padx=(0, 6))
        ttk.Button(buttons, text="Clear", command=self._clear_binding).pack(side="left", padx=(0, 12))
        ttk.Label(buttons, text="Functions: " + ", ".join(FUNCTIONS)).pack(side="left")

        self.bindings_tree = ttk.Treeview(
            parent,
            columns=("event", "kind", "value"),
            show="headings",
            height=7,
        )
        self.bindings_tree.grid(row=7, column=0, sticky="nsew")
        self.bindings_tree.heading("event", text="Input")
        self.bindings_tree.heading("kind", text="Action")
        self.bindings_tree.heading("value", text="Value")
        self.bindings_tree.column("event", width=180, anchor="w")
        self.bindings_tree.column("kind", width=90, anchor="w")
        self.bindings_tree.column("value", width=260, anchor="w")
        self.bindings_tree.bind("<<TreeviewSelect>>", self._on_binding_tree_selected)

        self.binding_event_var.set(event_labels[0])
        self._on_binding_event_selected()
        self._refresh_bindings_tree()

    def _populate_key_list(self) -> None:
        self.key_listbox.delete(0, "end")

        for group_name, keys in KEY_GROUPS:
            self.key_listbox.insert("end", "[" + group_name + "]")
            header_index = self.key_listbox.size() - 1
            self.key_listbox.itemconfig(header_index, foreground="#666666")
            for key in keys:
                self.key_listbox.insert("end", key)

    def _draw_encoder(self) -> None:
        self.encoder_canvas.delete("all")

        active = "#1f9d55"
        inactive = "#d8d8d8"
        outline = "#555555"
        text = "#111111"

        ccw_fill = active if self.encoder_direction == "ccw" else inactive
        cw_fill = active if self.encoder_direction == "cw" else inactive
        button_fill = active if self.encoder_button_down else "#f2f2f2"
        button_text = "white" if self.encoder_button_down else text

        self.encoder_canvas.create_polygon(
            18, 56,
            42, 38,
            42, 74,
            fill=ccw_fill,
            outline=outline,
        )
        self.encoder_canvas.create_text(31, 88, text="CCW", fill=text, font=("Segoe UI", 9))

        self.encoder_canvas.create_polygon(
            132, 56,
            108, 38,
            108, 74,
            fill=cw_fill,
            outline=outline,
        )
        self.encoder_canvas.create_text(119, 88, text="CW", fill=text, font=("Segoe UI", 9))

        self.encoder_canvas.create_oval(48, 16, 102, 70, fill="#eeeeee", outline=outline, width=2)
        self.encoder_canvas.create_oval(62, 30, 88, 56, fill=button_fill, outline=outline, width=2)
        self.encoder_canvas.create_text(75, 43, text="BTN", fill=button_text, font=("Segoe UI", 9, "bold"))
        self.encoder_canvas.create_line(75, 20, 75, 30, fill=outline, width=2)

    def _on_encoder_canvas_click(self, event: tk.Event) -> None:
        if event.x < 50:
            self._select_binding_event("encoder:ccw")
        elif event.x > 100:
            self._select_binding_event("encoder:cw")
        else:
            self._select_binding_event("encoder:button_down")

    def _show_key_binding(self, key: int) -> None:
        self._select_binding_event("key:{}:down".format(key))

    def _select_binding_event(self, event_id: str) -> None:
        label = EVENT_LABELS.get(event_id, event_id)
        self.binding_event_var.set(label)
        self._on_binding_event_selected()
        self.right_tabs.select(self.bindings_tab)

        if self.bindings_tree.exists(event_id):
            self.bindings_tree.selection_set(event_id)
            self.bindings_tree.see(event_id)
        else:
            self.bindings_tree.selection_remove(self.bindings_tree.selection())

    def refresh_ports(self) -> None:
        try:
            ports = PicoKeypadClient.list_ports()
        except SerialConnectionError as exc:
            self._log(str(exc))
            self.port_combo["values"] = []
            return

        values = [port.device for port in ports]
        labels = {port.device: port.label for port in ports}
        self.port_combo["values"] = values

        if not self.port_var.get() and values:
            detected = PicoKeypadClient.autodetect_port()
            self.port_var.set(detected or values[0])

        for port in values:
            self._log(labels[port])

    def connect(self) -> None:
        try:
            self.client.open(self.port_var.get().strip() or None)
        except Exception as exc:
            messagebox.showerror("Connection failed", str(exc))
            self._log(f"Connection failed: {exc}")
            return

        self.port_var.set(self.client.port or "")
        self.stop_reader.clear()
        self.reader_thread = threading.Thread(target=self._reader_loop, daemon=True)
        self.reader_thread.start()
        self._set_connected(True)
        self._log(f"Connected to {self.client.port}")
        self._arm_splash_inactivity_timer()

        try:
            self.client.ping()
        except Exception as exc:
            self._log(f"Ping failed: {exc}")

    def disconnect(self) -> None:
        # Try to notify the device that we're disconnecting so the firmware can
        # show the ready splash immediately.
        try:
            if self.client.is_open:
                self.client.send_disconnect()
        except Exception:
            # Best-effort: ignore errors while sending disconnect
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

    def _process_messages(self, *_args: object) -> None:
        while True:
            try:
                message = self.messages.get_nowait()
            except queue.Empty:
                break

            self._handle_message(message)

        self.after(50, self._process_messages, None)

    def _handle_message(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")

        if message_type == "hello":
            display = message.get("display", {})
            ready = "ready" if display.get("ready") else "not ready"
            self.status_var.set(f"Connected, display {ready}")
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
        elif message_type == "connection_error":
            self._log(f"Connection error: {message.get('message')}")
            self.disconnect()
        elif message_type not in ("empty",):
            self._log(message.get("message") or json.dumps(message))

    def _handle_key(self, message: dict[str, Any]) -> None:
        key = int(message.get("key", 0))
        widget = self.key_widgets.get(key)
        if widget is None:
            return

        if message.get("event") == "down":
            widget.configure(bg="#1f9d55", fg="white")
        else:
            widget.configure(bg="#f2f2f2", fg="#111111")

        self._reset_splash_inactivity_timer()
        self._log(f"KEY {message.get('event')} {key}")

    def _handle_encoder(self, message: dict[str, Any]) -> None:
        event = message.get("event")
        if event == "turn":
            delta = int(message.get("delta", 0))
            self.encoder_direction = "cw" if delta > 0 else "ccw"
            self._draw_encoder()
            self._schedule_encoder_direction_clear()
            self._reset_splash_inactivity_timer()
            self._log(f"ENC {message.get('delta')} pos={message.get('position')}")
        elif event in ("button_down", "button_up"):
            self.encoder_button_down = bool(message.get("pressed"))
            self._draw_encoder()
            self._reset_splash_inactivity_timer()
            self._log(f"ENC {event}")

    def _schedule_encoder_direction_clear(self) -> None:
        if self.encoder_direction_after_id is not None:
            self.after_cancel(self.encoder_direction_after_id)

        self.encoder_direction_after_id = self.after(350, self._clear_encoder_direction, None)

    def _clear_encoder_direction(self, *_args: object) -> None:
        self.encoder_direction_after_id = None
        self.encoder_direction = None
        self._draw_encoder()

    def _arm_splash_inactivity_timer(self) -> None:
        self._cancel_splash_inactivity_timer()
        self.splash_inactivity_after_id = self.after(2000, self._send_saved_splash_if_present, None)

    def _reset_splash_inactivity_timer(self) -> None:
        if self.client.is_open:
            self._arm_splash_inactivity_timer()

    def _cancel_splash_inactivity_timer(self) -> None:
        if self.splash_inactivity_after_id is not None:
            try:
                self.after_cancel(self.splash_inactivity_after_id)
            except Exception:
                pass
            self.splash_inactivity_after_id = None

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

    def _apply_display_rule(self, rule: DisplayRule, *_args: object) -> None:
        if rule.kind == DISPLAY_TEXT:
            self._send_display_lines(display_lines_from_value(rule.value), warn_if_disconnected=False)
        elif rule.kind == DISPLAY_IMAGE:
            self._send_display_image_path(rule.value, warn_if_disconnected=False)
        elif rule.kind == DISPLAY_VOLUME:
            self._send_system_status_display(DISPLAY_VOLUME)
        elif rule.kind == DISPLAY_MEDIA:
            self._send_system_status_display(DISPLAY_MEDIA)

    def _send_system_status_display(self, kind: str, *_args: object) -> None:
        def worker() -> None:
            if kind == DISPLAY_VOLUME:
                lines = volume_display_lines(get_volume_status())
            else:
                lines = media_display_lines(get_media_status())

            self.messages.put({"type": "display_action", "lines": lines})

        threading.Thread(target=worker, daemon=True).start()

    def send_text(self) -> None:
        lines = self.text_input.get("1.0", "end-1c").splitlines() or [""]
        self._send_display_lines(lines, warn_if_disconnected=True)

    def clear_display(self) -> None:
        if not self._can_send():
            return

        try:
            self.client.send_clear()
            self.canvas.delete("all")
        except Exception as exc:
            messagebox.showerror("Clear failed", str(exc))
            self._log(f"Clear failed: {exc}")

    def load_image(self) -> None:
        if not self._can_send():
            return

        path = filedialog.askopenfilename(
            filetypes=(
                ("Tk images", "*.png *.gif *.ppm *.pgm"),
                ("All files", "*.*"),
            )
        )
        if not path:
            return

        try:
            photo = tk.PhotoImage(file=path)
            buffer = self._photo_to_oled_buffer(photo)
            self.preview_buffer = buffer
            self.client.send_image(buffer)
            self._draw_buffer_preview(buffer)
            self._log(f"Image sent: {path}")
        except Exception as exc:
            messagebox.showerror("Image failed", str(exc))
            self._log(f"Image failed: {exc}")

    def _splash_status_text(self) -> str:
        if self.splash_buffer is not None:
            return f"Splash ready: {DEFAULT_SPLASH_PATH.name} ({len(self.splash_buffer)} bytes)"
        if DEFAULT_SPLASH_PATH.exists():
            return f"Splash file present: {DEFAULT_SPLASH_PATH.name}"
        return f"No splash saved at {DEFAULT_SPLASH_PATH.name}"

    def _update_splash_status(self) -> None:
        self.splash_status_var.set(self._splash_status_text())

    def _load_saved_splash_preview(self) -> None:
        buffer = load_splash_binary(DEFAULT_SPLASH_PATH)
        if buffer is None:
            self.splash_buffer = None
            self._update_splash_status()
            self._log(f"No valid splash file at {DEFAULT_SPLASH_PATH}")
            messagebox.showinfo("Splash", f"No valid splash file found at {DEFAULT_SPLASH_PATH}")
            return

        self.splash_buffer = buffer
        self.preview_buffer = buffer
        self._update_splash_status()
        self._draw_buffer_preview(buffer)
        self._log(f"Loaded splash preview from {DEFAULT_SPLASH_PATH}")

    def _save_current_splash(self) -> None:
        buffer = self.preview_buffer or self.splash_buffer
        if buffer is None:
            messagebox.showwarning("Splash", "Load an image first")
            return

        try:
            save_splash_binary(DEFAULT_SPLASH_PATH, buffer)
            self.splash_buffer = buffer
            self._update_splash_status()
            self._log(f"Saved splash to {DEFAULT_SPLASH_PATH}")
        except Exception as exc:
            messagebox.showerror("Splash save failed", str(exc))
            self._log(f"Splash save failed: {exc}")

    def _send_saved_splash_if_present(self, *_args: object) -> bool:
        self.splash_inactivity_after_id = None
        buffer = load_splash_binary(DEFAULT_SPLASH_PATH)
        if buffer is None:
            self._update_splash_status()
            self._log("No splash to send")
            return False

        try:
            self.client.send_image(buffer)
            self.splash_buffer = buffer
            self._update_splash_status()
            self._draw_buffer_preview(buffer)
            self._log(f"Sent splash from {DEFAULT_SPLASH_PATH} on connect")
            return True
        except Exception as exc:
            self._log(f"Splash send failed: {exc}")
            return False

    def _send_saved_splash_to_device(self) -> None:
        if not self._can_send():
            return
        if not self._send_saved_splash_if_present():
            messagebox.showinfo("Splash", f"No valid splash file found at {DEFAULT_SPLASH_PATH}")

    def _send_display_lines(self, lines: list[str], warn_if_disconnected: bool) -> None:
        lines = self._render_display_lines(lines)
        if not lines:
            lines = [""]

        if not self.client.is_open:
            self._draw_text_preview(lines)
            self._log("Display skipped: serial port is not connected")
            if warn_if_disconnected:
                messagebox.showwarning("Not connected", "Connect to the Pico first")
            # Arm inactivity timer so splash will appear after the configured delay
            self._arm_splash_inactivity_timer()
            return

        try:
            self.client.send_text(lines)
            self._draw_text_preview(lines)
            # Arm inactivity timer after updating the display so splash will be
            # shown after the configured idle timeout.
            self._arm_splash_inactivity_timer()
        except Exception as exc:
            if warn_if_disconnected:
                messagebox.showerror("Send failed", str(exc))
            self._log(f"Send display failed: {exc}")

    def _send_display_image_path(self, path: str, warn_if_disconnected: bool) -> None:
        path = path.strip()
        if not path:
            self._log("Display image skipped: image path is empty")
            return

        try:
            photo = tk.PhotoImage(file=path)
            buffer = self._photo_to_oled_buffer(photo)
        except Exception as exc:
            if warn_if_disconnected:
                messagebox.showerror("Image failed", str(exc))
            self._log(f"Display image failed: {exc}")
            return

        if not self.client.is_open:
            self._draw_buffer_preview(buffer)
            self._log("Display image skipped: serial port is not connected")
            if warn_if_disconnected:
                messagebox.showwarning("Not connected", "Connect to the Pico first")
            # Arm inactivity timer so splash will appear after the configured delay
            self._arm_splash_inactivity_timer()
            return

        try:
            self.client.send_image(buffer)
            self._draw_buffer_preview(buffer)
            # Arm inactivity timer after updating the display so splash will be
            # shown after the configured idle timeout.
            self._arm_splash_inactivity_timer()
        except Exception as exc:
            if warn_if_disconnected:
                messagebox.showerror("Image failed", str(exc))
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
                    "volume_up",
                    "volume_down",
                    "volume_mute",
                ):
                    return value

        return None

    def _volume_display_lines(self) -> list[str]:
        return volume_display_lines(get_volume_status())

    def _photo_to_oled_buffer(self, photo: tk.PhotoImage) -> bytes:
        src_width = photo.width()
        src_height = photo.height()
        if src_width <= 0 or src_height <= 0:
            raise ValueError("Image is empty")

        buffer = bytearray(protocol.DISPLAY_BUFFER_SIZE)
        invert = self.image_invert_var.get()

        for y in range(protocol.DISPLAY_HEIGHT):
            source_y = (y * src_height) // protocol.DISPLAY_HEIGHT
            for x in range(protocol.DISPLAY_WIDTH):
                source_x = (x * src_width) // protocol.DISPLAY_WIDTH
                luminance = self._pixel_luminance(photo.get(source_x, source_y))
                lit = luminance >= 128 if invert else luminance < 128
                if lit:
                    buffer[x + (y // 8) * protocol.DISPLAY_WIDTH] |= 1 << (y & 7)

        return bytes(buffer)

    def _pixel_luminance(self, value: Any) -> int:
        if isinstance(value, tuple):
            red, green, blue = value[:3]
        elif isinstance(value, str) and value.startswith("#") and len(value) >= 7:
            red = int(value[1:3], 16)
            green = int(value[3:5], 16)
            blue = int(value[5:7], 16)
        else:
            red16, green16, blue16 = self.winfo_rgb(str(value))
            red = red16 // 257
            green = green16 // 257
            blue = blue16 // 257

        return (red * 299 + green * 587 + blue * 114) // 1000

    def _draw_text_preview(self, lines: list[str]) -> None:
        self.canvas.delete("all")
        for index, line in enumerate(lines[:4]):
            self.canvas.create_text(
                2,
                index * 8 * SCALE + 2,
                anchor="nw",
                text=line[:16],
                fill="white",
                font=("Consolas", 18),
            )

    def _draw_buffer_preview(self, buffer: bytes) -> None:
        self.canvas.delete("all")
        for y in range(protocol.DISPLAY_HEIGHT):
            for x in range(protocol.DISPLAY_WIDTH):
                value = buffer[x + (y // 8) * protocol.DISPLAY_WIDTH] & (1 << (y & 7))
                if value:
                    self.canvas.create_rectangle(
                        x * SCALE,
                        y * SCALE,
                        (x + 1) * SCALE,
                        (y + 1) * SCALE,
                        fill="white",
                        outline="white",
                    )

    def _can_send(self) -> bool:
        if self.client.is_open:
            return True
        messagebox.showwarning("Not connected", "Connect to the Pico first")
        return False

    def _on_display_event_selected(self, event: object | None = None) -> None:
        event_id = self._selected_display_event_id()
        rule = self.display_store.get(event_id)
        self.display_kind_var.set(rule.kind)
        self._set_display_value(rule.value)
        self._on_display_kind_changed()

    def _on_display_kind_changed(self, event: object | None = None) -> None:
        kind = self.display_kind_var.get()
        current = self._get_display_value()

        if kind == DISPLAY_TEXT and not current:
            self._set_display_value("Button {volume}%\n{mute}")
        elif kind == DISPLAY_VOLUME:
            self._set_display_value("System master volume")
        elif kind == DISPLAY_MEDIA:
            self._set_display_value("Current media session")
        elif kind == DISPLAY_IMAGE and current in (
            "System master volume",
            "Current media session",
        ):
            self._set_display_value("")

    def _on_display_tree_selected(self, event: object | None = None) -> None:
        selection = self.display_rules_tree.selection()
        if not selection:
            return

        event_id = selection[0]
        self.display_event_var.set(EVENT_LABELS.get(event_id, event_id))
        self._on_display_event_selected()

    def _selected_display_event_id(self) -> str:
        label = self.display_event_var.get()
        return LABEL_EVENTS.get(label, label)

    def _current_display_rule(self) -> DisplayRule:
        value = self._get_display_value()
        kind = self.display_kind_var.get()

        if kind in (DISPLAY_VOLUME, DISPLAY_MEDIA):
            value = ""

        return DisplayRule(kind=kind, value=value)

    def _save_display_rule(self) -> None:
        event_id = self._selected_display_event_id()
        rule = self._current_display_rule()
        self.display_store.set(event_id, rule)
        self._refresh_display_rules_tree()
        self._log(f"Saved display rule {EVENT_LABELS.get(event_id, event_id)}")

    def _clear_display_rule(self) -> None:
        event_id = self._selected_display_event_id()
        self.display_store.clear(event_id)
        self.display_kind_var.set(DISPLAY_NONE)
        self._set_display_value("")
        self._refresh_display_rules_tree()
        self._log(f"Cleared display rule {EVENT_LABELS.get(event_id, event_id)}")

    def _test_display_rule(self) -> None:
        rule = self._current_display_rule()
        if not rule.enabled():
            self._log("No display rule to test")
            return

        self._apply_display_rule(rule)

    def _browse_display_image(self) -> None:
        path = filedialog.askopenfilename(
            filetypes=(
                ("Tk images", "*.png *.gif *.ppm *.pgm"),
                ("All files", "*.*"),
            )
        )
        if not path:
            return

        self.display_kind_var.set(DISPLAY_IMAGE)
        self._set_display_value(path)

    def _refresh_display_rules_tree(self) -> None:
        for item in self.display_rules_tree.get_children():
            self.display_rules_tree.delete(item)

        for event_id, rule in sorted(self.display_store.rules.items()):
            self.display_rules_tree.insert(
                "",
                "end",
                iid=event_id,
                values=(
                    EVENT_LABELS.get(event_id, event_id),
                    rule.kind,
                    self._short_value(rule.value),
                ),
            )

    def _set_display_value(self, value: str) -> None:
        self.display_value_text.delete("1.0", "end")
        self.display_value_text.insert("1.0", value)

    def _get_display_value(self) -> str:
        return self.display_value_text.get("1.0", "end-1c").strip()

    def _on_binding_event_selected(self, event: object | None = None) -> None:
        event_id = self._selected_event_id()
        action = self.binding_store.get(event_id)
        self.binding_kind_var.set(action.kind)
        self._set_binding_value(action.value)
        self._on_binding_kind_changed()

    def _on_binding_kind_changed(self, event: object | None = None) -> None:
        kind = self.binding_kind_var.get()
        current = self._get_binding_value()

        if kind == ACTION_FUNCTION and current not in FUNCTIONS:
            self._set_binding_value("volume_up")
        elif kind == ACTION_MACRO and not current:
            self._set_binding_value(
                "hotkey: ctrl+c\n"
                "sleep: 100\n"
                "hotkey: ctrl+v\n"
                "display: COPIED | TO CLIPBOARD"
            )
        elif kind == ACTION_DISPLAY_TEXT and not current:
            self._set_binding_value("Button action\n{volume}% {mute}")

    def _on_key_list_double_click(self, event: object | None = None) -> None:
        self._insert_selected_key()

    def _insert_selected_key(self) -> None:
        selection = self.key_listbox.curselection()
        if not selection:
            return

        key_name = self.key_listbox.get(selection[0])
        if key_name.startswith("[") and key_name.endswith("]"):
            return

        kind = self.binding_kind_var.get()

        if kind == ACTION_FUNCTION:
            self._set_binding_value(key_name)
            return

        if kind == ACTION_HOTKEY:
            current = self._get_binding_value()
            separator = "+" if current and not current.endswith(("+", " ", "\n")) else ""
            self._set_binding_value(current + separator + key_name)
            return

        if kind == ACTION_MACRO:
            self.binding_value_text.insert("insert", key_name)
            self.binding_value_text.focus_set()

    def _on_binding_tree_selected(self, event: object | None = None) -> None:
        selection = self.bindings_tree.selection()
        if not selection:
            return

        event_id = selection[0]
        label = EVENT_LABELS.get(event_id, event_id)
        self.binding_event_var.set(label)
        self._on_binding_event_selected()

    def _selected_event_id(self) -> str:
        label = self.binding_event_var.get()
        return LABEL_EVENTS.get(label, label)

    def _current_binding_action(self) -> Action:
        return Action(
            kind=self.binding_kind_var.get(),
            value=self._get_binding_value(),
        )

    def _save_binding(self) -> None:
        event_id = self._selected_event_id()
        action = self._current_binding_action()
        self.binding_store.set(event_id, action)
        self._refresh_bindings_tree()
        self._log(f"Saved binding {EVENT_LABELS.get(event_id, event_id)}")

    def _clear_binding(self) -> None:
        event_id = self._selected_event_id()
        self.binding_store.clear(event_id)
        self.binding_kind_var.set(ACTION_TYPES[0])
        self._set_binding_value("")
        self._refresh_bindings_tree()
        self._log(f"Cleared binding {EVENT_LABELS.get(event_id, event_id)}")

    def _test_binding(self) -> None:
        action = self._current_binding_action()
        if not action.enabled():
            self._log("No action to test")
            return
        self.action_runner.run(action)

    def _refresh_bindings_tree(self) -> None:
        for item in self.bindings_tree.get_children():
            self.bindings_tree.delete(item)

        for event_id, action in sorted(self.binding_store.bindings.items()):
            self.bindings_tree.insert(
                "",
                "end",
                iid=event_id,
                values=(
                    EVENT_LABELS.get(event_id, event_id),
                    action.kind,
                    self._short_value(action.value),
                ),
            )

    def _set_binding_value(self, value: str) -> None:
        self.binding_value_text.delete("1.0", "end")
        self.binding_value_text.insert("1.0", value)

    def _get_binding_value(self) -> str:
        return self.binding_value_text.get("1.0", "end-1c").strip()

    def _short_value(self, value: str, limit: int = 56) -> str:
        value = " ".join(value.splitlines())
        if len(value) <= limit:
            return value
        return value[:limit - 3] + "..."

    def _set_connected(self, connected: bool) -> None:
        self.connect_button.configure(state="disabled" if connected else "normal")
        self.disconnect_button.configure(state="normal" if connected else "disabled")
        self.status_var.set(f"Connected to {self.client.port}" if connected else "Disconnected")

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        # Hide to system tray on window close if pystray is available.
        if HAS_PYSTRAY:
            # If already hidden, do nothing
            if self._tray_icon is not None:
                return

            # Withdraw the window so it disappears from the taskbar
            try:
                self.withdraw()
            except Exception:
                pass

            # Create and run the tray icon in a background thread
            try:
                self._create_tray_icon()
                self._log("Application hidden to system tray")
            except Exception as exc:
                # If tray creation failed, fallback to normal shutdown
                self._log(f"Tray icon failed: {exc}")
                if self.client.is_open:
                    self.disconnect()
                else:
                    self._cancel_splash_inactivity_timer()
                self.destroy()
        else:
            # No tray support: withdraw the window and inform the user how to
            # restore or install tray support. This avoids unexpectedly
            # destroying the app when user expects it to hide.
            try:
                self.withdraw()
            except Exception:
                pass
            messagebox.showinfo(
                "Hidden",
                "Application hidden. To enable system tray behavior install 'pystray' and 'Pillow' and restart the app.\n\nTo quit completely run the app again and choose Quit from the menu (if available) or press Ctrl+C in the terminal.",
            )

    def _create_tray_icon(self) -> None:
        """Create and start a pystray icon in a background thread.

        The icon menu contains 'Show' and 'Quit'. Callbacks schedule GUI
        actions on the tkinter main thread via `after`.
        """
        if not HAS_PYSTRAY:
            raise RuntimeError("pystray not available")

        # Create a simple image for the tray icon.
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Simple filled circle with border
        draw.ellipse((4, 4, 60, 60), fill=(31, 157, 85, 255), outline=(0, 0, 0, 255))

        def _on_show(icon, item):
            # Schedule showing the window on the main thread
            try:
                self.after(0, self._show_from_tray)
            except Exception:
                pass

        def _on_quit(icon, item):
            try:
                self.after(0, self._quit_from_tray)
            except Exception:
                pass

        menu = pystray.Menu(pystray.MenuItem("Show", _on_show), pystray.MenuItem("Quit", _on_quit))
        icon = pystray.Icon("pico_keypad", img, "Pico Keypad", menu)
        self._tray_icon = icon

        def _run_icon() -> None:
            try:
                icon.run()
            except Exception:
                # Ensure we don't keep broken icon reference
                try:
                    self._tray_icon = None
                except Exception:
                    pass

        thread = threading.Thread(target=_run_icon, daemon=True)
        thread.start()

    def _show_from_tray(self) -> None:
        # Stop and remove the tray icon, then deiconify the window
        try:
            if self._tray_icon is not None:
                try:
                    self._tray_icon.stop()
                except Exception:
                    pass
                self._tray_icon = None
        finally:
            try:
                self.deiconify()
                self.lift()
                self.focus_force()
            except Exception:
                pass

    def _quit_from_tray(self) -> None:
        # Cleanup and quit the application
        try:
            if self.client.is_open:
                self.disconnect()
            else:
                self._cancel_splash_inactivity_timer()
        finally:
            try:
                if self._tray_icon is not None:
                    try:
                        self._tray_icon.stop()
                    except Exception:
                        pass
                    self._tray_icon = None
            finally:
                try:
                    self.destroy()
                except Exception:
                    pass


def run(
    port: str | None = None,
    bindings_path: str | None = None,
    display_rules_path: str | None = None,
) -> None:
    app = KeypadApp(
        initial_port=port,
        bindings_path=bindings_path,
        display_rules_path=display_rules_path,
    )
    app.mainloop()
