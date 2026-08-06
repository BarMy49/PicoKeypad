# PyInstaller spec for PicoKeypad
# Build with: pyinstaller app\pyinstaller.spec

import os
import sys
from pathlib import Path

import PyInstaller.utils.win32.versioninfo as vs

# Determine if we should build the Slint or Tkinter GUI (default: slint)
APP_ROOT = Path("app")
SLINT_FILE = APP_ROOT / "gui_slint" / "main_window.slint"

datas = [
    (str(SLINT_FILE), "app/gui_slint"),
]

a = Analysis(
    [str(APP_ROOT / "main.py")],
    pathex=[str(Path.cwd())],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "PIL",
        "PIL.Image",
        "PIL.ImageDraw",
        "PIL.ImageFont",
        "serial",
        "serial.tools.list_ports",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        "tkinter",
        "tcl",
        "tk",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="PicoKeypad",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)
