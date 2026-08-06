# PicoKeypad
### or KeyCo as i've called it
A customizable USB keypad built on the Raspberry Pi Pico with a 128x32 SSD1306 OLED display, a 4x3 key matrix, and a rotary encoder knob. Connect it to your PC and use it to control media, system volume, launch macros, type text, or run hotkeys -- all configurable through a desktop GUI.

## Features

### Desktop Application
- **Slint-based GUI** with dark theme and live OLED preview
- **Action system** -- map every key and encoder event to:
  - **Hotkeys** -- multi-key combos (`ctrl+shift+a`)
  - **Function keys** -- single media/system keys (`volume_up`, `mute`, `play_pause`, `f13`, etc.)
  - **Text** -- type literal strings
  - **Macros** -- multi-step sequences with delays, display commands, and combos
  - **Display text** -- push custom text to the OLED
- **Display rules** -- automatically show on the OLED on any event:
  - **Volume bar** -- current system volume level
  - **Media info** -- currently playing track (Windows only)
  - **Custom text** -- with `{volume}` and `{mute}` placeholders
  - **Images** -- any PNG/JPEG (converted to 128x32 monochrome)
- **Splash screen** -- auto-sent when idle for 2 seconds; upload custom images
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
- `--bindings path/to/bindings.json` -- custom bindings file
- `--display-rules path/to/rules.json` -- custom display rules file

### 4. Connect

In the GUI, select your Pico's serial port from the dropdown and click **Connect**. The OLED will show "PICO KEYPAD" / "USB READY" until a connection is established.

## Building a Standalone EXE

```bash
pip install pyinstaller
pyinstaller PicoKeypad.spec
```

Output lands in `dist\PicoKeypad\`. Run `PicoKeypad.exe` from anywhere -- no Python needed.

The spec bundles `bindings.json`, `display_rules.json`, the Slint UI, and `splash.bin` (if present). Tkinter is excluded to keep the build lean. For an alternative build that includes an icon and `slint` hidden import, use the root-level `PicoKeypad.spec`.

## Configuration Files

### `bindings.json`

Maps hardware events to actions. Default layout:

| Event | Action |
|-------|--------|
| Encoder CW | `volume_up` |
| Encoder CCW | `volume_down` |
| Encoder button | `volume_mute` |
| Key 2 | `media_previous` |
| Key 3 | `media_play_pause` |
| Key 4 | `media_next` |
| Key 11 | hotkey `f13` |
| Key 12 | hotkey `f14` |

### `display_rules.json`

Defines what appears on the OLED when an event fires. Default rules show volume bar on encoder events, media info on transport keys, and custom images on F13/F14.

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

1. Navigate to the **Display** tab → **Splash** section
2. Click **Load Saved** to load a previous splash, or edit the OLED preview to create one
3. Click **Send on Connect** to push the current image to the device immediately
4. Click **Save Current** to persist it for future sessions

The splash is stored as `app/splash.bin` (512-byte OLED bitmap) and is auto-sent to the device after 2 seconds of inactivity.

## How It Works

```
Pico (firmware)  <--USB serial (JSON-Lines)-->  Desktop app (Slint GUI)
```

- **Connected** -- status updates appear instantly on the OLED
- **Idle** -- after 2 seconds of no events, the splash screen is shown
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
│   │   ├── engine.py           # Central engine (serial, bindings, display rules)
│   │   ├── actions.py          # Action system (hotkeys, macros, keyboard backend)
│   │   ├── bindings.py         # Binding store (event → action mapping)
│   │   ├── display_rules.py    # Display rule store (event → OLED content)
│   │   ├── serial_transport.py # Serial port client with autodetection
│   │   ├── protocol.py         # Serial protocol builders & parsers
│   │   ├── splash_store.py     # Splash screen binary I/O
│   │   └── system_status.py    # Windows volume & media status queries
│   ├── tests/                  # Test suite
│   ├── bindings.json           # Default key bindings
│   ├── display_rules.json      # Default display rules
│   ├── splash.bin              # Saved splash screen
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
