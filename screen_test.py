from machine import I2C, Pin
from time import sleep_ms
import framebuf


# Raspberry Pi Pico + OLED 0.91" 128x32 SSD1306:
#   GP20 -> SDA
#   GP21 -> SCL
#   3V3  -> VCC
#   GND  -> GND
I2C_ID = 0
SDA_PIN = 20
SCL_PIN = 21
WIDTH = 128
HEIGHT = 32


class SSD1306_I2C(framebuf.FrameBuffer):
    def __init__(self, width, height, i2c, addr=0x3C):
        self.width = width
        self.height = height
        self.i2c = i2c
        self.addr = addr
        self.pages = self.height // 8
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self.init_display()

    def write_cmd(self, cmd):
        self.i2c.writeto(self.addr, bytearray([0x80, cmd]))

    def write_data(self, data):
        packet = bytearray(1 + len(data))
        packet[0] = 0x40
        packet[1:] = data
        self.i2c.writeto(self.addr, packet)

    def init_display(self):
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
        self.write_cmd(0x21)  # column address
        self.write_cmd(0)
        self.write_cmd(self.width - 1)
        self.write_cmd(0x22)  # page address
        self.write_cmd(0)
        self.write_cmd(self.pages - 1)
        self.write_data(self.buffer)


def scan_i2c(i2c):
    addresses = i2c.scan()
    print("Znalezione adresy I2C:", [hex(addr) for addr in addresses])
    return addresses


def draw_static_test(display, address):
    display.fill(0)
    display.rect(0, 0, WIDTH, HEIGHT, 1)
    display.text("Pico SSD1306", 8, 6, 1)
    display.text("I2C " + hex(address), 8, 18, 1)
    display.show()


def animate(display, address):
    x = 0
    direction = 1

    while True:
        display.fill(0)
        display.rect(0, 0, WIDTH, HEIGHT, 1)
        display.text("SSD1306 128x32", 8, 4, 1)
        display.text("GP14/15 " + hex(address), 8, 14, 1)
        display.fill_rect(8 + x, 26, 16, 4, 1)
        display.show()

        x += direction * 4
        if x <= 0 or x >= 96:
            direction *= -1

        sleep_ms(80)


def main():
    i2c = I2C(I2C_ID, sda=Pin(SDA_PIN), scl=Pin(SCL_PIN), freq=400000)
    addresses = scan_i2c(i2c)

    if not addresses:
        print("Brak urzadzen I2C.")
        print(f"Sprawdz podlaczenie: VCC=3V3, GND=GND, SDA={SDA_PIN}, SCL={SCL_PIN}.")
        while True:
            sleep_ms(1000)

    address = 0x3C if 0x3C in addresses else addresses[0]
    display = SSD1306_I2C(WIDTH, HEIGHT, i2c, address)
    draw_static_test(display, address)
    sleep_ms(1000)
    animate(display, address)


main()
