import unittest

from app.core.actions import (
    ACTION_DISABLED,
    ACTION_DISPLAY_TEXT,
    ACTION_FUNCTION,
    ACTION_HOTKEY,
    ACTION_MACRO,
    ACTION_TEXT,
    ACTION_TYPES,
    Action,
    ActionError,
    ActionRunner,
    KEY_GROUPS,
    KEY_NAMES,
    parse_delay,
    parse_hotkey,
    split_macro_line,
    display_lines_from_value,
)


class ActionTests(unittest.TestCase):
    def test_default_action_is_disabled(self):
        a = Action()
        self.assertEqual(a.kind, ACTION_DISABLED)
        self.assertEqual(a.value, "")
        self.assertFalse(a.enabled())

    def test_enabled_action_returns_true(self):
        a = Action(kind=ACTION_HOTKEY, value="ctrl+c")
        self.assertTrue(a.enabled())

    def test_disabled_kind_not_enabled(self):
        a = Action(kind=ACTION_DISABLED, value="something")
        self.assertFalse(a.enabled())

    def test_empty_value_not_enabled(self):
        a = Action(kind=ACTION_HOTKEY, value="")
        self.assertFalse(a.enabled())

    def test_whitespace_value_not_enabled(self):
        a = Action(kind=ACTION_HOTKEY, value="   ")
        self.assertFalse(a.enabled())

    def test_to_dict(self):
        a = Action(kind=ACTION_MACRO, value="sleep: 100")
        self.assertEqual(a.to_dict(), {"kind": ACTION_MACRO, "value": "sleep: 100"})

    def test_from_dict_valid(self):
        a = Action.from_dict({"kind": ACTION_FUNCTION, "value": "volume_up"})
        self.assertEqual(a.kind, ACTION_FUNCTION)
        self.assertEqual(a.value, "volume_up")

    def test_from_dict_invalid_kind_defaults_to_disabled(self):
        a = Action.from_dict({"kind": "invalid_kind", "value": "x"})
        self.assertEqual(a.kind, ACTION_DISABLED)

    def test_from_dict_none_returns_default(self):
        a = Action.from_dict(None)
        self.assertEqual(a.kind, ACTION_DISABLED)

    def test_from_dict_not_dict_returns_default(self):
        a = Action.from_dict("not a dict")
        self.assertEqual(a.kind, ACTION_DISABLED)

    def test_from_dict_missing_value_defaults_empty(self):
        a = Action.from_dict({"kind": ACTION_TEXT})
        self.assertEqual(a.value, "")


class ActionTypesTests(unittest.TestCase):
    def test_action_types_tuple_contains_all(self):
        self.assertIn(ACTION_DISABLED, ACTION_TYPES)
        self.assertIn(ACTION_HOTKEY, ACTION_TYPES)
        self.assertIn(ACTION_TEXT, ACTION_TYPES)
        self.assertIn(ACTION_FUNCTION, ACTION_TYPES)
        self.assertIn(ACTION_MACRO, ACTION_TYPES)
        self.assertIn(ACTION_DISPLAY_TEXT, ACTION_TYPES)

    def test_action_types_length(self):
        self.assertEqual(len(ACTION_TYPES), 6)


class ParseHotkeyTests(unittest.TestCase):
    def test_simple_hotkey(self):
        self.assertEqual(parse_hotkey("ctrl+c"), ["ctrl", "c"])

    def test_multi_key_hotkey(self):
        self.assertEqual(parse_hotkey("ctrl+shift+a"), ["ctrl", "shift", "a"])

    def test_comma_separator(self):
        self.assertEqual(parse_hotkey("ctrl,shift,a"), ["ctrl", "shift", "a"])

    def test_space_separator(self):
        self.assertEqual(parse_hotkey("ctrl shift a"), ["ctrl", "shift", "a"])

    def test_mixed_separators(self):
        self.assertEqual(parse_hotkey("ctrl+ shift, a"), ["ctrl", "shift", "a"])

    def test_empty_string_returns_empty(self):
        self.assertEqual(parse_hotkey(""), [])

    def test_trims_whitespace(self):
        self.assertEqual(parse_hotkey("  ctrl + c  "), ["ctrl", "c"])

    def test_lowercases_keys(self):
        self.assertEqual(parse_hotkey("CTRL+SHIFT+A"), ["ctrl", "shift", "a"])


class SplitMacroLineTests(unittest.TestCase):
    def test_colon_separator(self):
        self.assertEqual(split_macro_line("sleep: 100"), ("sleep", "100"))

    def test_space_separator(self):
        self.assertEqual(split_macro_line("sleep 100"), ("sleep", "100"))

    def test_no_value(self):
        self.assertEqual(split_macro_line("clear_display"), ("clear_display", ""))

    def test_spaces_in_value(self):
        self.assertEqual(split_macro_line("display: hello world"), ("display", "hello world"))

    def test_extra_colons(self):
        self.assertEqual(split_macro_line("hotkey: ctrl+shift+a"), ("hotkey", "ctrl+shift+a"))

    def test_trims_whitespace(self):
        self.assertEqual(split_macro_line("  sleep  :  100  "), ("sleep", "100"))


