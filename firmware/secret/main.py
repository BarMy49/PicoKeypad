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
    ("HORSE RUN", "horse_run"),
    ("CUBE ESCAPE", "cube_escape"),
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


class NullLed:
    def duty_u16(self, value):
        pass


class MatrixActionButton:
    def __init__(self, controls):
        self.controls = controls

    def pressed(self, now):
        return self.controls.action_pressed(now)

    def held(self):
        return self.controls.action_held(ticks_ms())

    def close(self):
        pass


class MatrixSecretControls:
    def __init__(self, keyboard, exit_key, action_key, hold_ms):
        self.keyboard = keyboard
        self.exit_key = exit_key
        self.action_key = action_key
        self.hold_ms = hold_ms
        self.last_poll = None
        self.exit_press_start = None
        self.exit_long_sent = False
        self.exit_latched = False
        self.exit_consumed = False
        self.exit_short_latched = False
        self.action_latched = False
        self.action_down = self._key_down(action_key)
        self.exit_armed = not self._key_down(exit_key)

    def _key_down(self, key):
        index = key - 1
        return 0 <= index < len(self.keyboard.stable_state) and self.keyboard.stable_state[index]

    def poll(self, now):
        if self.last_poll == now:
            return

        self.last_poll = now

        for event in self.keyboard.poll(now):
            if event.get("type") != "key":
                continue

            key = event.get("key")
            pressed = event.get("event") == "down"

            if key == self.exit_key:
                if pressed:
                    if self.exit_armed:
                        self.exit_press_start = now
                        self.exit_long_sent = False
                else:
                    if (
                        self.exit_armed
                        and self.exit_press_start is not None
                        and not self.exit_long_sent
                    ):
                        self.exit_short_latched = True
                    self.exit_press_start = None
                    self.exit_long_sent = False
                    self.exit_armed = True
            elif key == self.action_key:
                self.action_down = pressed
                if pressed:
                    self.action_latched = True

        if (
            self.exit_armed
            and self.exit_press_start is not None
            and not self.exit_long_sent
            and ticks_diff(now, self.exit_press_start) >= self.hold_ms
        ):
            self.exit_long_sent = True
            self.exit_latched = True

    def exit_requested(self, now):
        self.poll(now)
        if not self.exit_latched:
            return False

        self.exit_latched = False
        self.exit_consumed = True
        return True

    def consume_exit_request(self):
        if not self.exit_consumed:
            return False

        self.exit_consumed = False
        return True

    def menu_event(self, now):
        self.poll(now)

        if self.action_latched:
            self.action_latched = False
            return "long"

        if self.exit_short_latched:
            self.exit_short_latched = False
            return "short"

        return None

    def action_pressed(self, now):
        self.poll(now)
        if not self.action_latched:
            return False

        self.action_latched = False
        return True

    def action_held(self, now):
        self.poll(now)
        return self.action_down

    def action_button(self):
        self.action_latched = False
        return MatrixActionButton(self)


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
    hint = "K1 NEXT K2 GO" if ((frame // 40) & 1) == 0 else "HOLD K1 EXIT"
    display.text(hint, 0, 24, 1)

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


def import_game(module_name):
    package = __name__.rsplit(".", 1)[0]
    names = []

    if package and package != "__main__":
        names.append(package + ".games." + module_name)

    names.append("games." + module_name)
    last_error = None

    for name in names:
        try:
            return __import__(name, None, None, ("main",))
        except ImportError as exc:
            last_error = exc

    raise last_error


def launch_game(display, led, selected, controls=None):
    module_name = GAMES[selected][1]
    draw_launching(display, selected)
    led.duty_u16(0)
    sleep_ms(250)

    game = import_game(module_name)

    if controls is None:
        game.main()
        return False
    else:
        game.main(
            display=display,
            button=controls.action_button(),
            exit_requested=controls.exit_requested,
            led=led,
        )
        return controls.consume_exit_request()


def main(display=None, keyboard=None, exit_key=1, action_key=2, hold_ms=LONG_PRESS_MS, led=None):
    if display is None:
        display = init_display()

    controls = None
    button = None

    if led is None:
        if keyboard is None:
            led = PWM(Pin(LED_PIN, Pin.OUT))
            led.freq(PWM_FREQ)
        else:
            led = NullLed()

    led.duty_u16(0)

    if keyboard is None:
        button = Button(Pin(SWITCH_PIN, Pin.IN, SWITCH_PULL))
    else:
        controls = MatrixSecretControls(keyboard, exit_key, action_key, hold_ms)

    selected = 0
    frame = 0

    try:
        while True:
            frame_start = ticks_ms()
            if controls is None:
                event = button.update(frame_start)
                held_ms = button.held_ms(frame_start)
            else:
                if controls.exit_requested(frame_start):
                    return
                event = controls.menu_event(frame_start)
                held_ms = 0

            if event == "short":
                selected = (selected + 1) % len(GAMES)
            elif event == "long":
                if launch_game(display, led, selected, controls):
                    return

            draw_menu(display, selected, frame)
            update_led(led, frame_start, held_ms)
            frame += 1

            elapsed = ticks_diff(ticks_ms(), frame_start)
            if elapsed < FRAME_MS:
                sleep_ms(FRAME_MS - elapsed)
    finally:
        led.duty_u16(0)


if __name__ == "__main__":
    main()
