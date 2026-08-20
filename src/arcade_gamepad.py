#!/usr/bin/env python3
"""
arcade_gamepad.py
=================
Turns an ADS1115-connected analog joystick + GPIO tactile buttons into a
virtual Linux gamepad via uinput.

Once running, the kernel creates /dev/input/js0 and /dev/input/event*.
RetroArch, MAME, EmulationStation, pygame, SDL2 -- everything sees a
normal gamepad. No emulator-specific glue needed.

Hardware (Raspberry Pi 5):
    ADS1115 on I2C1 @ 0x48, joystick VRx->A1, VRy->A3
    Buttons on GPIO, active-low, internal pull-ups

Usage:
    sudo python3 arcade_gamepad.py              # run the bridge
    sudo python3 arcade_gamepad.py --test       # print raw values, no uinput
    sudo python3 arcade_gamepad.py --calibrate  # record joystick center/range

Dependencies:
    sudo apt install python3-evdev python3-smbus2 python3-gpiozero python3-lgpio
"""

import argparse
import json
import os
import signal
import sys
import time

from smbus2 import SMBus
from gpiozero import Button
from evdev import UInput, AbsInfo, ecodes as e

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

I2C_BUS = 1
ADS_ADDR = 0x48

CAL_FILE = os.path.expanduser("~/.arcade_calibration.json")

# BCM GPIO -> evdev button code.
# Linux gamepad convention: SOUTH is the bottom face button (Xbox "A").
# RetroArch/MAME let you rebind in-app, so this is only the starting layout.
BUTTON_MAP = {
    5:  e.BTN_SOUTH,    # A
    6:  e.BTN_EAST,     # B
    12: e.BTN_NORTH,    # X
    13: e.BTN_WEST,     # Y
    16: e.BTN_TL,       # Left shoulder
    19: e.BTN_TR,       # Right shoulder
    20: e.BTN_START,    # Start
    21: e.BTN_SELECT,   # Select / Coin
    26: e.BTN_THUMBL,   # Joystick push-down
}

# Hold these two together for HOTKEY_HOLD seconds to shut the bridge down.
QUIT_COMBO = (20, 21)      # Start + Select
HOTKEY_HOLD = 2.0

POLL_HZ = 250              # main loop rate
DEADZONE = 0.14            # fraction of half-range ignored around center
AXIS_MAX = 32767           # evdev axis range is -AXIS_MAX .. +AXIS_MAX

# Set True if an axis points the wrong way once you test it in a game.
INVERT_X = False
INVERT_Y = True            # most joystick modules read "up" as a low value


# --------------------------------------------------------------------------
# ADS1115 driver (raw SMBus -- no heavyweight vendor library)
# --------------------------------------------------------------------------

class ADS1115:
    """Minimal single-shot driver. ~860 SPS, so ~1.2 ms per conversion."""

    REG_CONVERSION = 0x00
    REG_CONFIG = 0x01

    # MUX codes for single-ended reads on A0..A3
    _MUX = {0: 0x4000, 1: 0x5000, 2: 0x6000, 3: 0x7000}

    # PGA 001 = +/-4.096 V full scale. Correct choice for a 3.3 V joystick
    # rail: it comfortably covers 0-3.3 V without clipping.
    _BASE = (
        0x8000    # OS: start a single conversion
        | 0x0200  # PGA = +/-4.096 V
        | 0x0100  # MODE = single-shot
        | 0x00E0  # DR  = 860 samples/sec
        | 0x0003  # comparator disabled
    )

    # Volts per LSB at +/-4.096 V full scale
    LSB_VOLTS = 4.096 / 32768.0
    CONV_TIME = 0.0014  # 1/860 s plus a little margin

    def __init__(self, bus, addr=ADS_ADDR):
        self.bus = bus
        self.addr = addr

    def start(self, channel):
        cfg = self._BASE | self._MUX[channel]
        self.bus.write_i2c_block_data(
            self.addr, self.REG_CONFIG, [(cfg >> 8) & 0xFF, cfg & 0xFF]
        )

    def result(self):
        hi, lo = self.bus.read_i2c_block_data(self.addr, self.REG_CONVERSION, 2)
        raw = (hi << 8) | lo
        if raw & 0x8000:          # two's complement
            raw -= 1 << 16
        return raw

    def read(self, channel):
        self.start(channel)
        time.sleep(self.CONV_TIME)
        return self.result()

    def read_pair(self):
        """Read A1 (VRx) then A3 (VRy) -- matches this build's wiring."""
        return self.read(1), self.read(3)


# --------------------------------------------------------------------------
# Calibration
# --------------------------------------------------------------------------

DEFAULT_CAL = {
    # Sensible defaults for a 3.3 V rail before you calibrate:
    # center ~1.65 V, swing 0 - 3.3 V.
    "x": {"min": 0, "center": 13200, "max": 26400},
    "y": {"min": 0, "center": 13200, "max": 26400},
}


def load_calibration():
    try:
        with open(CAL_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULT_CAL)


