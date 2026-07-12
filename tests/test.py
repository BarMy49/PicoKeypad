from machine import I2C, Pin, disable_irq, enable_irq
from time import sleep_ms, ticks_add, ticks_diff, ticks_ms
import framebuf

try:
    from machine import SoftI2C
except ImportError:
    SoftI2C = None


# Raspberry Pi Pico + OLED 0.91" 128x32 SSD1306:
I2C_ID = 1
SDA_PIN = 2
SCL_PIN = 3
I2C_FREQ = 50000
I2C_RETRIES = 5
I2C_SCAN_RETRIES = 5
I2C_POWERUP_MS = 300
I2C_RETRY_DELAY_MS = 50
I2C_DATA_CHUNK = 32
WIDTH = 128
HEIGHT = 32

ROW_PINS = (16, 17, 18)
COL_PINS = (22, 21, 20, 19)
ENCODER_A_PIN = 26
ENCODER_B_PIN = 27
ENCODER_BUTTON_PIN = 28
ENCODER_REVERSE = True
DEBOUNCE_MS = 30
SCAN_SETTLE_MS = 1
LOOP_MS = 1
ENCODER_IDLE_STATE = 3
ENCODER_STEP_MIN_EDGES = 3

ENCODER_TRANSITIONS = (
    0, -1, 1, 0,
    1, 0, 0, -1,
    -1, 0, 0, 1,
    0, 1, -1, 0,
)

I2C1_ALTERNATE_BUSES = (
    (1, 2, 3),
    (1, 6, 7),
    (1, 10, 11),
    (1, 18, 19),
    (1, 26, 27),
)


class SSD1306_I2C(framebuf.FrameBuffer):
    def __init__(self, width, height, i2c, addr=0x3C, config=None):
        self.width = width
        self.height = height
        self.i2c = i2c
        self.addr = addr
        self.config = config
        self.pages = self.height // 8
        self.buffer = bytearray(self.pages * self.width)
        super().__init__(self.buffer, self.width, self.height, framebuf.MONO_VLSB)
        self.init_display()

    def write_cmd(self, cmd):
        self.write_with_retry(bytearray([0x80, cmd]), "cmd 0x{:02x}".format(cmd))

    def write_data(self, data):
        for offset in range(0, len(data), I2C_DATA_CHUNK):
            chunk = data[offset:offset + I2C_DATA_CHUNK]
            packet = bytearray(1 + len(chunk))
            packet[0] = 0x40
            packet[1:] = chunk
            self.write_with_retry(packet, "data {}..{}".format(
                offset, offset + len(chunk) - 1
            ))

    def recover_after_failure(self):
        if self.config is None:
            return

        bus_name, sda_pin, scl_pin = self.config
        recover_i2c_bus(sda_pin, scl_pin)

        if bus_name != "soft" and SoftI2C is not None:
            print("Przelaczam OLED na SoftI2C po bledzie zapisu.")
            self.config = ("soft", sda_pin, scl_pin)

        self.i2c = rebuild_i2c_from_config(self.config)

    def write_with_retry(self, packet, label):
        last_error = None

        for attempt in range(1, I2C_RETRIES + 1):
            try:
                self.i2c.writeto(self.addr, packet)
                return
            except OSError as exc:
                last_error = exc
                print("I2C write failed on {}, attempt {}/{}: {}".format(
                    label, attempt, I2C_RETRIES, exc
                ))

                if self.config is not None and attempt < I2C_RETRIES:
                    self.recover_after_failure()

                sleep_ms(I2C_RETRY_DELAY_MS)

        raise last_error

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


def read_idle_levels(sda_pin, scl_pin):
    sda = Pin(sda_pin, Pin.IN, Pin.PULL_UP)
    scl = Pin(scl_pin, Pin.IN, Pin.PULL_UP)
    sleep_ms(5)

    sda_level = sda.value()
    scl_level = scl.value()
    print("Idle GP{} SDA={}, GP{} SCL={}".format(
        sda_pin, sda_level, scl_pin, scl_level
    ))

    if not sda_level or not scl_level:
        print("Jedna z linii jest trzymana nisko. I2C nie odpowie, dopoki SDA i SCL nie beda 1.")

    return sda_level, scl_level


