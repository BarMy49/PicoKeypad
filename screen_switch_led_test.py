from machine import I2C, Pin, PWM
from time import sleep_ms, ticks_diff, ticks_ms
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

LED_PIN = 6
SWITCH_PIN = 9

# Default wiring: switch connects GP9 to GND when pressed.
# If your switch connects GP9 to 3V3 instead, use Pin.PULL_DOWN and level 1.
SWITCH_PULL = Pin.PULL_UP
PRESSED_LEVEL = 0

DEBOUNCE_MS = 40
LED_EFFECT_MS = 250
PWM_FREQ = 1000
MAX_DUTY = 65535


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


def draw_status(display, address, click_count, led_active):
    display.fill(0)
    display.rect(0, 0, WIDTH, HEIGHT, 1)
    display.text("Switch + LED", 8, 4, 1)
    display.text("Clicks: " + str(click_count), 8, 14, 1)
    display.text(("LED fade " if led_active else "Ready    ") + hex(address), 8, 24, 1)
    display.show()


def switch_pressed(switch):
    return switch.value() == PRESSED_LEVEL


def led_duty(elapsed_ms):
    if elapsed_ms < 0 or elapsed_ms > LED_EFFECT_MS:
        return 0

    half = LED_EFFECT_MS // 2
    if elapsed_ms <= half:
        return (MAX_DUTY * elapsed_ms) // half

    return (MAX_DUTY * (LED_EFFECT_MS - elapsed_ms)) // (LED_EFFECT_MS - half)


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

    led = PWM(Pin(LED_PIN, Pin.OUT))
    led.freq(PWM_FREQ)
    led.duty_u16(0)

    switch = Pin(SWITCH_PIN, Pin.IN, SWITCH_PULL)
    clicks = 0
    effect_start = None

    last_read = switch_pressed(switch)
    stable_state = last_read
    last_change = ticks_ms()

    draw_status(display, address, clicks, False)

    try:
        while True:
            now = ticks_ms()
            current = switch_pressed(switch)

            if current != last_read:
                last_read = current
                last_change = now

            if ticks_diff(now, last_change) >= DEBOUNCE_MS and current != stable_state:
                stable_state = current
                if stable_state:
                    clicks += 1
                    effect_start = now
                    draw_status(display, address, clicks, True)

            if effect_start is None:
                led.duty_u16(0)
            else:
                elapsed = ticks_diff(now, effect_start)
                if elapsed <= LED_EFFECT_MS:
                    led.duty_u16(led_duty(elapsed))
                else:
                    effect_start = None
                    led.duty_u16(0)
                    draw_status(display, address, clicks, False)

            sleep_ms(10)
    finally:
        led.duty_u16(0)


main()
