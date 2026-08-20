"""
Test fixtures.

The point of this file is that `pytest` runs on a laptop. None of the
hardware libraries are importable off a Pi -- smbus2 needs /dev/i2c-1,
gpiozero probes for a pin factory at import, evdev wants /dev/uinput --
so they are replaced with mocks in sys.modules before any source module
is imported.

The source is not modified for testability beyond one thing: the pure
functions that were worth testing (padmap.decode_axes, tarcade.
term_geometry, tarcade.enable_output's runner) are split from the I/O
that surrounds them, so the tests exercise real code rather than a
reimplementation of it.
"""

import os
import sys
import types
from unittest.mock import MagicMock

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))


def _stub(name, **attrs):
    """Install a fake module, unless the real one is importable."""
    if name in sys.modules:
        return sys.modules[name]
    m = MagicMock(name=name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# pygame is imported for its constants and its display/font subsystems;
# a MagicMock satisfies every module-level use in tarcade.py.
_stub("pygame")
_stub("smbus2")
_stub("gpiozero")

# evdev.ecodes is read at import time to build the key profiles. MagicMock
# hands out a distinct object per attribute, which is all the tests need.
_evdev = _stub("evdev")
_evdev.ecodes = MagicMock(name="ecodes")


class FakeBus:
    """Enough of smbus2.SMBus to drive padmap.read_adc.

    Feed it (h, v) counts; it answers whichever channel was last selected
    via the config register.
    """

    def __init__(self, h=0, v=0):
        self.h = h
        self.v = v
        self.channel = None
        self.writes = 0

    def set(self, h, v):
        self.h, self.v = h, v

    def write_i2c_block_data(self, addr, reg, data):
        import config
        cfg = (data[0] << 8) | data[1]
        mux = cfg & 0x7000
        self.channel = next((c for c, m in config.ADS_MUX.items()
                             if m == mux), None)
        self.writes += 1

    def read_i2c_block_data(self, addr, reg, count):
        import config
        val = self.h if self.channel == config.ADC_CH_H else self.v
        raw = val & 0xFFFF
        return [(raw >> 8) & 0xFF, raw & 0xFF]

    def close(self):
        pass


@pytest.fixture
def fake_bus():
    return FakeBus()


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the ADC conversion and calibration delays.

    Deliberately not autouse: the process-teardown tests need real time
    to pass while a real process dies.
    """
    import time
    monkeypatch.setattr(time, "sleep", lambda *_: None)