def scan_named_bus(name, i2c_factory, sda_pin=None, scl_pin=None):
    print("Skanuje {}...".format(name))

    i2c = None
    last_error = None

    for attempt in range(1, I2C_SCAN_RETRIES + 1):
        if attempt > 1:
            print("  Ponawiam skan, proba {}/{}...".format(
                attempt, I2C_SCAN_RETRIES
            ))
            if sda_pin is not None and scl_pin is not None:
                recover_i2c_bus(sda_pin, scl_pin)
            sleep_ms(I2C_RETRY_DELAY_MS)

        try:
            i2c = i2c_factory()
            addresses = scan_i2c(i2c)
            if addresses:
                return i2c, addresses
        except Exception as exc:
            last_error = exc
            print("  Skan/proba {} zakonczona bledem: {}".format(
                attempt, exc
            ))

    if last_error is not None:
        print("  Ostatni blad I2C:", last_error)

    return i2c, []


def make_hardware_i2c(i2c_id, sda_pin, scl_pin):
    return I2C(i2c_id, sda=Pin(sda_pin), scl=Pin(scl_pin), freq=I2C_FREQ)


def make_soft_i2c(sda_pin, scl_pin):
    if SoftI2C is None:
        raise RuntimeError("SoftI2C nie jest dostepne w tym firmware")

    return SoftI2C(sda=Pin(sda_pin), scl=Pin(scl_pin), freq=I2C_FREQ)


def recover_i2c_bus(sda_pin, scl_pin):
    print("Probuje zwolnic magistrale I2C na GP{}/{}...".format(sda_pin, scl_pin))
    sda = Pin(sda_pin, Pin.IN, Pin.PULL_UP)
    scl = Pin(scl_pin, Pin.IN, Pin.PULL_UP)
    sleep_ms(2)

    for _ in range(9):
        if sda.value():
            break
        scl = Pin(scl_pin, Pin.OUT, value=0)
        sleep_ms(1)
        scl = Pin(scl_pin, Pin.IN, Pin.PULL_UP)
        sleep_ms(1)

    sda = Pin(sda_pin, Pin.OUT, value=0)
    sleep_ms(1)
    scl = Pin(scl_pin, Pin.IN, Pin.PULL_UP)
    sleep_ms(1)
    sda = Pin(sda_pin, Pin.IN, Pin.PULL_UP)
    sleep_ms(2)

    Pin(sda_pin, Pin.IN, Pin.PULL_UP)
    Pin(scl_pin, Pin.IN, Pin.PULL_UP)


def diagnose_primary_bus():
    print("Diagnoza: I2C{} SDA=GP{} SCL=GP{}, addr=0x3C".format(
        I2C_ID, SDA_PIN, SCL_PIN
    ))
    sleep_ms(I2C_POWERUP_MS)
    read_idle_levels(SDA_PIN, SCL_PIN)
    recover_i2c_bus(SDA_PIN, SCL_PIN)

    i2c, addresses = scan_named_bus(
        "hardware I2C{} GP{}/{}".format(I2C_ID, SDA_PIN, SCL_PIN),
        lambda: make_hardware_i2c(I2C_ID, SDA_PIN, SCL_PIN),
        SDA_PIN,
        SCL_PIN,
    )
    if addresses:
        return i2c, addresses, (I2C_ID, SDA_PIN, SCL_PIN)

    i2c, addresses = scan_named_bus(
        "SoftI2C GP{}/{}".format(SDA_PIN, SCL_PIN),
        lambda: make_soft_i2c(SDA_PIN, SCL_PIN),
        SDA_PIN,
        SCL_PIN,
    )
    if addresses:
        print("SoftI2C dziala na GP{}/GP{}, ale hardware I2C{} nie.".format(
            SDA_PIN, SCL_PIN, I2C_ID
        ))
        print("To wskazuje na problem z hardware I2C{} albo firmware, nie na adres OLED.".format(
            I2C_ID
        ))
        return i2c, addresses, ("soft", SDA_PIN, SCL_PIN)

    print("SoftI2C tez nie widzi urzadzenia na GP{}/GP{}.".format(
        SDA_PIN, SCL_PIN
    ))
    print("To bardziej wskazuje na linie/piny/zasilanie niz na sam kanal I2C{}.".format(
        I2C_ID
    ))
    return None, [], None


