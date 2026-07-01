from machine import I2C, Pin, PWM
from time import sleep_ms, ticks_diff, ticks_ms
import framebuf


# Raspberry Pi Pico + OLED 0.91" 128x32 SSD1306.
# Uses the same hardware pins as the other games.
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

DEBOUNCE_MS = 25
FRAME_MS = 35
PWM_FREQ = 1000
MAX_DUTY = 65535

CEILING_Y = 0
GROUND_Y = 31
MID_Y = 16

CUBE_SIZE = 8
CUBE_X = 18
TOP = 0
BOTTOM = 1

TELEPORT_MS = 220
OBSTACLE_LED_MS = 180
SCORE_DISTANCE10 = 80
BASE_SPEED10 = 20
SPEED_SCORE_STEP = 5
MAX_SPEED_BOOST10 = 24
START_SPAWN_GAP10 = 220
SPAWN_GAP_MIN10 = 120
SPAWN_GAP_RANDOM10 = 180

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
        self.press_latched = False
        self.last_press = ticks_ms() - DEBOUNCE_MS

        trigger = Pin.IRQ_FALLING if PRESSED_LEVEL == 0 else Pin.IRQ_RISING
        self.pin.irq(trigger=trigger, handler=self._irq)

    def is_down(self):
        return self.pin.value() == PRESSED_LEVEL

    def _irq(self, pin):
        now = ticks_ms()
        if self.is_down() and ticks_diff(now, self.last_press) >= DEBOUNCE_MS:
            self.last_press = now
            self.press_latched = True

    def pressed(self, now):
        if not self.press_latched:
            return False

        self.press_latched = False
        return True

    def close(self):
        self.pin.irq(handler=None)


def scan_i2c(i2c):
    addresses = i2c.scan()
    print("Znalezione adresy I2C:", [hex(addr) for addr in addresses])
    return addresses


def next_seed(seed):
    return (seed * 1103515245 + 12345) & 0x7FFFFFFF


def lane_y(lane):
    if lane == TOP:
        return CEILING_Y + 2
    return GROUND_Y - CUBE_SIZE


def safe_pixel(display, x, y, color=1):
    if 0 <= x < WIDTH and 0 <= y < HEIGHT:
        display.pixel(x, y, color)


def hline_clip(display, x, y, width, color=1):
    if y < 0 or y >= HEIGHT or width <= 0:
        return

    end = x + width
    if end <= 0 or x >= WIDTH:
        return

    if x < 0:
        x = 0
    if end > WIDTH:
        end = WIDTH

    display.hline(x, y, end - x, color)


def vline_clip(display, x, y, height, color=1):
    if x < 0 or x >= WIDTH or height <= 0:
        return

    end = y + height
    if end <= 0 or y >= HEIGHT:
        return

    if y < 0:
        y = 0
    if end > HEIGHT:
        end = HEIGHT

    display.vline(x, y, end - y, color)


def fill_rect_clip(display, x, y, width, height, color=1):
    if width <= 0 or height <= 0:
        return

    x2 = x + width
    y2 = y + height
    if x2 <= 0 or y2 <= 0 or x >= WIDTH or y >= HEIGHT:
        return

    if x < 0:
        x = 0
    if y < 0:
        y = 0
    if x2 > WIDTH:
        x2 = WIDTH
    if y2 > HEIGHT:
        y2 = HEIGHT

    display.fill_rect(x, y, x2 - x, y2 - y, color)


def rect_clip(display, x, y, width, height, color=1):
    hline_clip(display, x, y, width, color)
    hline_clip(display, x, y + height - 1, width, color)
    vline_clip(display, x, y, height, color)
    vline_clip(display, x + width - 1, y, height, color)


def rects_overlap(ax, ay, aw, ah, bx, by, bw, bh):
    return ax < bx + bw and ax + aw > bx and ay < by + bh and ay + ah > by


def divider_blocks_teleport(divider):
    if divider is None:
        return False

    x = divider[0] // 10
    width = divider[1]
    return x <= CUBE_X + CUBE_SIZE and x + width >= CUBE_X


def draw_cube(display, lane):
    y = lane_y(lane)
    fill_rect_clip(display, CUBE_X, y, CUBE_SIZE, CUBE_SIZE, 1)
    safe_pixel(display, CUBE_X + 1, y + 1, 0)
    safe_pixel(display, CUBE_X + CUBE_SIZE - 2, y + CUBE_SIZE - 2, 0)


