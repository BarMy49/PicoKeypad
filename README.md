# PicoKeypad
### or KeyCo as i've called it
A customizable USB keypad built on the Raspberry Pi Pico with a 128x32 SSD1306 OLED display, a 4x3 key matrix, and a rotary encoder knob. Connect it to your PC and use it to control media, system volume, launch macros, type text, or run hotkeys -- all configurable through a desktop GUI.

## Features

### Desktop Application
- **Slint-based GUI** with dark theme and live OLED preview
- **Unified action system** -- every key and encoder event maps to a single action with two parts:
  - **Response** -- what happens on the PC:
    - **Hotkeys** -- multi-key combos (`ctrl+shift+a`)
    - **Macros** -- multi-step sequences with delays, display commands, and combos
    - **Volume** -- `volume_up` / `volume_down` / `volume_mute`
    - **Media** -- `media_play_pause`, `media_next`, `media_previous`, `media_stop`
    - **Toggle** -- flips between ON and OFF, each with its own action and image; state persists in the config JSON
  - **Display response** -- what appears on the OLED:
    - **Custom text** -- with `{volume}` and `{mute}` placeholders
    - **Images** -- any PNG/JPEG (converted to 128x32 monochrome)
    - **Volume bar** -- current system volume level (auto-selected for volume actions)
    - **Media info** -- currently playing track (auto-selected for media actions, Windows only)
- **Dynamic splash screen** -- instead of a static image, the idle splash can show:
  - **Static** -- the classic uploaded image
  - **Clock** -- large bold time with date and weekday footer
  - **Text** -- time/date lines with configurable font size, spacing and alignment
  - **Custom template** -- free-form text with `{time}`, `{time_short}`, `{date}`, `{date_short}`, `{weekday}` placeholders
- **Settings tab** -- connect on start, start minimized, splash mode / interval / idle timeout / template / font settings
- **System tray** -- minimize to tray with show/quit menu (requires `pystray`)

### Firmware
- **4x3 key matrix** scanning with configurable debounce (30ms)
- **Rotary encoder** with quadrature decoding and button debounce
- **OLED display** -- hardware & SoftI2C fallback, differential partial updates, I2C bus recovery
- **Secret mode** -- hold key 1 for 700ms to launch a game menu with two built-in games:
  - *Horse Run* -- side-scrolling runner (jump over trees, duck under birds)
  - *Cube Escape* -- teleport between lanes to dodge obstacles

## What You Need

- Raspberry Pi Pico (or RP2040 clone)
- 128x32 SSD1306 OLED I2C display (address 0x3C)
- 4x3 keypad matrix
- Rotary encoder with push button

## Pin Configuration

| Component | Pico GPIO |
|-----------|-----------|
| **OLED SDA** | GP2 |
| **OLED SCL** | GP3 |
| **Matrix rows** | GP18, GP17, GP16 |
| **Matrix columns** | GP22, GP21, GP20, GP19 |
| **Encoder A** | GP26 |
| **Encoder B** | GP27 |
| **Encoder button** | GP28 |

I2C runs at 400kHz on bus 1. All pin assignments are configurable in `firmware/config.py`.

## Getting Started

### 1. Install desktop dependencies

```bash
pip install -r app/requirements.txt
```

### 2. Flash firmware to your Pico

You need to install micropython onto your pico and then copy all the files from `firmware/` onto it.

### 3. Run the desktop app

```bash
python app/main.py
```

Optional CLI arguments:
- `--port COM5` -- connect to a specific serial port
- `--actions path/to/actions.json` -- custom actions file

### 4. Connect

In the GUI, select your Pico's serial port from the dropdown and click **Connect**. The OLED will show "PICO KEYPAD" / "USB READY" until a connection is established.

## Building a Standalone EXE

```bash
pip install pyinstaller
pyinstaller PicoKeypad.spec
```

