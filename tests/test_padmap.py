"""
The rotation truth table.

The stick is mounted rotated 90 degrees, so every direction the player
pushes comes out as a different direction in software. This is the kind
of thing that is obvious for ten minutes and then silently regresses,
which is why the mapping lives in one module and is pinned here.

    physical UP    -> LEFT
    physical RIGHT -> UP
    physical DOWN  -> RIGHT
    physical LEFT  -> DOWN
"""

import pytest

import config
import padmap

DIRS = ("up", "down", "left", "right")


def only(direction):
    """The expected decode() output when exactly one direction is active."""
    return {d: (d == direction) for d in DIRS}


def none():
    return {d: False for d in DIRS}


# Physical axis values, well past the deadzone, BEFORE inversion --
# i.e. what the ADC actually reports relative to centre.
FULL = 1.0

# (raw h, raw v, what the player should get)
# Inversion is applied inside decode_axes, so a raw h of +1.0 is a
# physical LEFT push on this wiring.
TRUTH_TABLE = [
    pytest.param(+FULL, 0.0, "down",  id="physical-left->down"),
    pytest.param(-FULL, 0.0, "up",    id="physical-right->up"),
    pytest.param(0.0, +FULL, "left",  id="physical-up->left"),
    pytest.param(0.0, -FULL, "right", id="physical-down->right"),
]


@pytest.mark.parametrize("h,v,expected", TRUTH_TABLE)
def test_rotation_truth_table(h, v, expected):
    assert padmap.decode_axes(h, v) == only(expected)


def test_centre_is_neutral():
    assert padmap.decode_axes(0.0, 0.0) == none()


@pytest.mark.parametrize("mag", [0.0, 0.1, 0.49])
def test_inside_deadzone_reads_neutral(mag):
    """Half of full travel is required, so resting drift never registers."""
    assert padmap.decode_axes(mag, mag) == none()
    assert padmap.decode_axes(-mag, -mag) == none()


def test_deadzone_boundary_is_exclusive():
    """Exactly at the deadzone is still neutral; just past it is not."""
    dz = config.DEADZONE
    assert padmap.decode_axes(dz, 0.0) == none()
    assert padmap.decode_axes(dz + 1e-6, 0.0)["down"] is True


def test_diagonal_produces_two_directions():
    """A diagonal push must not collapse to a single direction."""
    s = padmap.decode_axes(+FULL, +FULL)
    assert {d for d in DIRS if s[d]} == {"left", "down"}


def test_no_direction_is_ever_both():
    """Opposite directions can never be true at once, at any input."""
    for h in (-1.0, -0.5, 0.0, 0.5, 1.0):
        for v in (-1.0, -0.5, 0.0, 0.5, 1.0):
            s = padmap.decode_axes(h, v)
            assert not (s["up"] and s["down"])
            assert not (s["left"] and s["right"])


def test_rotation_can_be_disabled(monkeypatch):
    """With ROTATE_90 off, axes pass through (still inverted by wiring)."""
    monkeypatch.setattr(config, "ROTATE_90", False)
    assert padmap.decode_axes(+FULL, 0.0) == only("left")
    assert padmap.decode_axes(0.0, +FULL) == only("up")


# --- the ADC path, against a fake I2C bus -----------------------------------

def test_read_adc_sign_extends(fake_bus, no_sleep):
    """The ADS1115 is signed 16-bit; 0xFFFF must read as -1, not 65535."""
    fake_bus.set(-1, -1)
    assert padmap.read_adc(fake_bus, config.ADC_CH_H) == -1
    fake_bus.set(20377, 0)
    assert padmap.read_adc(fake_bus, config.ADC_CH_H) == 20377


def test_calibrate_averages_the_resting_position(fake_bus, no_sleep):
    fake_bus.set(13200, 13100)
    ch, cv = padmap.calibrate(fake_bus, samples=8)
    assert (ch, cv) == (13200, 13100)
    assert fake_bus.writes == 16          # two channels per sample


def test_decode_uses_the_calibrated_centre(fake_bus, no_sleep):
    """A stick resting off-centre must still read neutral after calibration."""
    fake_bus.set(15000, 11000)
    ch, cv = padmap.calibrate(fake_bus, samples=4)
    assert padmap.decode(fake_bus, ch, cv) == none()

    # Now push it a full span past that centre.
    fake_bus.set(15000 + config.ADC_SPAN, 11000)
    assert padmap.decode(fake_bus, ch, cv) == only("down")
