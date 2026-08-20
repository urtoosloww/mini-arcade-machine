"""
padmap.py -- shared joystick/button decoding for T-ARCADE.

Both the launcher and the in-game input bridge import this, so the
control mapping can never drift between them.

The stick is mounted rotated 90 degrees in this cabinet, so raw axes are
remapped before anything downstream sees them:

    physical UP    -> LEFT
    physical RIGHT -> UP
    physical DOWN  -> RIGHT
    physical LEFT  -> DOWN
"""

import time

ADS_ADDR = 0x48
MUX = {0: 0x4000, 1: 0x5000, 2: 0x6000, 3: 0x7000}
BASE = 0x8000 | 0x0200 | 0x0100 | 0x00E0 | 0x0003

CH_H, CH_V = 1, 3          # VRx on A1, VRy on A3
SPAN = 9400
DEADZONE = 0.50
INVERT_H = True            # wiring gives reversed horizontal
INVERT_V = True
ROTATE_90 = True           # stick is mounted rotated

BTN_PINS = {"a": 5, "b": 6, "start": 20, "select": 21}


def read_adc(bus, ch):
    cfg = BASE | MUX[ch]
    bus.write_i2c_block_data(ADS_ADDR, 1, [(cfg >> 8) & 0xFF, cfg & 0xFF])
    time.sleep(0.0015)
    hi, lo = bus.read_i2c_block_data(ADS_ADDR, 0, 2)
    r = (hi << 8) | lo
    return r - 65536 if r & 0x8000 else r


def calibrate(bus, samples=30):
    """Average the resting position so drift doesn't register as input."""
    time.sleep(0.3)
    sh = sv = 0
    for _ in range(samples):
        sh += read_adc(bus, CH_H)
        sv += read_adc(bus, CH_V)
    return sh // samples, sv // samples


def decode(bus, ch, cv):
    """Raw ADC -> the four logical directions, rotation applied."""
    h = (read_adc(bus, CH_H) - ch) / SPAN
    v = (read_adc(bus, CH_V) - cv) / SPAN
    if INVERT_H:
        h = -h
    if INVERT_V:
        v = -v

    # Physical stick directions
    p_left = h < -DEADZONE
    p_right = h > DEADZONE
    p_up = v < -DEADZONE
    p_down = v > DEADZONE

    if ROTATE_90:
        # up->left, right->up, down->right, left->down
        return {"left": p_up, "up": p_right,
                "right": p_down, "down": p_left}
    return {"left": p_left, "right": p_right,
            "up": p_up, "down": p_down}