def run_calibration(ads):
    print("Joystick calibration.\n")
    input("1. Let go of the stick so it centers, then press Enter... ")
    samples = [ads.read_pair() for _ in range(50)]
    cx = sum(s[0] for s in samples) // len(samples)
    cy = sum(s[1] for s in samples) // len(samples)
    print(f"   center: x={cx}  y={cy}\n")

    print("2. Now roll the stick around its full outer edge for 5 seconds.")
    input("   Press Enter to begin... ")
    xs, ys = [cx], [cy]
    end = time.time() + 5.0
    while time.time() < end:
        x, y = ads.read_pair()
        xs.append(x)
        ys.append(y)
    print(f"   x range: {min(xs)} .. {max(xs)}")
    print(f"   y range: {min(ys)} .. {max(ys)}\n")

    cal = {
        "x": {"min": min(xs), "center": cx, "max": max(xs)},
        "y": {"min": min(ys), "center": cy, "max": max(ys)},
    }
    with open(CAL_FILE, "w") as f:
        json.dump(cal, f, indent=2)
    # Make sure the file is usable when the daemon later runs as root.
    try:
        os.chmod(CAL_FILE, 0o644)
    except OSError:
        pass
    print(f"Saved to {CAL_FILE}")
    return cal


def scale_axis(raw, cal, invert):
    """Map a raw ADC count to -32767..32767 with a radial-ish deadzone."""
    center = cal["center"]
    span = (cal["max"] - center) if raw >= center else (center - cal["min"])
    if span <= 0:
        return 0

    norm = (raw - center) / span              # roughly -1.0 .. 1.0
    norm = max(-1.0, min(1.0, norm))

    if abs(norm) < DEADZONE:
        return 0

    # Rescale so the axis still reaches full travel outside the deadzone,
    # instead of jumping from 0 to DEADZONE.
    sign = 1.0 if norm > 0 else -1.0
    norm = sign * (abs(norm) - DEADZONE) / (1.0 - DEADZONE)

    if invert:
        norm = -norm
    return int(norm * AXIS_MAX)


# --------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------

def build_uinput():
    caps = {
        e.EV_KEY: sorted(set(BUTTON_MAP.values())),
        e.EV_ABS: [
            (e.ABS_X, AbsInfo(value=0, min=-AXIS_MAX, max=AXIS_MAX,
                              fuzz=64, flat=0, resolution=0)),
            (e.ABS_Y, AbsInfo(value=0, min=-AXIS_MAX, max=AXIS_MAX,
                              fuzz=64, flat=0, resolution=0)),
        ],
    }
    return UInput(
        caps,
        name="Trax Arcade Panel",
        vendor=0x1209,     # pid.codes open vendor ID
        product=0xA001,
        version=1,
        bustype=e.BUS_USB,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test", action="store_true",
                    help="print live values instead of creating a gamepad")
    ap.add_argument("--calibrate", action="store_true",
                    help="record joystick center and range, then exit")
    args = ap.parse_args()

    bus = SMBus(I2C_BUS)
    ads = ADS1115(bus)

    if args.calibrate:
        run_calibration(ads)
        return

    cal = load_calibration()

    buttons = {
        pin: Button(pin, pull_up=True, bounce_time=0.008)
        for pin in BUTTON_MAP
    }

    ui = None if args.test else build_uinput()
    if ui:
        print("Virtual gamepad up. Check with: ls /dev/input/js*")
        print("Hold Start + Select for 2s to stop.")
    else:
        print("Test mode -- Ctrl-C to stop.\n")

    running = True

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    last_state = {pin: False for pin in BUTTON_MAP}
    last_x = last_y = 0
    combo_since = None
    period = 1.0 / POLL_HZ

    try:
        while running:
            loop_start = time.perf_counter()

            raw_x, raw_y = ads.read_pair()
            x = scale_axis(raw_x, cal["x"], INVERT_X)
            y = scale_axis(raw_y, cal["y"], INVERT_Y)

            pressed = {pin: b.is_pressed for pin, b in buttons.items()}

            # Quit hotkey
            if all(pressed[p] for p in QUIT_COMBO):
                combo_since = combo_since or time.time()
                if time.time() - combo_since >= HOTKEY_HOLD:
                    print("\nHotkey -- shutting down.")
                    break
            else:
                combo_since = None

            if args.test:
                flags = "".join(
                    "1" if pressed[p] else "0" for p in sorted(BUTTON_MAP)
                )
                print(f"\rraw({raw_x:6d},{raw_y:6d})  "
                      f"axis({x:7d},{y:7d})  btn {flags}", end="", flush=True)
            else:
                dirty = False
                if x != last_x:
                    ui.write(e.EV_ABS, e.ABS_X, x)
                    last_x, dirty = x, True
                if y != last_y:
                    ui.write(e.EV_ABS, e.ABS_Y, y)
                    last_y, dirty = y, True
                for pin, code in BUTTON_MAP.items():
                    if pressed[pin] != last_state[pin]:
                        ui.write(e.EV_KEY, code, 1 if pressed[pin] else 0)
                        last_state[pin], dirty = pressed[pin], True
                if dirty:
                    ui.syn()

            slack = period - (time.perf_counter() - loop_start)
            if slack > 0:
                time.sleep(slack)
    finally:
        if ui:
            ui.close()
        for b in buttons.values():
            b.close()
        bus.close()
        print()


if __name__ == "__main__":
    if os.geteuid() != 0 and "--test" not in sys.argv and "--calibrate" not in sys.argv:
        print("Needs access to /dev/uinput -- run with sudo, "
              "or install the udev rule in SETUP.md.", file=sys.stderr)
    main()
