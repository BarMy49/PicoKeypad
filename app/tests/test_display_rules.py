import json
import tempfile
import unittest
from pathlib import Path

from app.core.display_rules import (
    DISPLAY_IMAGE,
    DISPLAY_MEDIA,
    DISPLAY_NONE,
    DISPLAY_TEXT,
    DISPLAY_TYPES,
    DISPLAY_VOLUME,
    DisplayRule,
    DisplayRuleStore,
)


class DisplayRuleTests(unittest.TestCase):
    def test_default_rule_is_none(self):
        r = DisplayRule()
        self.assertEqual(r.kind, DISPLAY_NONE)
        self.assertEqual(r.value, "")
        self.assertFalse(r.enabled())

    def test_enabled_rule(self):
        r = DisplayRule(kind=DISPLAY_TEXT, value="hello")
        self.assertTrue(r.enabled())

    def test_none_kind_not_enabled(self):
        r = DisplayRule(kind=DISPLAY_NONE, value="something")
        self.assertFalse(r.enabled())

    def test_volume_and_media_are_enabled(self):
        self.assertTrue(DisplayRule(kind=DISPLAY_VOLUME).enabled())
        self.assertTrue(DisplayRule(kind=DISPLAY_MEDIA).enabled())

    def test_to_dict(self):
        r = DisplayRule(kind=DISPLAY_IMAGE, value="/path/to/img.png")
        self.assertEqual(r.to_dict(), {"kind": DISPLAY_IMAGE, "value": "/path/to/img.png"})

    def test_from_dict_valid(self):
        r = DisplayRule.from_dict({"kind": DISPLAY_TEXT, "value": "hello"})
        self.assertEqual(r.kind, DISPLAY_TEXT)
        self.assertEqual(r.value, "hello")

    def test_from_dict_invalid_kind_defaults_to_none(self):
        r = DisplayRule.from_dict({"kind": "bogus", "value": "x"})
        self.assertEqual(r.kind, DISPLAY_NONE)

    def test_from_dict_none_returns_default(self):
        r = DisplayRule.from_dict(None)
        self.assertEqual(r.kind, DISPLAY_NONE)

    def test_from_dict_missing_value(self):
        r = DisplayRule.from_dict({"kind": DISPLAY_TEXT})
        self.assertEqual(r.value, "")


class DisplayTypesTests(unittest.TestCase):
    def test_all_types_present(self):
        self.assertIn(DISPLAY_NONE, DISPLAY_TYPES)
        self.assertIn(DISPLAY_TEXT, DISPLAY_TYPES)
        self.assertIn(DISPLAY_IMAGE, DISPLAY_TYPES)
        self.assertIn(DISPLAY_VOLUME, DISPLAY_TYPES)
        self.assertIn(DISPLAY_MEDIA, DISPLAY_TYPES)

    def test_length(self):
        self.assertEqual(len(DISPLAY_TYPES), 5)


class DisplayRuleStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "test_rules.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_rules_empty(self):
        store = DisplayRuleStore(path=self.path)
        self.assertEqual(store.rules, {})

    def test_get_unknown_event_returns_none_rule(self):
        store = DisplayRuleStore(path=self.path)
        rule = store.get("key:1:down")
        self.assertFalse(rule.enabled())

    def test_set_and_get(self):
        store = DisplayRuleStore(path=self.path)
        rule = DisplayRule(kind=DISPLAY_TEXT, value="hello")
        store.set("key:1:down", rule)
        retrieved = store.get("key:1:down")
        self.assertEqual(retrieved.kind, DISPLAY_TEXT)
        self.assertEqual(retrieved.value, "hello")

    def test_set_none_rule_removes(self):
        store = DisplayRuleStore(path=self.path)
        store.set("key:1:down", DisplayRule(kind=DISPLAY_TEXT, value="hello"))
        store.set("key:1:down", DisplayRule(kind=DISPLAY_NONE, value=""))
        self.assertNotIn("key:1:down", store.rules)

    def test_clear_removes_rule(self):
        store = DisplayRuleStore(path=self.path)
        store.set("key:1:down", DisplayRule(kind=DISPLAY_TEXT, value="hello"))
        store.clear("key:1:down")
        self.assertNotIn("key:1:down", store.rules)

    def test_save_and_load_persists(self):
        store = DisplayRuleStore(path=self.path)
        store.set("encoder:cw", DisplayRule(kind=DISPLAY_VOLUME, value=""))
        store.set("key:1:down", DisplayRule(kind=DISPLAY_IMAGE, value="/img.png"))

        store2 = DisplayRuleStore(path=self.path)
        self.assertEqual(store2.get("encoder:cw").kind, DISPLAY_VOLUME)
        self.assertEqual(store2.get("key:1:down").kind, DISPLAY_IMAGE)
        self.assertEqual(store2.get("key:1:down").value, "/img.png")

    def test_load_corrupted_json_returns_empty(self):
        self.path.write_text("bad json {")
        store = DisplayRuleStore(path=self.path)
        self.assertEqual(store.rules, {})

    def test_load_rules_top_level_key(self):
        data = json.dumps({"version": 1, "rules": {
            "key:1:down": {"kind": "text", "value": "hello"}
        }})
        self.path.write_text(data)
        store = DisplayRuleStore(path=self.path)
        self.assertEqual(store.get("key:1:down").kind, DISPLAY_TEXT)
        self.assertEqual(store.get("key:1:down").value, "hello")


if __name__ == "__main__":
    unittest.main()
