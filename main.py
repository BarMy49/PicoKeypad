from machine import I2C, Pin, PWM
from time import sleep_ms, ticks_diff, ticks_ms
import framebuf


I2C_ID = 0
SDA_PIN = 20
SCL_PIN = 21
WIDTH = 128
HEIGHT = 32

LED_PIN = 6
SWITCH_PIN = 9
SWITCH_PULL = Pin.PULL_UP
PRESSED_LEVEL = 0

DEBOUNCE_MS = 25
FRAME_MS = 35
LONG_PRESS_MS = 700
PWM_FREQ = 1000
MAX_DUTY = 65535

GAMES = (
    ("HORSE RUN", "games.horse_run"),
    ("CUBE ESCAPE", "games.cube_escape"),
)


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


class Button:
    def __init__(self, pin):
        self.pin = pin
        self.last_read = self.is_down()
        self.stable = self.last_read
        self.last_change = ticks_ms()
        self.press_start = None
        self.long_sent = False

    def is_down(self):
        return self.pin.value() == PRESSED_LEVEL

    def update(self, now):
        current = self.is_down()

        if current != self.last_read:
            self.last_read = current
            self.last_change = now

        if ticks_diff(now, self.last_change) < DEBOUNCE_MS:
            return None

        if current != self.stable:
            self.stable = current
            if self.stable:
                self.press_start = now
                self.long_sent = False
            else:
                if self.press_start is None:
                    return None
                duration = ticks_diff(now, self.press_start)
                self.press_start = None
                if not self.long_sent and duration < LONG_PRESS_MS:
                    return "short"
            return None

        if self.stable and not self.long_sent and self.press_start is not None:
            if ticks_diff(now, self.press_start) >= LONG_PRESS_MS:
                self.long_sent = True
                return "long"

        return None

    def held_ms(self, now):
        if self.stable and self.press_start is not None:
            return ticks_diff(now, self.press_start)
        return 0


def scan_i2c(i2c):
    addresses = i2c.scan()
    print("Znalezione adresy I2C:", [hex(addr) for addr in addresses])
    return addresses


def init_display():
    i2c = I2C(I2C_ID, sda=Pin(SDA_PIN), scl=Pin(SCL_PIN), freq=400000)
    addresses = scan_i2c(i2c)

    if not addresses:
        print("Brak urzadzen I2C.")
        print("Sprawdz podlaczenie: VCC=3V3, GND=GND, SDA=GP20, SCL=GP21.")
        while True:
            sleep_ms(1000)

    address = 0x3C if 0x3C in addresses else addresses[0]
    return SSD1306_I2C(WIDTH, HEIGHT, i2c, address)


def draw_menu(display, selected, frame):
    display.fill(0)
    display.text("GAME MENU", 28, 2, 1)
    display.text(">" + GAMES[selected][0], 10, 13, 1)
    display.text("TAP=NEXT HOLD=GO", 0, 24, 1)

    if (frame // 8) & 1:
        display.pixel(120, 14, 1)
        display.pixel(121, 15, 1)
        display.pixel(120, 16, 1)

    display.show()


def draw_launching(display, selected):
    display.fill(0)
    display.text("STARTING", 32, 7, 1)
    display.text(GAMES[selected][0], 18, 20, 1)
    display.show()


def update_led(led, now, held_ms):
    if held_ms > 0:
        duty = min(MAX_DUTY, (MAX_DUTY * held_ms) // LONG_PRESS_MS)
        led.duty_u16(duty)
        return

    phase = (now // 12) % 200
    if phase > 100:
        phase = 200 - phase
    led.duty_u16(phase * 160)


def launch_game(display, led, selected):
    module_name = GAMES[selected][1]
    draw_launching(display, selected)
    led.duty_u16(0)
    sleep_ms(250)

    game = __import__(module_name, None, None, ("main",))
    game.main()


def main():
    display = init_display()

    led = PWM(Pin(LED_PIN, Pin.OUT))
    led.freq(PWM_FREQ)
    led.duty_u16(0)

    button = Button(Pin(SWITCH_PIN, Pin.IN, SWITCH_PULL))
    selected = 0
    frame = 0

    try:
        while True:
            frame_start = ticks_ms()
            event = button.update(frame_start)

            if event == "short":
                selected = (selected + 1) % len(GAMES)
            elif event == "long":
                launch_game(display, led, selected)

            draw_menu(display, selected, frame)
            update_led(led, frame_start, button.held_ms(frame_start))
            frame += 1

            elapsed = ticks_diff(ticks_ms(), frame_start)
            if elapsed < FRAME_MS:
                sleep_ms(FRAME_MS - elapsed)
    finally:
        led.duty_u16(0)


main()
