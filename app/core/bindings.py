import json
import sys
from pathlib import Path

from .actions import Action


EVENTS: tuple[tuple[str, str], ...] = tuple(
    (f"key:{key}:down", f"Key {key} press") for key in range(1, 13)
) + tuple(
    (f"key:{key}:up", f"Key {key} release") for key in range(1, 13)
) + (
    ("encoder:cw", "Encoder clockwise"),
    ("encoder:ccw", "Encoder counter-clockwise"),
    ("encoder:button_down", "Encoder button press"),
    ("encoder:button_up", "Encoder button release"),
)

EVENT_LABELS = dict(EVENTS)
LABEL_EVENTS = {label: event_id for event_id, label in EVENTS}


def _app_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def default_bindings_path() -> Path:
    return _app_dir() / "bindings.json"


def event_id_from_message(message: dict) -> str | None:
    message_type = message.get("type")

    if message_type == "key":
        key = message.get("key")
        event = message.get("event")
        if key is None or event not in ("down", "up"):
            return None
        return "key:{}:{}".format(key, event)

    if message_type == "encoder":
        event = message.get("event")
        if event == "turn":
            delta = int(message.get("delta", 0))
            if delta > 0:
                return "encoder:cw"
            if delta < 0:
                return "encoder:ccw"
            return None

        if event in ("button_down", "button_up"):
            return "encoder:{}".format(event)

    return None


class BindingStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_bindings_path()
        self.bindings: dict[str, Action] = {}
        self.load()

    def load(self) -> None:
        self.bindings = {}
        if not self.path.exists():
            return

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        raw_bindings = data.get("bindings", data)
        if not isinstance(raw_bindings, dict):
            return

        for event_id, raw_action in raw_bindings.items():
            self.bindings[str(event_id)] = Action.from_dict(raw_action)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "bindings": {
                event_id: action.to_dict()
                for event_id, action in sorted(self.bindings.items())
                if action.enabled()
            },
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, event_id: str) -> Action:
        return self.bindings.get(event_id, Action())

    def set(self, event_id: str, action: Action) -> None:
        if action.enabled():
            self.bindings[event_id] = action
        else:
            self.bindings.pop(event_id, None)
        self.save()

    def clear(self, event_id: str) -> None:
        self.bindings.pop(event_id, None)
        self.save()
