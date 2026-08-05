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
        self.assertTrue(hasattr(w, "binding_event"))
        self.assertTrue(hasattr(w, "binding_kind"))
        self.assertTrue(hasattr(w, "display_event"))
        self.assertTrue(hasattr(w, "display_kind"))
        self.assertTrue(hasattr(w, "image_invert"))
        self.assertTrue(hasattr(w, "msg_timer_running"))
        self.assertTrue(hasattr(w, "wd_timer_running"))

    def test_window_has_expected_callbacks(self):
        slint_file = os.path.join(
            os.path.dirname(__file__), "..", "gui_slint", "main_window.slint"
        )
        slint_file = os.path.abspath(slint_file)
        comps = slint.load_file(slint_file)
        w = comps.MainWindow()

        self.assertTrue(callable(getattr(w, "connect_clicked", None)))
        self.assertTrue(callable(getattr(w, "disconnect_clicked", None)))
        self.assertTrue(callable(getattr(w, "refresh_ports", None)))
        self.assertTrue(callable(getattr(w, "key_clicked", None)))
        self.assertTrue(callable(getattr(w, "send_text", None)))
        self.assertTrue(callable(getattr(w, "load_image", None)))
        self.assertTrue(callable(getattr(w, "save_binding", None)))
        self.assertTrue(callable(getattr(w, "save_display_rule", None)))
        self.assertTrue(callable(getattr(w, "poll_messages", None)))
        self.assertTrue(callable(getattr(w, "watchdog_tick", None)))


class SlintKeypadAppInitTests(unittest.TestCase):
    def test_app_construction_no_serial(self):
        from app.gui_slint import SlintKeypadApp

        with tempfile.TemporaryDirectory() as tmp:
            bindings_path = str(Path(tmp) / "bindings.json")
            rules_path = str(Path(tmp) / "rules.json")

            app = SlintKeypadApp(
                initial_port="COM99",
                bindings_path=bindings_path,
                display_rules_path=rules_path,
            )

            self.assertIsNotNone(app._w)
            self.assertIsNotNone(app.client)
            self.assertIsNotNone(app.binding_store)
            self.assertIsNotNone(app.display_store)
            self.assertIsNotNone(app.action_runner)
            self.assertFalse(app._dev_busy)


if __name__ == "__main__":
    unittest.main()
