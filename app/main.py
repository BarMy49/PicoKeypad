import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    parser = argparse.ArgumentParser(description="Pico keypad desktop application")
    parser.add_argument("--port", help="Serial port, for example COM5")
    parser.add_argument(
        "--gui",
        choices=("tkinter", "slint"),
        default="slint",
        help="GUI toolkit to use (default: slint)",
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

    if args.gui == "slint":
        try:
            if __package__ in (None, ""):
                from app.gui_slint import run
            else:
                from .gui_slint import run
        except ImportError as exc:
            sys.exit(
                f"Slint GUI selected but slint package is not installed.\n"
                f"Install it with: pip install slint\n"
                f"Or use the Tkinter GUI with: --gui tkinter\n"
                f"Error: {exc}"
            )
    else:
        if __package__ in (None, ""):
            from app.gui import run
        else:
            from .gui import run

    run(
        port=args.port,
        bindings_path=args.bindings,
        display_rules_path=args.display_rules,
    )


if __name__ == "__main__":
    main()
