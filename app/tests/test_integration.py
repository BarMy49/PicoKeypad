import unittest

from app.core import protocol
from app.core.actions import (
    ACTION_FUNCTION,
    ACTION_HOTKEY,
    ACTION_MACRO,
    Action,
    ActionRunner,
)
from app.core.bindings import BindingStore
from app.core.display_rules import (
    DISPLAY_TEXT,
    DISPLAY_VOLUME,
    DisplayRule,
    DisplayRuleStore,
)


class IntegrationTests(unittest.TestCase):
    def test_binding_store_rejects_invalid_kinds(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bindings.json"
            path.write_text(json.dumps({
                "version": 1,
                "bindings": {
                    "key:1:down": {"kind": "totally_fake", "value": "x"},
                    "key:2:down": {"kind": "function", "value": "volume_up"},
                }
            }))
            store = BindingStore(path=path)
            self.assertFalse(store.get("key:1:down").enabled())
            self.assertTrue(store.get("key:2:down").enabled())
            self.assertEqual(store.get("key:2:down").kind, ACTION_FUNCTION)

    def test_display_rule_store_rejects_invalid_kinds(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rules.json"
            path.write_text(json.dumps({
                "version": 1,
                "rules": {
                    "key:1:down": {"kind": "bogus_kind", "value": "x"},
                    "key:2:down": {"kind": "text", "value": "hello"},
                }
            }))
            store = DisplayRuleStore(path=path)
            self.assertFalse(store.get("key:1:down").enabled())
            self.assertTrue(store.get("key:2:down").enabled())
            self.assertEqual(store.get("key:2:down").kind, DISPLAY_TEXT)

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

        import time
        for a in actions:
            runner.run(a)
        time.sleep(0.3)

        self.assertEqual(len(errors), 0)


if __name__ == "__main__":
    unittest.main()