def scan_i2c1_alternates():
    for i2c_id, sda_pin, scl_pin in I2C1_ALTERNATE_BUSES:
        i2c, addresses = scan_named_bus(
            "alternatywny I2C{} GP{}/{}".format(i2c_id, sda_pin, scl_pin),
            lambda i2c_id=i2c_id, sda_pin=sda_pin, scl_pin=scl_pin:
                make_hardware_i2c(i2c_id, sda_pin, scl_pin),
            sda_pin,
            scl_pin,
        )
        if addresses:
            print("I2C{} dziala na innej parze pinow.".format(i2c_id))
            print("Jesli OLED jest na GP{}/GP{}, problemem moga byc same piny albo polaczenie.".format(
                SDA_PIN, SCL_PIN
            ))
            return i2c, addresses, (i2c_id, sda_pin, scl_pin)

    return None, [], None


def init_display_or_none(i2c, address, config):
    bus_name, sda_pin, scl_pin = config
    print("Inicjalizuje OLED przez {} GP{}/{} addr={}...".format(
        bus_name, sda_pin, scl_pin, hex(address)
    ))

    last_error = None

    for attempt in range(1, I2C_RETRIES + 1):
        try:
            return SSD1306_I2C(WIDTH, HEIGHT, i2c, address, config)
        except OSError as exc:
            last_error = exc
            print("Inicjalizacja OLED nie powiodla sie, proba {}/{}: {}".format(
                attempt, I2C_RETRIES, exc
            ))

            if attempt < I2C_RETRIES:
                recover_i2c_bus(sda_pin, scl_pin)
                sleep_ms(I2C_RETRY_DELAY_MS)
                i2c = rebuild_i2c_from_config(config)

    print("Ostatni blad inicjalizacji OLED:", last_error)
    return None


def try_soft_i2c_after_hardware_failure(address):
    if SoftI2C is None:
        return None, None

    recover_i2c_bus(SDA_PIN, SCL_PIN)
    i2c, addresses = scan_named_bus(
        "awaryjny SoftI2C GP{}/{}".format(SDA_PIN, SCL_PIN),
        lambda: make_soft_i2c(SDA_PIN, SCL_PIN),
        SDA_PIN,
        SCL_PIN,
    )

    if address not in addresses:
        return None, None

    config = ("soft", SDA_PIN, SCL_PIN)
    return init_display_or_none(i2c, address, config), config


def rebuild_i2c_from_config(config):
    bus_name, sda_pin, scl_pin = config

    if bus_name == "soft":
        return make_soft_i2c(sda_pin, scl_pin)

    return make_hardware_i2c(bus_name, sda_pin, scl_pin)


def clear_screen(display):
    display.fill(0)
    display.show()


def draw_splash(display):
    clear_screen(display)
    display.fill(0)
    display.rect(0, 0, WIDTH, HEIGHT, 1)
    display.text("KEYPAD TEST", 20, 5, 1)
    display.text("MATRIX + ENC", 14, 18, 1)
    display.show()


