# PicoKeypad

A customizable USB keypad built on the Raspberry Pi Pico with a 128x32 OLED display, a 4x3 matrix of keys, and a control knob. Connect it to your computer and use it to control media, volume, and display custom status information.

## STILL IN DEVELOPEMENT
### TODO:
- debug, test and clean up the code for both the pico and app
- create the 3D printed case for it (I'm no expert)

## What's Included

### Desktop Application (`app/`)
A Python GUI for easy configuration:
- **gui.py**: Main desktop interface with splash management and status controls
- **splash_store.py**: Save and load your custom splash screen images
- **serial_transport.py**: Handles USB communication with the device
- **bindings.py**: Configure which keys do what
- **display_rules.py**: Define what gets displayed based on system state

### Firmware (`firmware/`)
The code running on your Pico:
- **main.py**: Core device loop
- **protocol.py**: Manages serial communication with your computer
- **display_control.py**: Controls the SSD1306 OLED display
- **inputs.py**: Reads button and knob input

## Getting Started

### What You Need
- Raspberry Pi Pico (or Pico W clone)
- 128x32 SSD1306 OLED I2C display
- 4x3 keypad matrix
- A control knob

### Installation

1. **Install desktop app dependencies:**
   ```bash
   pip install -r app/requirements.txt
   ```

2. **Flash firmware to your Pico:**
   - Copy all files from the `firmware/` folder to your Pico
   - (Instructions for flashing depend on your Pico setup)

3. **Run the desktop app:**
   ```bash
   python app/main.py
   ```

## Using the Splash Screen

1. Open the desktop app
2. Go to the **Display** tab and find the **Splash** section
3. Click **Load Saved** to load a previous splash, or create a new one
4. Click **Send on Connect** to send your current image to the device
5. Click **Save Current** to save it for future use

Your splash screen is stored as a binary file and automatically sent whenever you connect.

## How It Works

- **Connected**: Status updates appear instantly on your display
- **Idle**: If no new status appears for 2 seconds, your splash screen is shown
- **Disconnected**: The keypad displays the ready message and waits for reconnection

## Testing

Run the included tests:
```bash
python -m app.tests.test_splash_store
```