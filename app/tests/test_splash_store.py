from pathlib import Path
import tempfile
import unittest

from app.core import protocol
from app.core.splash_store import load_splash_binary, save_splash_binary


class SplashStoreTests(unittest.TestCase):
    def test_save_and_load_roundtrip(self):
        buffer = bytes([i % 256 for i in range(protocol.DISPLAY_BUFFER_SIZE)])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "splash.bin"
            saved_path = save_splash_binary(path, buffer)
            self.assertEqual(saved_path, path)
            self.assertEqual(load_splash_binary(path), buffer)

    def test_rejects_wrong_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "splash.bin"
            with self.assertRaises(ValueError):
                save_splash_binary(path, b"123")

    def test_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.bin"
            self.assertIsNone(load_splash_binary(path))


if __name__ == "__main__":
    unittest.main()

