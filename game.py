from machine import I2C, Pin, PWM
from time import sleep_ms, ticks_diff, ticks_ms
import framebuf


# Raspberry Pi Pico + OLED 0.91" 128x32 SSD1306.
# Uses the same pins as screen_test.py.
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

DEBOUNCE_MS = 35
FRAME_MS = 35
PWM_FREQ = 1000
MAX_DUTY = 65535

GROUND_Y = 29
HORSE_X = 14
HORSE_W = 16
HORSE_H = 11
HORSE_GROUND_TOP = GROUND_Y - HORSE_H
JUMP_SPEED = -43
GRAVITY = 6

TREE = 0
BIRD = 1

TITLE = 0
RUNNING = 1
GAME_OVER = 2


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

    def is_down(self):
        return self.pin.value() == PRESSED_LEVEL

    def pressed(self, now):
        current = self.is_down()

        if current != self.last_read:
            self.last_read = current
            self.last_change = now

        if ticks_diff(now, self.last_change) < DEBOUNCE_MS:
            return False

        if current == self.stable:
            return False

        self.stable = current
        return self.stable


def scan_i2c(i2c):
    addresses = i2c.scan()
    print("Znalezione adresy I2C:", [hex(addr) for addr in addresses])
    return addresses


def rects_overlap(ax, ay, aw, ah, bx, by, bw, bh):
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def safe_pixel(display, x, y, color=1):
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        display.pixel(x, y, color)


def next_seed(seed):
    return (seed * 1103515245 + 12345) & 0x7FFFFFFF


def new_obstacle(seed, score):
    seed = next_seed(seed)
    gap = 38 + ((seed >> 8) % 42)
    x10 = (WIDTH + gap) * 10

    if score > 18 and (seed & 3) == 0:
        y = 8 + ((seed >> 4) & 1)
        return seed, BIRD, x10, y, 11, 6

    height = 9 + (((seed >> 5) % 3) * 2)
    return seed, TREE, x10, GROUND_Y - height, 8, height


def draw_horse(display, x, y, step):
    display.fill_rect(x + 3, y + 4, 8, 4, 1)   # body
    display.fill_rect(x + 10, y + 2, 3, 5, 1)  # neck
    display.fill_rect(x + 12, y + 1, 4, 3, 1)  # head
    display.pixel(x + 14, y, 1)                # ear
    display.pixel(x + 15, y + 2, 0)            # eye
    display.line(x + 3, y + 5, x, y + 3, 1)    # tail

    if step:
        display.line(x + 4, y + 8, x + 3, y + 10, 1)
        display.line(x + 6, y + 8, x + 7, y + 10, 1)
        display.line(x + 9, y + 8, x + 8, y + 10, 1)
        display.line(x + 11, y + 7, x + 12, y + 10, 1)
    else:
        display.line(x + 4, y + 8, x + 5, y + 10, 1)
        display.line(x + 6, y + 8, x + 5, y + 10, 1)
        display.line(x + 9, y + 8, x + 10, y + 10, 1)
        display.line(x + 11, y + 7, x + 10, y + 10, 1)


def draw_tree(display, x, y, width, height):
    trunk_x = x + width // 2
    display.fill_rect(trunk_x - 1, y, 3, height, 1)
    display.line(trunk_x - 1, y + 4, x, y + 2, 1)
    display.line(trunk_x + 1, y + 6, x + width - 1, y + 3, 1)
    display.line(trunk_x - 1, y + 8, x + 1, y + 7, 1)


def draw_bird(display, x, y, wing_up):
    safe_pixel(display, x + 5, y + 2)
    safe_pixel(display, x + 6, y + 2)
    safe_pixel(display, x + 7, y + 2)
    safe_pixel(display, x + 8, y + 1)

    if wing_up:
        display.line(x, y + 3, x + 5, y, 1)
        display.line(x + 6, y, x + 10, y + 3, 1)
    else:
        display.line(x, y, x + 5, y + 4, 1)
        display.line(x + 6, y + 4, x + 10, y, 1)