def draw_button(display, row_index, col_index):
    button_number = row_index * len(COL_PINS) + col_index + 1
    row_pin = ROW_PINS[row_index]
    col_pin = COL_PINS[col_index]

    display.fill(0)
    display.text("BUTTON " + str(button_number), 20, 2, 1)
    display.text("ROW " + str(row_index + 1) + " COL " + str(col_index + 1), 14, 13, 1)
    display.text("GP{} -> GP{}".format(row_pin, col_pin), 12, 24, 1)
    display.show()


def draw_encoder_turn(display, direction, position):
    display.fill(0)
    display.text("ENC " + direction, 32, 2, 1)
    display.text("COUNT " + str(position), 26, 13, 1)
    display.text("GP{} GP{}".format(ENCODER_A_PIN, ENCODER_B_PIN), 18, 24, 1)
    display.show()


def draw_encoder_button(display, position):
    display.fill(0)
    display.text("ENC BUTTON", 24, 2, 1)
    display.text("PRESSED", 36, 13, 1)
    display.text("GP{} C{}".format(ENCODER_BUTTON_PIN, position), 24, 24, 1)
    display.show()


def init_matrix():
    rows = [Pin(pin, Pin.OUT, value=0) for pin in ROW_PINS]
    cols = [Pin(pin, Pin.IN, Pin.PULL_UP) for pin in COL_PINS]
    return rows, cols


def idle_rows(rows):
    for row in rows:
        row.init(Pin.OUT, value=0)


def release_rows(rows):
    for row in rows:
        row.init(Pin.IN)


def read_matrix(rows, cols):
    pressed = []

    for row_index, row in enumerate(rows):
        release_rows(rows)
        row.init(Pin.OUT, value=0)
        sleep_ms(SCAN_SETTLE_MS)

        for col_index, col in enumerate(cols):
            if col.value() == 0:
                pressed.append((row_index, col_index))

    idle_rows(rows)
    return pressed


def first_pressed_key(rows, cols):
    pressed = read_matrix(rows, cols)
    if pressed:
        return pressed[0]
    return None


class MatrixKeyboard:
    def __init__(self):
        self.rows, self.cols = init_matrix()
        self.stable_key = first_pressed_key(self.rows, self.cols)
        self.pending = True
        self.deadline = ticks_add(ticks_ms(), DEBOUNCE_MS)
        self.scanning = False

        trigger = Pin.IRQ_RISING | Pin.IRQ_FALLING
        for col in self.cols:
            col.irq(trigger=trigger, handler=self._irq)

    def _irq(self, pin):
        if not self.scanning:
            self.pending = True

    def poll_event(self, now):
        irq_state = disable_irq()
        pending = self.pending
        self.pending = False
        enable_irq(irq_state)

        if pending:
            self.deadline = ticks_add(now, DEBOUNCE_MS)

        if self.deadline is None or ticks_diff(now, self.deadline) < 0:
            return None

        self.deadline = None
        self.scanning = True
        current = first_pressed_key(self.rows, self.cols)
        self.scanning = False
        if current == self.stable_key:
            return None

        self.stable_key = current
        return ("matrix", current)


