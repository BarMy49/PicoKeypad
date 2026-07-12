import json
from dataclasses import dataclass
from pathlib import Path


DISPLAY_NONE = "none"
DISPLAY_TEXT = "text"
DISPLAY_IMAGE = "image"
DISPLAY_VOLUME = "volume_status"
DISPLAY_MEDIA = "media_status"

DISPLAY_TYPES = (
    DISPLAY_NONE,
    DISPLAY_TEXT,
    DISPLAY_IMAGE,
    DISPLAY_VOLUME,
    DISPLAY_MEDIA,
)


@dataclass
class DisplayRule:
    kind: str = DISPLAY_NONE
    value: str = ""

    def enabled(self) -> bool:
        return self.kind != DISPLAY_NONE

    def to_dict(self) -> dict[str, str]:
        return {
            "kind": self.kind,
            "value": self.value,
        }

    @classmethod
    def from_dict(cls, data: dict[str, str] | None) -> "DisplayRule":
        if not isinstance(data, dict):
            return cls()

        kind = str(data.get("kind", DISPLAY_NONE))
        if kind not in DISPLAY_TYPES:
            kind = DISPLAY_NONE

        return cls(kind=kind, value=str(data.get("value", "")))


def default_display_rules_path() -> Path:
    return Path(__file__).resolve().with_name("display_rules.json")


class DisplayRuleStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path is not None else default_display_rules_path()
        self.rules: dict[str, DisplayRule] = {}
        self.load()

    def load(self) -> None:
        self.rules = {}
        if not self.path.exists():
            return

        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return

        raw_rules = data.get("rules", data)
        if not isinstance(raw_rules, dict):
            return

        for event_id, raw_rule in raw_rules.items():
            self.rules[str(event_id)] = DisplayRule.from_dict(raw_rule)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "rules": {
                event_id: rule.to_dict()
                for event_id, rule in sorted(self.rules.items())
                if rule.enabled()
            },
        }
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def get(self, event_id: str) -> DisplayRule:
        return self.rules.get(event_id, DisplayRule())

    def set(self, event_id: str, rule: DisplayRule) -> None:
        if rule.enabled():
            self.rules[event_id] = rule
        else:
            self.rules.pop(event_id, None)
        self.save()

    def clear(self, event_id: str) -> None:
        self.rules.pop(event_id, None)
        self.save()

