import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.gui import run
else:
    from .gui import run


def main() -> None:
    parser = argparse.ArgumentParser(description="Pico keypad desktop application")
    parser.add_argument("--port", help="Serial port, for example COM5")
    parser.add_argument(
        "--bindings",
        help="Path to the bindings JSON file. Defaults to app/bindings.json",
    )
    parser.add_argument(
        "--display-rules",
        help="Path to the display rules JSON file. Defaults to app/display_rules.json",
    )
    args = parser.parse_args()
    run(
        port=args.port,
        bindings_path=args.bindings,
        display_rules_path=args.display_rules,
    )


if __name__ == "__main__":
    main()
