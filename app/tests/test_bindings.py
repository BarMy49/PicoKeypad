import json
import tempfile
import unittest
from pathlib import Path

from app.core.actions import ACTION_FUNCTION, ACTION_HOTKEY, ACTION_MACRO, Action
from app.core.bindings import (
    EVENTS,
    EVENT_LABELS,
    LABEL_EVENTS,
    BindingStore,
    event_id_from_message,
)


class EventsTests(unittest.TestCase):
    def test_event_count(self):
        self.assertEqual(len(EVENTS), 28)

    def test_key_events_present(self):
        for key in range(1, 13):
            self.assertIn(f"key:{key}:down", EVENT_LABELS)
            self.assertIn(f"key:{key}:up", EVENT_LABELS)

    def test_encoder_events_present(self):
        self.assertIn("encoder:cw", EVENT_LABELS)
        self.assertIn("encoder:ccw", EVENT_LABELS)
        self.assertIn("encoder:button_down", EVENT_LABELS)
        self.assertIn("encoder:button_up", EVENT_LABELS)

    def test_label_events_roundtrip(self):
        for event_id, label in EVENTS:
            self.assertEqual(LABEL_EVENTS[label], event_id)

    def test_event_labels_are_unique(self):
        labels = list(EVENT_LABELS.values())
        self.assertEqual(len(labels), len(set(labels)))


class EventIdFromMessageTests(unittest.TestCase):
    def test_key_down(self):
        msg = {"type": "key", "key": 5, "event": "down"}
        self.assertEqual(event_id_from_message(msg), "key:5:down")

    def test_key_up(self):
        msg = {"type": "key", "key": 12, "event": "up"}
        self.assertEqual(event_id_from_message(msg), "key:12:up")

    def test_encoder_cw(self):
        msg = {"type": "encoder", "event": "turn", "delta": 3}
        self.assertEqual(event_id_from_message(msg), "encoder:cw")

    def test_encoder_ccw(self):
        msg = {"type": "encoder", "event": "turn", "delta": -1}
        self.assertEqual(event_id_from_message(msg), "encoder:ccw")

    def test_encoder_turn_zero_delta(self):
        msg = {"type": "encoder", "event": "turn", "delta": 0}
        self.assertIsNone(event_id_from_message(msg))

    def test_encoder_button_down(self):
        msg = {"type": "encoder", "event": "button_down"}
        self.assertEqual(event_id_from_message(msg), "encoder:button_down")

    def test_encoder_button_up(self):
        msg = {"type": "encoder", "event": "button_up"}
        self.assertEqual(event_id_from_message(msg), "encoder:button_up")

    def test_unknown_type_returns_none(self):
        msg = {"type": "hello"}
        self.assertIsNone(event_id_from_message(msg))

    def test_empty_message(self):
        self.assertIsNone(event_id_from_message({}))

    def test_key_no_event_returns_none(self):
        msg = {"type": "key", "key": 1}
        self.assertIsNone(event_id_from_message(msg))

    def test_key_invalid_event_returns_none(self):
        msg = {"type": "key", "key": 1, "event": "hold"}
        self.assertIsNone(event_id_from_message(msg))

    def test_encoder_unknown_event_returns_none(self):
        msg = {"type": "encoder", "event": "rotate"}
        self.assertIsNone(event_id_from_message(msg))


class BindingStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test_bindings.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_bindings_empty(self):
        store = BindingStore(path=self.path)
        self.assertEqual(store.bindings, {})

    def test_get_unknown_event_returns_disabled(self):
        store = BindingStore(path=self.path)
        action = store.get("key:1:down")
        self.assertFalse(action.enabled())

    def test_set_and_get(self):
        store = BindingStore(path=self.path)
        action = Action(kind=ACTION_HOTKEY, value="ctrl+c")
        store.set("key:1:down", action)
        retrieved = store.get("key:1:down")
        self.assertEqual(retrieved.kind, ACTION_HOTKEY)
        self.assertEqual(retrieved.value, "ctrl+c")

    def test_set_disabled_action_removes_binding(self):
        store = BindingStore(path=self.path)
        store.set("key:1:down", Action(kind=ACTION_HOTKEY, value="ctrl+c"))
        store.set("key:1:down", Action(kind="disabled", value=""))
        self.assertNotIn("key:1:down", store.bindings)

    def test_clear_removes_binding(self):
        store = BindingStore(path=self.path)
        store.set("key:1:down", Action(kind=ACTION_HOTKEY, value="ctrl+c"))
        store.clear("key:1:down")
        self.assertNotIn("key:1:down", store.bindings)

    def test_save_and_load_persists(self):
        store = BindingStore(path=self.path)
        store.set("encoder:cw", Action(kind=ACTION_FUNCTION, value="volume_up"))
        store.set("key:1:down", Action(kind=ACTION_MACRO, value="sleep: 100"))

        store2 = BindingStore(path=self.path)
        self.assertEqual(store2.get("encoder:cw").kind, ACTION_FUNCTION)
        self.assertEqual(store2.get("key:1:down").kind, ACTION_MACRO)

    def test_load_corrupted_json_returns_empty(self):
        self.path.write_text("not json {{{")
        store = BindingStore(path=self.path)
        self.assertEqual(store.bindings, {})

    def test_bindings_top_level_key(self):
        data = json.dumps({"version": 1, "bindings": {
            "key:1:down": {"kind": "function", "value": "volume_up"}
        }})
        self.path.write_text(data)
        store = BindingStore(path=self.path)
        self.assertEqual(store.get("key:1:down").kind, ACTION_FUNCTION)


if __name__ == "__main__":
    unittest.main()
