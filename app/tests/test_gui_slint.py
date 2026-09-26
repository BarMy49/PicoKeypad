import os
import tempfile
import unittest
from pathlib import Path

import slint


class SlintWindowCompileTests(unittest.TestCase):
    def test_slint_file_compiles(self):
        slint_file = os.path.join(
            os.path.dirname(__file__), "..", "gui_slint", "main_window.slint"
        )
        slint_file = os.path.abspath(slint_file)
        comps = slint.load_file(slint_file)
        self.assertIsNotNone(comps)
        window = comps.MainWindow()
        self.assertIsNotNone(window)

    def test_window_has_expected_properties(self):
        slint_file = os.path.join(
            os.path.dirname(__file__), "..", "gui_slint", "main_window.slint"
        )
        slint_file = os.path.abspath(slint_file)
        comps = slint.load_file(slint_file)
        w = comps.MainWindow()

        self.assertTrue(hasattr(w, "port_text"))
        self.assertTrue(hasattr(w, "connected"))
        self.assertTrue(hasattr(w, "status_text"))
        self.assertTrue(hasattr(w, "key_1_pressed"))
        self.assertTrue(hasattr(w, "key_12_pressed"))
        self.assertTrue(hasattr(w, "encoder_direction"))
        self.assertTrue(hasattr(w, "encoder_btn_pressed"))
        self.assertTrue(hasattr(w, "action_event"))
        self.assertTrue(hasattr(w, "action_kind"))
        self.assertTrue(hasattr(w, "action_value"))
        self.assertTrue(hasattr(w, "volume_value"))
        self.assertTrue(hasattr(w, "media_value"))
        self.assertTrue(hasattr(w, "toggle_image_on"))
        self.assertTrue(hasattr(w, "toggle_image_off"))
        self.assertTrue(hasattr(w, "display_kind"))
        self.assertTrue(hasattr(w, "display_value"))
        self.assertTrue(hasattr(w, "splash_mode"))
        self.assertTrue(hasattr(w, "splash_interval"))
        self.assertTrue(hasattr(w, "splash_idle"))
        self.assertTrue(hasattr(w, "splash_template"))
        self.assertTrue(hasattr(w, "splash_font_size"))
        self.assertTrue(hasattr(w, "splash_line_spacing"))
        self.assertTrue(hasattr(w, "splash_alignment"))
        self.assertTrue(hasattr(w, "splash_status"))
        self.assertTrue(hasattr(w, "actions_model"))
        self.assertTrue(hasattr(w, "connect_on_start"))
        self.assertTrue(hasattr(w, "start_minimized"))
        self.assertTrue(hasattr(w, "msg_timer_running"))
        self.assertTrue(hasattr(w, "wd_timer_running"))

    def test_window_has_expected_callbacks(self):
        slint_file = os.path.join(
            os.path.dirname(__file__), "..", "gui_slint", "main_window.slint"
        )
        slint_file = os.path.abspath(slint_file)
        comps = slint.load_file(slint_file)
        w = comps.MainWindow()

        self.assertTrue(callable(getattr(w, "toggle_connection", None)))
        self.assertTrue(callable(getattr(w, "refresh_ports", None)))
        self.assertTrue(callable(getattr(w, "key_clicked", None)))
        self.assertTrue(callable(getattr(w, "save_action", None)))
        self.assertTrue(callable(getattr(w, "test_action", None)))
        self.assertTrue(callable(getattr(w, "clear_action", None)))
        self.assertTrue(callable(getattr(w, "insert_key", None)))
        self.assertTrue(callable(getattr(w, "browse_display_image", None)))
        self.assertTrue(callable(getattr(w, "load_splash_image", None)))
        self.assertTrue(callable(getattr(w, "splash_save", None)))
        self.assertTrue(callable(getattr(w, "splash_preview", None)))
        self.assertTrue(callable(getattr(w, "poll_messages", None)))
        self.assertTrue(callable(getattr(w, "watchdog_tick", None)))


class SlintKeypadAppInitTests(unittest.TestCase):
    def test_app_construction_no_serial(self):
        from app.gui_slint import SlintKeypadApp

        with tempfile.TemporaryDirectory() as tmp:
            actions_path = str(Path(tmp) / "actions.json")

            app = SlintKeypadApp(
                initial_port="COM99",
                actions_path=actions_path,
            )

            self.assertIsNotNone(app._w)
            self.assertIsNotNone(app._engine)
            self.assertIsNotNone(app._engine.client)
            self.assertIsNotNone(app._engine.action_store)
            self.assertIsNotNone(app._engine.action_runner)

    def test_app_settings_widgets_populated(self):
        from app.gui_slint import SlintKeypadApp

        with tempfile.TemporaryDirectory() as tmp:
            actions_path = str(Path(tmp) / "actions.json")

            app = SlintKeypadApp(
                initial_port="COM99",
                actions_path=actions_path,
            )

            self.assertEqual(app._w.action_event, "Key 1 press")
            self.assertEqual(app._w.volume_value, "volume_up")
            self.assertEqual(app._w.media_value, "media_play_pause")
            self.assertIn(app._w.splash_mode, ("static", "text", "clock", "custom"))


if __name__ == "__main__":
    unittest.main()
