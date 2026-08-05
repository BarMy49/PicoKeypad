import base64
import json
import unittest

from app.core import protocol


class ProtocolParseTests(unittest.TestCase):
    def test_parse_json_dict(self):
        result = protocol.parse_serial_line('{"type":"hello","display":{"ready":true}}')
        self.assertEqual(result, {"type": "hello", "display": {"ready": True}})

    def test_parse_json_list_returns_log(self):
        result = protocol.parse_serial_line("[1,2,3]")
        self.assertEqual(result["type"], "log")
        self.assertIn("[1, 2, 3]", result["message"])

    def test_parse_bytes(self):
        result = protocol.parse_serial_line(b'{"type":"key","key":1,"event":"down"}')
        self.assertEqual(result, {"type": "key", "key": 1, "event": "down"})

    def test_parse_empty_string(self):
        result = protocol.parse_serial_line("")
        self.assertEqual(result["type"], "empty")

    def test_parse_whitespace_only(self):
        result = protocol.parse_serial_line("   \n  ")
        self.assertEqual(result["type"], "empty")

    def test_parse_invalid_json_returns_log(self):
        result = protocol.parse_serial_line("not json at all")
        self.assertEqual(result, {"type": "log", "message": "not json at all"})

    def test_parse_number_returns_log(self):
        result = protocol.parse_serial_line("42")
        self.assertEqual(result, {"type": "log", "message": "42"})

    def test_parse_json_with_whitespace(self):
        result = protocol.parse_serial_line('  {"type":"ping"}  ')
        self.assertEqual(result, {"type": "ping"})


class ProtocolEncodeTests(unittest.TestCase):
    def test_encode_simple_message(self):
        result = protocol.encode_command({"type": "ping"})
        self.assertEqual(result, b'{"type":"ping"}\n')

    def test_encode_complex_message(self):
        result = protocol.encode_command({"type": "display", "lines": ["a", "b"]})
        self.assertIn(b'"type":"display"', result)
        self.assertIn(b'"lines":["a","b"]', result)


class ProtocolBuildTests(unittest.TestCase):
    def test_build_ping(self):
        self.assertEqual(protocol.build_ping(), {"type": "ping"})

    def test_build_display_clear_default(self):
        cmd = protocol.build_display_clear()
        self.assertEqual(cmd["type"], "display")
        self.assertEqual(cmd["mode"], "clear")
        self.assertEqual(cmd["color"], 0)

    def test_build_display_clear_color(self):
        cmd = protocol.build_display_clear(color=1)
        self.assertEqual(cmd["color"], 1)

    def test_build_display_text(self):
        cmd = protocol.build_display_text(["hello", "world"])
        self.assertEqual(cmd["type"], "display")
        self.assertEqual(cmd["mode"], "text")
        self.assertEqual(cmd["lines"], ["hello", "world"])
        self.assertEqual(cmd["clear"], True)

    def test_build_display_text_no_clear(self):
        cmd = protocol.build_display_text(["x"], clear=False)
        self.assertFalse(cmd["clear"])

    def test_build_display_image_valid_buffer(self):
        buf = bytes(protocol.DISPLAY_BUFFER_SIZE)
        cmd = protocol.build_display_image(buf)
        self.assertEqual(cmd["type"], "display")
        self.assertEqual(cmd["mode"], "image")
        self.assertEqual(cmd["encoding"], "base64")
        decoded = base64.b64decode(cmd["data"])
        self.assertEqual(len(decoded), protocol.DISPLAY_BUFFER_SIZE)

    def test_build_display_image_wrong_size_raises(self):
        with self.assertRaises(ValueError):
            protocol.build_display_image(b"too short")


class ProtocolConstantsTests(unittest.TestCase):
    def test_display_dimensions(self):
        self.assertEqual(protocol.DISPLAY_WIDTH, 128)
        self.assertEqual(protocol.DISPLAY_HEIGHT, 32)
        self.assertEqual(protocol.DISPLAY_BUFFER_SIZE, 512)

    def test_buffer_size_math(self):
        self.assertEqual(protocol.DISPLAY_BUFFER_SIZE, (128 * 32) // 8)


if __name__ == "__main__":
    unittest.main()
