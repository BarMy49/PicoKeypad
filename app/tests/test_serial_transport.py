import unittest
from dataclasses import asdict

from app.core.protocol import DISPLAY_BUFFER_SIZE
from app.core.serial_transport import (
    PICO_HINTS,
    PicoKeypadClient,
    SerialConnectionError,
    SerialPortInfo,
)


class SerialPortInfoTests(unittest.TestCase):
    def test_construct_and_label_with_description(self):
        info = SerialPortInfo(device="COM3", description="USB Serial", hwid="USB\\VID_1234")
        self.assertEqual(info.device, "COM3")
        self.assertEqual(info.label, "COM3 - USB Serial")

    def test_label_without_description(self):
        info = SerialPortInfo(device="COM5", description="", hwid="")
        self.assertEqual(info.label, "COM5")

    def test_immutable(self):
        info = SerialPortInfo(device="COM3", description="", hwid="")
        with self.assertRaises(Exception):
            info.device = "COM4"

    def test_asdict(self):
        info = SerialPortInfo(device="COM3", description="test", hwid="hw")
        d = asdict(info)
        self.assertEqual(d["device"], "COM3")


class PicoKeypadClientConstructionTests(unittest.TestCase):
    def test_default_construction(self):
        client = PicoKeypadClient()
        self.assertIsNone(client.port)
        self.assertEqual(client.baudrate, 115200)
        self.assertFalse(client.is_open)

    def test_construction_with_port(self):
        client = PicoKeypadClient(port="COM5")
        self.assertEqual(client.port, "COM5")

    def test_custom_baudrate(self):
        client = PicoKeypadClient(baudrate=9600)
        self.assertEqual(client.baudrate, 9600)


class PicoKeypadClientWithoutSerialTests(unittest.TestCase):
    def test_open_without_port_raises(self):
        client = PicoKeypadClient()
        with self.assertRaises(SerialConnectionError):
            client.open()

    def test_send_when_closed_raises(self):
        client = PicoKeypadClient()
        with self.assertRaises(SerialConnectionError):
            client.read_message()

    def test_send_disconnect_when_closed_noop(self):
        client = PicoKeypadClient()
        client.send_disconnect()


class PicoHintsTests(unittest.TestCase):
    def test_hints_contain_expected(self):
        self.assertIn("pico", PICO_HINTS)
        self.assertIn("rp2040", PICO_HINTS)
        self.assertIn("micropython", PICO_HINTS)


class SerialConnectionErrorTests(unittest.TestCase):
    def test_is_runtime_error(self):
        err = SerialConnectionError("test")
        self.assertIsInstance(err, RuntimeError)

    def test_message(self):
        err = SerialConnectionError("something went wrong")
        self.assertEqual(str(err), "something went wrong")


if __name__ == "__main__":
    unittest.main()
