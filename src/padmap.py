"""
padmap.py -- shared joystick/button decoding for T-ARCADE.

Both the launcher and the in-game input bridge import this, so the
control mapping can never drift between them. Getting this wrong in one
place and not the other is exactly the bug that motivated splitting it
out: the menu scrolled the right way and the game did not.

The stick is mounted rotated 90 degrees in this cabinet, so raw axes are
remapped before anything downstream sees them:

    physical UP    -> LEFT
    physical RIGHT -> UP
    physical DOWN  -> RIGHT
    physical LEFT  -> DOWN

Everything tunable lives in config.py. The two module-level names kept
here (BTN_PINS, and the decode/calibrate pair) are the whole interface.
"""

import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config

BTN_PINS = config.BTN_PINS


def read_adc(bus, ch):
    """One single-shot conversion on an ADS1115 channel.

    Returns the signed 16-bit result. The chip is left in single-shot
    mode rather than continuous because four channels share one input
    mux and continuous mode would just read the same one repeatedly.
    """
    cfg = config.ADS_CONFIG_BASE | config.ADS_MUX[ch]
    bus.write_i2c_block_data(config.ADS_ADDR, 1,
                             [(cfg >> 8) & 0xFF, cfg & 0xFF])
    time.sleep(config.ADC_CONVERSION_DELAY)
    hi, lo = bus.read_i2c_block_data(config.ADS_ADDR, 0, 2)
    r = (hi << 8) | lo
    return r - 65536 if r & 0x8000 else r


def calibrate(bus, samples=None):
    """Average the resting position so drift doesn't register as input.

    Called at startup by both the launcher and the bridge. Leave the
    stick alone while it runs -- a held direction becomes the new centre
    and the axis will read inverted afterwards.
    """
    samples = samples or config.CALIBRATION_SAMPLES
    time.sleep(config.CALIBRATION_SETTLE)
    sh = sv = 0
    for _ in range(samples):
        sh += read_adc(bus, config.ADC_CH_H)
        sv += read_adc(bus, config.ADC_CH_V)
    return sh // samples, sv // samples


def decode_axes(h, v):
    """Normalised (-1..1) axes -> the four logical directions.

    Split out from decode() so the rotation table can be tested without
    an I2C bus. Inversion is applied first, then the deadzone, then the
    90-degree rotation -- order matters, since rotating a value that has
    not been inverted yet swaps two directions rather than four.
    """
    if config.INVERT_H:
        h = -h
    if config.INVERT_V:
        v = -v

    dz = config.DEADZONE
    p_left = h < -dz
    p_right = h > dz
    p_up = v < -dz
    p_down = v > dz

    if config.ROTATE_90:
        # up->left, right->up, down->right, left->down
        return {"left": p_up, "up": p_right,
                "right": p_down, "down": p_left}
    return {"left": p_left, "right": p_right,
            "up": p_up, "down": p_down}


def decode(bus, ch, cv):
    """Raw ADC -> the four logical directions, rotation applied."""
    h = (read_adc(bus, config.ADC_CH_H) - ch) / config.ADC_SPAN
    v = (read_adc(bus, config.ADC_CH_V) - cv) / config.ADC_SPAN
    return decode_axes(h, v)