def draw_ground(display, distance10):
    display.hline(0, GROUND_Y + 1, WIDTH, 1)
    offset = (distance10 // 10) % 16
    for x in range(-offset, WIDTH, 16):
        safe_pixel(display, x, GROUND_Y)
        safe_pixel(display, x + 9, GROUND_Y - 1)


def draw_obstacle(display, kind, x, y, width, height, frame):
    if kind == TREE:
        draw_tree(display, x, y, width, height)
    else:
        draw_bird(display, x, y, (frame // 4) & 1)


def draw_title(display, frame):
    display.fill(0)
    display.text("HORSE RUN", 28, 2, 1)
    display.text("PRESS BUTTON", 28, 13, 1)
    draw_ground(display, frame * 4)
    draw_horse(display, HORSE_X, HORSE_GROUND_TOP, (frame // 6) & 1)
    draw_tree(display, 98, GROUND_Y - 11, 8, 11)
    draw_bird(display, 113, 8, (frame // 6) & 1)
    display.show()


def draw_game(display, horse_y, kind, obs_x10, obs_y, obs_w, obs_h, score,
              distance10, frame):
    display.fill(0)
    score_text = str(score)
    display.text(score_text, WIDTH - len(score_text) * 8, 0, 1)
    draw_ground(display, distance10)
    draw_horse(display, HORSE_X, horse_y, (frame // 3) & 1)
    draw_obstacle(display, kind, obs_x10 // 10, obs_y, obs_w, obs_h, frame)
    display.show()


def draw_game_over(display, score, high_score):
    display.fill(0)
    display.text("GAME OVER", 28, 2, 1)
    display.text("S:" + str(score), 12, 14, 1)
    display.text("H:" + str(high_score), 72, 14, 1)
    display.text("PRESS BUTTON", 28, 24, 1)
    display.show()


def update_led(led, state, now, horse_y):
    if state == TITLE:
        phase = (now // 12) % 200
        if phase > 100:
            phase = 200 - phase
        led.duty_u16(phase * 180)
    elif state == RUNNING:
        jump_height = HORSE_GROUND_TOP - horse_y
        if jump_height > 0:
            led.duty_u16(min(MAX_DUTY, 6000 + jump_height * 3600))
        else:
            led.duty_u16(0)
    else:
        led.duty_u16(MAX_DUTY if (now // 140) & 1 else 0)


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


def main():
    display = init_display()

    led = PWM(Pin(LED_PIN, Pin.OUT))
    led.freq(PWM_FREQ)
    led.duty_u16(0)

    button = Button(Pin(SWITCH_PIN, Pin.IN, SWITCH_PULL))

    state = TITLE
    high_score = 0
    score = 0
    distance10 = 0
    frame = 0
    seed = ticks_ms() & 0x7FFFFFFF

    horse_y10 = HORSE_GROUND_TOP * 10
    horse_vy10 = 0
    seed, obs_kind, obs_x10, obs_y, obs_w, obs_h = new_obstacle(seed, score)

    try:
        while True:
            frame_start = ticks_ms()
            pressed = button.pressed(frame_start)
            horse_y = horse_y10 // 10

            if state == TITLE:
                if pressed:
                    state = RUNNING
                    score = 0
                    distance10 = 0
                    horse_y10 = HORSE_GROUND_TOP * 10
                    horse_vy10 = 0
                    seed, obs_kind, obs_x10, obs_y, obs_w, obs_h = new_obstacle(seed, score)
                draw_title(display, frame)

            elif state == RUNNING:
                on_ground = horse_y10 >= HORSE_GROUND_TOP * 10

                if pressed and on_ground:
                    horse_vy10 = JUMP_SPEED

                horse_y10 += horse_vy10
                horse_vy10 += GRAVITY

                if horse_y10 >= HORSE_GROUND_TOP * 10:
                    horse_y10 = HORSE_GROUND_TOP * 10
                    horse_vy10 = 0

                speed10 = 22 + min(score // 18, 16)
                distance10 += speed10
                score = distance10 // 100
                obs_x10 -= speed10

                if obs_x10 // 10 < -obs_w:
                    seed, obs_kind, obs_x10, obs_y, obs_w, obs_h = new_obstacle(seed, score)

                horse_y = horse_y10 // 10
                horse_box_x = HORSE_X + 2
                horse_box_y = horse_y + 2
                horse_box_w = HORSE_W - 4
                horse_box_h = HORSE_H - 3

                if rects_overlap(
                    horse_box_x, horse_box_y, horse_box_w, horse_box_h,
                    obs_x10 // 10, obs_y, obs_w, obs_h,
                ):
                    state = GAME_OVER
                    if score > high_score:
                        high_score = score
                    draw_game_over(display, score, high_score)
                else:
                    draw_game(
                        display, horse_y, obs_kind, obs_x10, obs_y, obs_w,
                        obs_h, score, distance10, frame,
                    )

            else:
                if pressed:
                    state = TITLE
                    draw_title(display, frame)
                else:
                    draw_game_over(display, score, high_score)

            update_led(led, state, frame_start, horse_y10 // 10)
            frame += 1

            elapsed = ticks_diff(ticks_ms(), frame_start)
            if elapsed < FRAME_MS:
                sleep_ms(FRAME_MS - elapsed)
    finally:
        led.duty_u16(0)


main()