class Encoder:
    def __init__(self):
        self.a = Pin(ENCODER_A_PIN, Pin.IN, Pin.PULL_UP)
        self.b = Pin(ENCODER_B_PIN, Pin.IN, Pin.PULL_UP)
        self.button = Pin(ENCODER_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
        self.last_state = self.read_state()
        self.edge_sum = 0
        self.position = 0
        self.reported_position = 0

        self.last_button_read = self.button_pressed()
        self.stable_button = self.last_button_read
        self.button_last_change = ticks_ms()
        self.button_pending = False
        self.button_deadline = None

        trigger = Pin.IRQ_RISING | Pin.IRQ_FALLING
        self.a.irq(trigger=trigger, handler=self._irq)
        self.b.irq(trigger=trigger, handler=self._irq)
        self.button.irq(trigger=trigger, handler=self._button_irq)

    def read_state(self):
        return (self.a.value() << 1) | self.b.value()

    def button_pressed(self):
        return self.button.value() == 0

    def _button_irq(self, pin):
        self.button_pending = True

    def _irq(self, pin):
        state = self.read_state()
        if state == self.last_state:
            return

        movement = ENCODER_TRANSITIONS[(self.last_state << 2) | state]
        self.last_state = state

        if movement == 0:
            self.edge_sum = 0
            return

        self.edge_sum += movement
        if state != ENCODER_IDLE_STATE:
            return

        if self.edge_sum >= ENCODER_STEP_MIN_EDGES:
            step = 1
        elif self.edge_sum <= -ENCODER_STEP_MIN_EDGES:
            step = -1
        else:
            self.edge_sum = 0
            return

        self.edge_sum = 0
        if ENCODER_REVERSE:
            step = -step

        self.position += step

    def poll_turn(self):
        irq_state = disable_irq()
        position = self.position
        enable_irq(irq_state)

        movement = position - self.reported_position
        if movement:
            self.reported_position = position

        return movement

    def poll_button(self, now):
        irq_state = disable_irq()
        pending = self.button_pending
        self.button_pending = False
        enable_irq(irq_state)

        if pending:
            self.button_deadline = ticks_add(now, DEBOUNCE_MS)

        if self.button_deadline is None or ticks_diff(now, self.button_deadline) < 0:
            return None

        self.button_deadline = None
        current = self.button_pressed()
        self.last_button_read = current

        if current != self.stable_button:
            self.stable_button = current
            return current

        return None


def run_input_test(display):
    matrix = MatrixKeyboard()
    encoder = Encoder()
    print("Matrix ready. Rows={}, columns={}.".format(ROW_PINS, COL_PINS))
    print("Encoder ready. A=GP{}, B=GP{}, button=GP{}, idle high.".format(
        ENCODER_A_PIN, ENCODER_B_PIN, ENCODER_BUTTON_PIN
    ))

    if matrix.stable_key is not None:
        draw_button(display, matrix.stable_key[0], matrix.stable_key[1])

    while True:
        now = ticks_ms()

        turn = encoder.poll_turn()
        if turn:
            direction = "CW" if turn > 0 else "CCW"
            print("Encoder {} count {}".format(direction, encoder.position))
            draw_encoder_turn(display, direction, encoder.position)

        button_event = encoder.poll_button(now)
        if button_event is not None:
            if button_event:
                print("Encoder button pressed")
                draw_encoder_button(display, encoder.position)
            else:
                print("Encoder button released")

        matrix_event = matrix.poll_event(now)
        if matrix_event is not None:
            current = matrix_event[1]
            if current is None:
                print("Button released")
            else:
                row_index, col_index = current
                button_number = row_index * len(COL_PINS) + col_index + 1
                print("Button {}: row GP{}, col GP{}".format(
                    button_number, ROW_PINS[row_index], COL_PINS[col_index]
                ))
                draw_button(display, row_index, col_index)

        sleep_ms(LOOP_MS)


def main():
    i2c, addresses, config = diagnose_primary_bus()

    if not addresses:
        print("Brak urzadzen I2C.")
        print("Sprawdz podlaczenie: VCC=3V3, GND=GND, SDA=GP{}, SCL=GP{}.".format(
            SDA_PIN, SCL_PIN
        ))
        while True:
            sleep_ms(1000)

    address = 0x3C if 0x3C in addresses else addresses[0]
    recover_i2c_bus(config[1], config[2])
    i2c = rebuild_i2c_from_config(config)
    display = init_display_or_none(i2c, address, config)

    if display is None and config[0] != "soft":
        display, soft_config = try_soft_i2c_after_hardware_failure(address)
        if display is not None:
            config = soft_config

    if display is None:
        print("Adres jest widoczny, ale OLED odrzuca lub gubi zapisy I2C.")
        print("Najbardziej podejrzane: zbyt slabe pullupy, dlugie przewody, zasilanie albo uszkodzony pin/modul.")
        while True:
            sleep_ms(1000)

    draw_splash(display)
    run_input_test(display)


main()
