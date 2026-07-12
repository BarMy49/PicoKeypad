from machine import Pin, disable_irq, enable_irq
from time import sleep_ms, ticks_add, ticks_diff, ticks_ms

import config


class MatrixKeyboard:
    def __init__(self):
        self.rows = [Pin(pin, Pin.IN) for pin in config.ROW_PINS]
        self.cols = [Pin(pin, Pin.IN, Pin.PULL_UP) for pin in config.COL_PINS]
        self.raw_state = self.read_state()
        self.stable_state = self.raw_state
        self.raw_changed_at = ticks_ms()

    def release_rows(self):
        for row in self.rows:
            row.init(Pin.IN)

    def reset(self):
        self.raw_state = self.read_state()
        self.stable_state = self.raw_state
        self.raw_changed_at = ticks_ms()

    def read_state(self):
        state = [False] * (len(config.ROW_PINS) * len(config.COL_PINS))

        for row_index, row in enumerate(self.rows):
            self.release_rows()
            row.init(Pin.OUT, value=0)
            sleep_ms(config.SCAN_SETTLE_MS)

            for col_index, col in enumerate(self.cols):
                key_index = row_index * len(config.COL_PINS) + col_index
                state[key_index] = col.value() == 0

        self.release_rows()
        return tuple(state)

    def poll(self, now):
        raw_state = self.read_state()

        if raw_state != self.raw_state:
            self.raw_state = raw_state
            self.raw_changed_at = now
            return []

        if ticks_diff(now, self.raw_changed_at) < config.DEBOUNCE_MS:
            return []

        if raw_state == self.stable_state:
            return []

        old_state = self.stable_state
        self.stable_state = raw_state
        events = []

        for key_index, pressed in enumerate(raw_state):
            if pressed == old_state[key_index]:
                continue

            row_index = key_index // len(config.COL_PINS)
            col_index = key_index % len(config.COL_PINS)
            events.append({
                "type": "key",
                "event": "down" if pressed else "up",
                "key": key_index + 1,
                "row": row_index + 1,
                "col": col_index + 1,
                "row_index": row_index,
                "col_index": col_index,
                "row_pin": config.ROW_PINS[row_index],
                "col_pin": config.COL_PINS[col_index],
            })

        return events


class Encoder:
    def __init__(self):
        self.a = Pin(config.ENCODER_A_PIN, Pin.IN, Pin.PULL_UP)
        self.b = Pin(config.ENCODER_B_PIN, Pin.IN, Pin.PULL_UP)
        self.button = Pin(config.ENCODER_BUTTON_PIN, Pin.IN, Pin.PULL_UP)
        self.last_state = self.read_state()
        self.edge_sum = 0
        self.position = 0
        self.reported_position = 0

        self.stable_button = self.button_pressed()
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

        movement = config.ENCODER_TRANSITIONS[(self.last_state << 2) | state]
        self.last_state = state

        if movement == 0:
            self.edge_sum = 0
            return

        self.edge_sum += movement
        if state not in config.ENCODER_DETENT_STATES:
            return

        if self.edge_sum >= config.ENCODER_STEP_MIN_EDGES:
            step = 1
        elif self.edge_sum <= -config.ENCODER_STEP_MIN_EDGES:
            step = -1
        else:
            self.edge_sum = 0
            return

        self.edge_sum = 0
        if config.ENCODER_REVERSE:
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
            self.button_deadline = ticks_add(now, config.DEBOUNCE_MS)

        if self.button_deadline is None or ticks_diff(now, self.button_deadline) < 0:
            return None

        self.button_deadline = None
        current = self.button_pressed()

        if current != self.stable_button:
            self.stable_button = current
            return current

        return None

    def poll(self, now):
        events = []
        movement = self.poll_turn()

        if movement:
            events.append({
                "type": "encoder",
                "event": "turn",
                "delta": movement,
                "position": self.reported_position,
            })

        button = self.poll_button(now)
        if button is not None:
            events.append({
                "type": "encoder",
                "event": "button_down" if button else "button_up",
                "pressed": button,
                "position": self.reported_position,
            })

        return events