class ParseDelayTests(unittest.TestCase):
    def test_plain_number(self):
        self.assertEqual(parse_delay("100"), 100)

    def test_ms_suffix(self):
        self.assertEqual(parse_delay("250ms"), 250)

    def test_s_suffix(self):
        self.assertEqual(parse_delay("1.5s"), 1500)

    def test_s_suffix_integer(self):
        self.assertEqual(parse_delay("2s"), 2000)

    def test_negative_clamped(self):
        self.assertEqual(parse_delay("-100"), 0)

    def test_zero(self):
        self.assertEqual(parse_delay("0"), 0)

    def test_float_string(self):
        self.assertEqual(parse_delay("42.5"), 42)

    def test_negative_s_suffix(self):
        self.assertEqual(parse_delay("-2s"), 0)


class DisplayLinesFromValueTests(unittest.TestCase):
    def test_single_line(self):
        self.assertEqual(display_lines_from_value("hello"), ["hello"])

    def test_multiline(self):
        self.assertEqual(display_lines_from_value("line1\nline2"), ["line1", "line2"])

    def test_pipe_separator(self):
        self.assertEqual(display_lines_from_value("a|b|c"), ["a", "b", "c"])

    def test_escaped_newline(self):
        self.assertEqual(display_lines_from_value("a\\nb"), ["a", "b"])

    def test_truncates_to_4_lines(self):
        self.assertEqual(
            display_lines_from_value("a\nb\nc\nd\ne"),
            ["a", "b", "c", "d"],
        )

    def test_empty_string(self):
        self.assertEqual(display_lines_from_value(""), [])

    def test_strips_whitespace(self):
        self.assertEqual(display_lines_from_value(" a | b "), ["a", "b"])


class KeyGroupsTests(unittest.TestCase):
    def test_all_groups_have_keys(self):
        for group_name, keys in KEY_GROUPS:
            with self.subTest(group=group_name):
                self.assertGreater(len(keys), 0)

    def test_all_keys_in_key_names(self):
        names_set = set(KEY_NAMES)
        for _, keys in KEY_GROUPS:
            for key in keys:
                with self.subTest(key=key):
                    self.assertIn(key, names_set)

    def test_known_keys_present(self):
        names_set = set(KEY_NAMES)
        self.assertIn("ctrl", names_set)
        self.assertIn("shift", names_set)
        self.assertIn("a", names_set)
        self.assertIn("f1", names_set)
        self.assertIn("volume_up", names_set)
        self.assertIn("enter", names_set)


class ActionRunnerTests(unittest.TestCase):
    def setUp(self):
        self.errors = []
        self.displays = []
        self.runner = ActionRunner(
            on_error=lambda msg: self.errors.append(msg),
            on_display=lambda lines: self.displays.append(lines),
        )

    def test_disabled_action_not_run(self):
        self.runner.run(Action(kind=ACTION_DISABLED, value="x"))
        self.assertEqual(self.errors, [])
        self.assertEqual(self.displays, [])

    def test_display_text_action(self):
        import time
        a = Action(kind=ACTION_DISPLAY_TEXT, value="hello|world")
        self.runner.run(a)
        time.sleep(0.2)
        self.assertEqual(len(self.displays), 1)
        self.assertEqual(self.displays[0], ["hello", "world"])

    def test_macro_display_command(self):
        import time
        a = Action(kind=ACTION_MACRO, value="display: macro text")
        self.runner.run(a)
        time.sleep(0.2)
        self.assertEqual(len(self.displays), 1)

    def test_macro_sleep_command(self):
        import time
        a = Action(kind=ACTION_MACRO, value="sleep: 50\ndisplay: after sleep")
        self.runner.run(a)
        time.sleep(0.2)
        self.assertEqual(len(self.displays), 1)

    def test_macro_comments_skipped(self):
        import time
        a = Action(kind=ACTION_MACRO, value="# comment\ndisplay: actual")
        self.runner.run(a)
        time.sleep(0.2)
        self.assertEqual(len(self.displays), 1)

    def test_macro_clear_display_command(self):
        import time
        a = Action(kind=ACTION_MACRO, value="clear_display")
        self.runner.run(a)
        time.sleep(0.2)
        self.assertEqual(len(self.displays), 1)

    def test_run_without_display_handler_raises_in_thread(self):
        runner = ActionRunner(on_error=lambda msg: self.errors.append(msg))
        a = Action(kind=ACTION_DISPLAY_TEXT, value="test")
        runner.run(a)
        import time
        time.sleep(0.2)
        self.assertEqual(len(self.errors), 1)

    def test_macro_unknown_command_raises(self):
        import time
        a = Action(kind=ACTION_MACRO, value="bogus: 123")
        self.runner.run(a)
        time.sleep(0.2)
        self.assertEqual(len(self.errors), 1)


if __name__ == "__main__":
    unittest.main()