Output lands in `dist\PicoKeypad\`. Run `PicoKeypad.exe` from anywhere -- no Python needed.

The spec bundles the Slint UI and icons. Tkinter is excluded to keep the build lean. For an alternative build that includes an icon and `slint` hidden import, use the root-level `PicoKeypad.spec`.

## Configuration Files

### `actions.json`

Maps hardware events to unified actions. Each action has a **response** (`kind` + `value`) and a **display response** (`display` + `display_value`). Toggles additionally store their state and per-state action/image:

```json
{
  "version": 1,
  "actions": {
    "encoder:cw": {
      "kind": "volume",
      "value": "volume_up",
      "display": "volume_bar",
      "display_value": ""
    },
    "key:3:down": {
      "kind": "media",
      "value": "media_play_pause",
      "display": "media_info",
      "display_value": ""
    },
    "key:1:down": {
      "kind": "toggle",
      "value": "",
      "display": "none",
      "display_value": "",
      "state": false,
      "action_on": "hotkey: ctrl+shift+m",
      "action_off": "hotkey: ctrl+shift+u",
      "image_on": "C:/pics/on.png",
      "image_off": "C:/pics/off.png"
    }
  }
}
```

Response kinds: `disabled`, `hotkey`, `macro`, `volume`, `media`, `toggle`.
Display kinds: `none`, `text`, `image`, `volume_bar`, `media_info`.

On first launch the app automatically migrates the legacy `bindings.json` + `display_rules.json` pair into `actions.json`.

### `splash_config.json`

Controls the dynamic splash screen:

```json
{
  "mode": "clock",
  "interval": 2.0,
  "idle_timeout": 2.0,
  "template": "{time_short} | {date_short}",
  "font_size": 12,
  "alignment": "left",
  "line_spacing": 2
}
```

Modes: `static` (uses `splash.bin`), `clock`, `text`, `custom`.
`interval` is the splash refresh period, `idle_timeout` is how long the device must be idle before the splash appears. For the text modes (`text`, `custom`) the text is rendered PC-side with a scalable bold font, configurable via `font_size` (8-24), `alignment` (`left`/`center`) and `line_spacing` (0-8). Templates support `{time}`, `{time_short}`, `{date}`, `{date_short}` and `{weekday}`. All settings are editable in the GUI Settings tab.

## Macro Syntax

Macros support these commands (one per line):

```
hotkey ctrl+c
key volume_up
text Hello world
display Line 1
sleep 500ms
clear_display
# this is a comment
```

Delays accept `ms` or `s` suffix. Lines starting with `#` are ignored.

## Splash Screen

1. Open the **Settings** tab
2. Pick a splash **mode** and set the update interval and idle timeout
3. For `static` mode, click **Load image...** and choose a PNG to convert and store as `app/splash.bin`
4. For `text`/`custom` modes, tune the **font size**, **line spacing** and **alignment**, and (for `custom`) edit the template with placeholders like `{time}`, `{date}`, `{weekday}`
5. Click **Preview** to see the result in the OLED preview, then **Save splash settings**

The splash is shown after the configured idle timeout and (for dynamic modes) refreshed at the configured interval.

## How It Works

```
Pico (firmware)  <--USB serial (JSON-Lines)-->  Desktop app (Slint GUI)
```

- **Connected** -- status updates appear instantly on the OLED
- **Idle** -- after the configured idle timeout, the splash screen is shown
- **Disconnected** -- the Pico displays "USB READY" and waits for reconnection
- **Secret mode** -- hold key 1 for 0.7s on the Pico to enter the game launcher; long-press key 1 again to exit

## Project Structure

```
PicoKeypad/
├── app/                        # Desktop application
│   ├── main.py                 # Entry point
│   ├── gui_slint/              # Slint GUI
│   │   ├── __init__.py         # SlintKeypadApp + run()
│   │   └── main_window.slint   # UI definition
│   ├── core/                   # Core logic
│   │   ├── engine.py           # Central engine (serial, actions, splash)
│   │   ├── actions.py          # Unified action model (response + display + toggle)
│   │   ├── actions_store.py    # Event → action mapping (actions.json)
│   │   ├── serial_transport.py # Serial port client with autodetection
│   │   ├── protocol.py         # Serial protocol builders & parsers
│   │   ├── splash_store.py     # Splash binary & splash config I/O
│   │   ├── splash_renderer.py  # Clock/text rendering for the dynamic splash
│   │   └── system_status.py    # Windows volume & media status queries
│   ├── tests/                  # Test suite
│   ├── actions.json            # Unified action mappings (migrated from bindings/display rules)
│   ├── splash.bin              # Saved static splash screen
│   ├── splash_config.json      # Dynamic splash configuration
│   └── pyinstaller.spec        # PyInstaller build spec
├── firmware/                   # Pico firmware (MicroPython)
│   ├── main.py                 # Core device loop
│   ├── config.py               # Pin assignments & timing constants
│   ├── protocol.py             # Serial protocol handler
│   ├── display_control.py      # OLED command handler
│   ├── inputs.py               # Key matrix & encoder drivers
│   ├── ssd1306.py              # SSD1306 OLED driver
│   ├── secret/                 # Secret mode game launcher
│   │   ├── main.py             # Game menu & launcher
│   │   └── games/
│   │       ├── horse_run.py    # Side-scrolling runner game
│   │       └── cube_escape.py  # Lane-dodging game
│   └── tests/
│       └── test.py             # Hardware diagnostic tool
└── PicoKeypad.spec             # Alternative PyInstaller spec (with icon)
```

## Testing

Run all tests:

```bash
python -m pytest app/tests/ -v
```

Or run a specific test module:

```bash
python -m app.tests.test_splash_store
```

The firmware includes `firmware/tests/test.py` -- a hardware diagnostic that tests the OLED, key matrix, and encoder on the Pico itself.
