from machine import I2C, Pin
from time import sleep_ms
import framebuf

import config

try:
    from machine import SoftI2C
except ImportError:
    SoftI2C = None


class SSD1306_I2C(framebuf.FrameBuffer):
    def __init__(
        self,
        width,
        height,
        i2c,
        addr=config.DISPLAY_ADDR,
        bus_kind="hardware",
        freq=config.I2C_FREQ,
        data_chunk=config.I2C_DATA_CHUNK,
    ):
        self.width = width
        self.height = height
        self.i2c = i2c
        self.addr = addr
        self.bus_kind = bus_kind
        self.freq = freq
        self.data_chunk = data_chunk
        self.pages = self.height // 8
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self.init_display()

    def write_cmd(self, cmd):
        self.write_with_retry(bytearray([0x80, cmd]))

    def write_data(self, data):
        for offset in range(0, len(data), self.data_chunk):
            chunk = data[offset:offset + self.data_chunk]
            packet = bytearray(1 + len(chunk))
            packet[0] = 0x40
            packet[1:] = chunk
            self.write_with_retry(packet)

    def write_with_retry(self, packet):
        last_error = None

        for attempt in range(config.I2C_RETRIES):
            try:
                self.i2c.writeto(self.addr, packet)
                return
            except OSError as exc:
                last_error = exc
                if attempt < config.I2C_RETRIES - 1:
                    recover_i2c_bus(self.i2c)
                    self.i2c = make_i2c(self.bus_kind, self.freq)
                    sleep_ms(config.I2C_RETRY_DELAY_MS)

        recover_i2c_bus(self.i2c)
        raise last_error

    def configure_bus(self, freq, data_chunk):
        if self.i2c is not None and hasattr(self.i2c, "deinit"):
            try:
                self.i2c.deinit()
            except Exception:
                pass

        self.freq = freq
        self.data_chunk = data_chunk
        self.i2c = make_i2c(self.bus_kind, self.freq)

    def init_display(self):
        sleep_ms(100)
        for cmd in (
            0xAE,        # display off
            0x20, 0x00,  # horizontal addressing mode
            0x40,        # display start line 0
            0xA1,        # segment remap
            0xA8, 0x1F,  # multiplex ratio for 128x32
            0xC8,        # COM output scan direction
            0xD3, 0x00,  # display offset
            0xDA, 0x02,  # COM pins for 128x32
            0xD5, 0x80,  # display clock
            0xD9, 0xF1,  # pre-charge
            0xDB, 0x30,  # VCOM detect
            0x81, 0x7F,  # contrast
            0xA4,        # output follows RAM
            0xA6,        # normal display
            0x8D, 0x14,  # charge pump on
            0xAF,        # display on
        ):
            self.write_cmd(cmd)

        self.fill(0)
        self.show()

    def show(self):
        self.write_cmd(0x21)
        self.write_cmd(0)
        self.write_cmd(self.width - 1)
        self.write_cmd(0x22)
        self.write_cmd(0)
        self.write_cmd(self.pages - 1)
        self.write_data(self.buffer)


def make_i2c(kind="hardware", freq=None):
    if freq is None:
        freq = config.I2C_FREQ

    if kind == "soft":
        if SoftI2C is None:
            raise RuntimeError("SoftI2C is not available")
        return SoftI2C(
            sda=Pin(config.SDA_PIN),
            scl=Pin(config.SCL_PIN),
            freq=freq,
        )

    return I2C(
        config.I2C_ID,
        sda=Pin(config.SDA_PIN),
        scl=Pin(config.SCL_PIN),
        freq=freq,
    )


def recover_i2c_bus(i2c=None):
    if i2c is not None and hasattr(i2c, "deinit"):
        try:
            i2c.deinit()
        except Exception:
            pass

    sda = Pin(config.SDA_PIN, Pin.IN, Pin.PULL_UP)
    scl = Pin(config.SCL_PIN, Pin.IN, Pin.PULL_UP)
    sleep_ms(2)

    for _ in range(config.I2C_RECOVERY_CLOCKS):
        if sda.value():
            break
        scl = Pin(config.SCL_PIN, Pin.OUT, value=0)
        sleep_ms(config.I2C_RECOVERY_PULSE_MS)
        scl = Pin(config.SCL_PIN, Pin.IN, Pin.PULL_UP)
        sleep_ms(config.I2C_RECOVERY_PULSE_MS)

    sda = Pin(config.SDA_PIN, Pin.OUT, value=0)
    sleep_ms(config.I2C_RECOVERY_PULSE_MS)
    scl = Pin(config.SCL_PIN, Pin.IN, Pin.PULL_UP)
    sleep_ms(config.I2C_RECOVERY_PULSE_MS)
    sda = Pin(config.SDA_PIN, Pin.IN, Pin.PULL_UP)
    sleep_ms(2)

    return sda.value() and scl.value()


def scan_i2c(kind, freq=None):
    last_error = None
    i2c = None

    for attempt in range(config.I2C_SCAN_RETRIES):
        if attempt:
            recover_i2c_bus(i2c)
            sleep_ms(config.I2C_RETRY_DELAY_MS)

        try:
            i2c = make_i2c(kind, freq)
            addresses = i2c.scan()
            if addresses:
                return i2c, addresses
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        recover_i2c_bus(i2c)
        raise last_error

    recover_i2c_bus(i2c)
    return make_i2c(kind, freq), []


def init_display(i2c=None, freq=None, data_chunk=None):
    if freq is None:
        freq = config.I2C_FREQ
    if data_chunk is None:
        data_chunk = config.I2C_DATA_CHUNK

    sleep_ms(config.I2C_POWERUP_MS)
    recover_i2c_bus(i2c)

    kinds = ("hardware", "soft") if SoftI2C is not None else ("hardware",)
    last_error = None

    for kind in kinds:
        try:
            i2c, addresses = scan_i2c(kind, freq)
            if not addresses:
                continue

            address = config.DISPLAY_ADDR if config.DISPLAY_ADDR in addresses else addresses[0]
            return SSD1306_I2C(
                config.WIDTH,
                config.HEIGHT,
                i2c,
                address,
                kind,
                freq,
                data_chunk,
            ), None
        except Exception as exc:
            last_error = exc

    if last_error is not None:
        return None, str(last_error)

    return None, "no I2C display found"
