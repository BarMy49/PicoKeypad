import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from app.core.actions import (
    ACTION_HOTKEY,
    ACTION_MACRO,
    ACTION_MEDIA,
    ACTION_TOGGLE,
    ACTION_VOLUME,
    DISPLAY_TEXT,
    Action,
    ActionRunner,
)
from app.core.actions_store import ActionStore
from app.core.engine import PicoKeypadEngine


class StoreValidationTests(unittest.TestCase):
    def test_action_store_rejects_invalid_kinds(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "actions.json"
            path.write_text(json.dumps({
                "version": 1,
                "actions": {
                    "key:1:down": {"kind": "totally_fake", "value": "x"},
                    "key:2:down": {"kind": ACTION_VOLUME, "value": "volume_up"},
                }
            }))
            store = ActionStore(path=path)
            self.assertFalse(store.get("key:1:down").enabled())
            self.assertTrue(store.get("key:2:down").enabled())
            self.assertEqual(store.get("key:2:down").kind, ACTION_VOLUME)

    def test_action_store_rejects_invalid_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "actions.json"
            path.write_text(json.dumps({
                "version": 1,
                "actions": {
                    "key:1:down": {"kind": ACTION_HOTKEY, "value": "f13", "display": "bogus_kind"},
                }
            }))
            store = ActionStore(path=path)
            self.assertEqual(store.get("key:1:down").display, "none")


class ActionRunnerIntegrationTests(unittest.TestCase):
    def test_action_runner_event_loop_safety(self):
        errors = []

        runner = ActionRunner(
            on_error=lambda m: errors.append(m),
            on_display=lambda lines: None,
        )
        actions = [
            Action(kind=ACTION_MACRO, value="sleep: 10\ndisplay: done"),
            Action(kind=ACTION_MACRO, value="display: test"),
        ]

        for a in actions:
            runner.run(a)

        self.assertEqual(len(errors), 0)


class EnginePipelineTests(unittest.TestCase):
    def test_engine_default_display_for_kind(self):
        self.assertEqual(PicoKeypadEngine.default_display_for_kind(ACTION_VOLUME), "volume_bar")
        self.assertEqual(PicoKeypadEngine.default_display_for_kind(ACTION_MEDIA), "media_info")
        self.assertEqual(PicoKeypadEngine.default_display_for_kind(ACTION_HOTKEY), "none")

    def test_volume_action_fires_volume_display(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = PicoKeypadEngine(
                initial_port="COM99",
                actions_path=Path(tmp) / "actions.json",
            )
            engine.action_runner.keyboard = MagicMock()
            engine.set_action("key:1:down", Action(kind=ACTION_VOLUME, value="volume_up"))

            captured_lines: list[list[str]] = []
            engine.events.set("on_display_lines", lambda lines: captured_lines.append(lines))

            engine._handle_message({"type": "key", "key": 1, "event": "down"})
            time.sleep(1.0)

            self.assertEqual(len(captured_lines), 1)

    def test_text_display_fires_on_connected_client(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = PicoKeypadEngine(
                initial_port="COM99",
                actions_path=Path(tmp) / "actions.json",
            )

            class FakeClient:
                is_open = True
                port = "COM99"

                def send_text(self, lines, clear=True):
                    self.sent = lines

            fake = FakeClient()
            engine.client = fake
            engine.action_runner.keyboard = MagicMock()

            engine.set_action("key:2:down", Action(
                kind=ACTION_HOTKEY,
                value="f13",
                display=DISPLAY_TEXT,
                display_value="hello",
            ))

            engine._handle_message({"type": "key", "key": 2, "event": "down"})
            time.sleep(0.2)

            self.assertEqual(fake.sent, ["hello"])

    def test_toggle_pipeline_flips_state_and_fires_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            actions_path = Path(tmp) / "actions.json"
            engine = PicoKeypadEngine(
                initial_port="COM99",
                actions_path=actions_path,
            )
            engine.set_action("key:1:down", Action(kind=ACTION_TOGGLE, state=False))

            callback_events: list[tuple[str, bool]] = []
            engine.events.set("on_toggle_state", lambda event_id, state: callback_events.append((event_id, state)))

            engine._handle_message({"type": "key", "key": 1, "event": "down"})
            self.assertEqual(callback_events, [("key:1:down", True)])
            self.assertTrue(engine.get_toggle_state("key:1:down"))

            engine._handle_message({"type": "key", "key": 1, "event": "down"})
            self.assertEqual(callback_events[1], ("key:1:down", False))
            self.assertFalse(engine.get_toggle_state("key:1:down"))

            data = json.loads(actions_path.read_text(encoding="utf-8"))
            self.assertEqual(data["actions"]["key:1:down"]["kind"], ACTION_TOGGLE)
            self.assertFalse(data["actions"]["key:1:down"]["state"])


if __name__ == "__main__":
    unittest.main()
