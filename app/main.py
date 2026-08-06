import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Pico keypad desktop application")
    parser.add_argument("--port", help="Serial port, for example COM5")
    parser.add_argument(
        "-m", "--minimized",
        action="store_true",
        help="Start minimized to system tray",
    )
    parser.add_argument(
        "--bindings",
        help="Path to the bindings JSON file. Defaults to app/bindings.json",
    )
    parser.add_argument(
        "--display-rules",
        help="Path to the display rules JSON file. Defaults to app/display_rules.json",
    )
    args = parser.parse_args()

    try:
        if __package__ in (None, ""):
            from app.gui_slint import run
        else:
            from .gui_slint import run
    except ImportError as exc:
        sys.exit(
            f"Slint GUI could not be loaded.\n"
            f"Install slint with: pip install slint\n"
            f"Error: {exc}"
        )

    run(
        port=args.port,
        bindings_path=args.bindings,
        display_rules_path=args.display_rules,
        start_minimized=args.minimized or None,
    )


if __name__ == "__main__":
    main()
