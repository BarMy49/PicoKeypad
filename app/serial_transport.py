import threading
from dataclasses import dataclass
from typing import Any

from . import protocol

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    serial = None
    list_ports = None


PICO_HINTS = (
    "pico",
    "rp2040",
    "micropython",
    "circuitpython",
    "usb serial device",
)


class SerialConnectionError(RuntimeError):
    pass


@dataclass(frozen=True)
class SerialPortInfo:
    device: str
    description: str
    hwid: str

    @property
    def label(self) -> str:
        if self.description:
            return f"{self.device} - {self.description}"
        return self.device


class PicoKeypadClient:
    def __init__(self, port: str | None = None, baudrate: int = 115200, timeout: float = 0.1):
        self.port = port
        self.baudrate = baudrate
        self.timeout = timeout
        self._serial = None
        self._write_lock = threading.Lock()

    @staticmethod
    def _require_pyserial() -> None:
        if serial is None or list_ports is None:
            raise SerialConnectionError(
                "pyserial is required. Install it with: python -m pip install pyserial"
            )

    @classmethod
    def list_ports(cls) -> list[SerialPortInfo]:
        cls._require_pyserial()
        return [
            SerialPortInfo(port.device, port.description or "", port.hwid or "")
            for port in list_ports.comports()
        ]

    @classmethod
    def autodetect_port(cls) -> str | None:
        ports = cls.list_ports()

        for port in ports:
            haystack = f"{port.device} {port.description} {port.hwid}".lower()
            if any(hint in haystack for hint in PICO_HINTS):
                return port.device

        if len(ports) == 1:
            return ports[0].device

        return None

    @property
    def is_open(self) -> bool:
        return bool(self._serial and self._serial.is_open)

    def open(self, port: str | None = None) -> None:
        self._require_pyserial()
        selected_port = port or self.port or self.autodetect_port()
        if not selected_port:
            raise SerialConnectionError("No serial port selected and autodetect found nothing")

        self.close()
        self._serial = serial.Serial(
            selected_port,
            self.baudrate,
            timeout=self.timeout,
            write_timeout=1,
        )
        self.port = selected_port
        self._serial.reset_input_buffer()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            finally:
                self._serial = None

    def _ensure_open(self) -> None:
        if not self.is_open:
            raise SerialConnectionError("Serial port is not open")

    def read_message(self) -> dict[str, Any] | None:
        self._ensure_open()
        line = self._serial.readline()
        if not line:
            return None
        return protocol.parse_serial_line(line)

    def send(self, message: dict[str, Any]) -> None:
        self._ensure_open()
        payload = protocol.encode_command(message)
        with self._write_lock:
            self._serial.write(payload)
            self._serial.flush()

    def ping(self) -> None:
        self.send(protocol.build_ping())

    def send_clear(self, color: int = 0) -> None:
        self.send(protocol.build_display_clear(color))

    def send_text(self, lines: list[str], clear: bool = True) -> None:
        self.send(protocol.build_display_text(lines, clear))

    def send_image(self, buffer: bytes) -> None:
        self.send(protocol.build_display_image(buffer))