def draw_explosion(display, lane, elapsed_ms):
    y = lane_y(lane)
    cx = CUBE_X + CUBE_SIZE // 2
    cy = y + CUBE_SIZE // 2
    radius = 1 + min(8, (elapsed_ms * 9) // TELEPORT_MS)

    safe_pixel(display, cx - radius, cy)
    safe_pixel(display, cx + radius, cy)
    safe_pixel(display, cx, cy - radius)
    safe_pixel(display, cx, cy + radius)
    safe_pixel(display, cx - radius, cy - radius // 2)
    safe_pixel(display, cx + radius, cy + radius // 2)
    safe_pixel(display, cx - radius // 2, cy + radius)
    safe_pixel(display, cx + radius // 2, cy - radius)
    rect_clip(display, cx - radius // 2, cy - radius // 2, radius + 1, radius + 1)


def draw_obstacle(display, obstacle, frame):
    x = obstacle[0] // 10
    lane = obstacle[1]
    width = obstacle[2]
    height = obstacle[3]

    if lane == TOP:
        y = CEILING_Y + 1
        fill_rect_clip(display, x, y, width, height, 1)
        safe_pixel(display, x + width // 2, y + height, frame & 1)
    else:
        y = GROUND_Y - height
        fill_rect_clip(display, x, y, width, height, 1)
        safe_pixel(display, x + width // 2, y - 1, frame & 1)


def draw_divider(display, divider, frame):
    if divider is None:
        return

    x = divider[0] // 10
    width = divider[1]
    blocked_lane = divider[2]
    block_x = x + width // 2 - 4
    block_y = lane_y(blocked_lane)

    hline_clip(display, x, MID_Y, width, 1)
    if frame & 2:
        for tick_x in range(x + 4, x + width, 12):
            vline_clip(display, tick_x, MID_Y - 1, 3, 1)

    rect_clip(display, block_x, block_y, 8, CUBE_SIZE, 1)
    fill_rect_clip(display, block_x + 2, block_y + 2, 4, CUBE_SIZE - 4, 1)


def draw_world(display, distance10, divider):
    display.fill(0)
    hline_clip(display, 0, CEILING_Y, WIDTH, 1)
    hline_clip(display, 0, GROUND_Y, WIDTH, 1)

    scroll = (distance10 // 10) % 12
    for x in range(-scroll, WIDTH, 12):
        safe_pixel(display, x, CEILING_Y + 2)
        safe_pixel(display, x + 6, GROUND_Y - 2)

    if divider is not None:
        hline_clip(display, divider[0] // 10, MID_Y, divider[1], 1)


def draw_title(display, frame):
    display.fill(0)
    display.text("CUBE ESCAPE", 20, 2, 1)
    display.text("PRESS BUTTON", 18, 13, 1)
    hline_clip(display, 0, CEILING_Y, WIDTH, 1)
    hline_clip(display, 0, GROUND_Y, WIDTH, 1)
    hline_clip(display, 76, 24, 40, 1)
    draw_cube(display, BOTTOM if (frame // 8) & 1 else TOP)
    display.show()


def draw_game(display, cube_lane, teleporting, teleport_from, teleport_to,
              teleport_elapsed, obstacles, divider, distance10, score, frame):
    draw_world(display, distance10, divider)

    score_text = str(score)
    display.text(score_text, WIDTH - len(score_text) * 8, 3, 1)

    for obstacle in obstacles:
        draw_obstacle(display, obstacle, frame)

    draw_divider(display, divider, frame)

    if teleporting:
        if teleport_elapsed < TELEPORT_MS // 2:
            draw_explosion(display, teleport_from, teleport_elapsed)
        else:
            draw_explosion(display, teleport_to, TELEPORT_MS - teleport_elapsed)
    else:
        draw_cube(display, cube_lane)

    display.show()


def draw_game_over(display, score, high_score):
    display.fill(0)
    display.text("GAME OVER", 28, 2, 1)
    display.text("S:" + str(score), 20, 14, 1)
    display.text("H:" + str(high_score), 76, 14, 1)
    display.text("PRESS BUTTON", 18, 24, 1)
    display.show()


def update_led(led, state, now, teleporting, obstacle_led_ms):
    if teleporting:
        led.duty_u16(MAX_DUTY if (now // 35) & 1 else 0)
    elif state == TITLE:
        phase = (now // 12) % 200
        if phase > 100:
            phase = 200 - phase
        led.duty_u16(phase * 180)
    elif state == RUNNING:
        if obstacle_led_ms > 0:
            led.duty_u16(MAX_DUTY if (obstacle_led_ms // 45) & 1 else 0)
        else:
            led.duty_u16(0)
    else:
        led.duty_u16(MAX_DUTY if (now // 150) & 1 else 0)


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

    cube_lane = BOTTOM
    teleporting = False
    teleport_start = 0
    teleport_from = BOTTOM
    teleport_to = TOP
    obstacles = []
    divider = None
    spawn_in10 = START_SPAWN_GAP10
    obstacle_led_ms = 0

    try:
        while True:
            frame_start = ticks_ms()
            pressed = button.pressed(frame_start)

            if state == TITLE:
                if pressed:
                    state = RUNNING
                    score = 0
                    distance10 = 0
                    cube_lane = BOTTOM
                    teleporting = False
                    obstacles = []
                    divider = None
                    spawn_in10 = START_SPAWN_GAP10
                    obstacle_led_ms = 0
                draw_title(display, frame)

            elif state == RUNNING:
                teleport_elapsed = 0

                if teleporting:
                    teleport_elapsed = ticks_diff(frame_start, teleport_start)
                    if teleport_elapsed >= TELEPORT_MS:
                        cube_lane = teleport_to
                        teleporting = False
                        teleport_elapsed = 0
                elif pressed:
                    if not divider_blocks_teleport(divider):
                        teleporting = True
                        teleport_start = frame_start
                        teleport_from = cube_lane
                        teleport_to = TOP if cube_lane == BOTTOM else BOTTOM
                        obstacle_led_ms = TELEPORT_MS
                    else:
                        obstacle_led_ms = OBSTACLE_LED_MS

                speed10 = BASE_SPEED10 + min(score // SPEED_SCORE_STEP, MAX_SPEED_BOOST10)
                distance10 += speed10
                score = distance10 // SCORE_DISTANCE10

                i = 0
                while i < len(obstacles):
                    obstacles[i][0] -= speed10
                    if obstacles[i][0] // 10 < -obstacles[i][2]:
                        obstacles.pop(i)
                    else:
                        i += 1

                if divider is not None:
                    divider[0] -= speed10
                    if divider[0] // 10 < -divider[1]:
                        divider = None

                active_hazard = len(obstacles) > 0 or divider is not None
                if not active_hazard:
                    spawn_in10 -= speed10

                if spawn_in10 <= 0 and not active_hazard:
                    seed = next_seed(seed)
                    if score > 6 and (seed & 7) == 0:
                        width = 40 + ((seed >> 4) % 18)
                        blocked_lane = (seed >> 9) & 1
                        divider = [WIDTH * 10, width, blocked_lane]
                    else:
                        lane = (seed >> 5) & 1
                        width = 6 + ((seed >> 8) % 5)
                        height = 6 + ((seed >> 12) % 5)
                        obstacles.append([WIDTH * 10, lane, width, height])

                    obstacle_led_ms = OBSTACLE_LED_MS
                    spawn_in10 = SPAWN_GAP_MIN10 + ((seed >> 15) % SPAWN_GAP_RANDOM10)

                if obstacle_led_ms > 0:
                    obstacle_led_ms = max(0, obstacle_led_ms - FRAME_MS)

                hit = False
                if not teleporting:
                    cube_y = lane_y(cube_lane)

                    for obstacle in obstacles:
                        obs_x = obstacle[0] // 10
                        obs_lane = obstacle[1]
                        obs_w = obstacle[2]
                        obs_h = obstacle[3]
                        obs_y = CEILING_Y + 1 if obs_lane == TOP else GROUND_Y - obs_h

                        if cube_lane == obs_lane and rects_overlap(
                            CUBE_X, cube_y, CUBE_SIZE, CUBE_SIZE,
                            obs_x, obs_y, obs_w, obs_h,
                        ):
                            hit = True
                            break

                    if divider is not None:
                        div_x = divider[0] // 10
                        div_w = divider[1]
                        blocked_lane = divider[2]
                        block_x = div_x + div_w // 2 - 4
                        block_y = lane_y(blocked_lane)

                        if cube_lane == blocked_lane and rects_overlap(
                            CUBE_X, cube_y, CUBE_SIZE, CUBE_SIZE,
                            block_x, block_y, 8, CUBE_SIZE,
                        ):
                            hit = True

                if hit:
                    state = GAME_OVER
                    if score > high_score:
                        high_score = score
                    draw_game_over(display, score, high_score)
                else:
                    if teleporting:
                        teleport_elapsed = ticks_diff(frame_start, teleport_start)
                    draw_game(
                        display, cube_lane, teleporting, teleport_from,
                        teleport_to, teleport_elapsed, obstacles, divider,
                        distance10, score, frame,
                    )

            else:
                if pressed:
                    state = TITLE
                    draw_title(display, frame)
                else:
                    draw_game_over(display, score, high_score)

            update_led(led, state, frame_start, teleporting, obstacle_led_ms)
            frame += 1

            elapsed = ticks_diff(ticks_ms(), frame_start)
            if elapsed < FRAME_MS:
                sleep_ms(FRAME_MS - elapsed)
    finally:
        button.close()
        led.duty_u16(0)


main()
