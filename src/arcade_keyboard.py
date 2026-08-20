#!/usr/bin/env python3
"""
arcade_keyboard.py
==================
Same hardware as arcade_gamepad.py, but presents a virtual KEYBOARD
instead of a virtual gamepad.

Why: gamepad detection needs udev permissions, the right input driver,
and autodetection to all line up. Keyboard input needs none of that --
every emulator and game already reads the keyboard. This just works.

Key mapping matches RetroArch's default keyboard binds, so nothing
needs configuring:

    stick up/down/left/right  ->  arrow keys
    button A                  ->  X   (RetroArch "A")
    button B                  ->  Z   (RetroArch "B")
    Start                     ->  Enter
    Select                    ->  Right Shift
    Start + Select together   ->  Escape  (quits RetroArch)

Hardware:
    ADS1115 on I2C1 @ 0x48, joystick VRx->A1, VRy->A3
    Buttons active-low on GPIO with internal pull-ups

Usage:
    sudo python3 arcade_keyboard.py           # run it
    python3 arcade_keyboard.py --test         # print values, no uinput
    python3 arcade_keyboard.py --calibrate    # record stick center/range
"""

import argparse
import json
import os
import signal
import sys
import time

from smbus2 import SMBus
from gpiozero import Button
from evdev import UInput, ecodes as e

I2C_BUS = 1
ADS_ADDR = 0x48
CAL_FILE = os.path.expanduser("~/.arcade_calibration.json")

# BCM GPIO -> key code
BUTTON_KEYS = {
    5:  e.KEY_X,          # A button
    6:  e.KEY_Z,          # B button
    20: e.KEY_ENTER,      # Start
    21: e.KEY_RIGHTSHIFT, # Select
    26: e.KEY_SPACE,      # stick push-down
}

# Held together, these send Escape (quits RetroArch cleanly)
ESCAPE_COMBO = (20, 21)
ESCAPE_HOLD = 1.5

DIR_KEYS = {
    "up": e.KEY_UP, "down": e.KEY_DOWN,
    "left": e.KEY_LEFT, "right": e.KEY_RIGHT,
}

POLL_HZ = 60
DEADZONE = 0.45          # higher than gamepad mode -- digital, not analog
INVERT_X = False
INVERT_Y = True


class ADS1115:
    REG_CONVERSION, REG_CONFIG = 0x00, 0x01
    _MUX = {0: 0x4000, 1: 0x5000, 2: 0x6000, 3: 0x7000}
    _BASE = 0x8000 | 0x0200 | 0x0100 | 0x00E0 | 0x0003
    CONV_TIME = 0.0014

    def __init__(self, bus, addr=ADS_ADDR):
        self.bus, self.addr = bus, addr

    def read(self, ch):
        cfg = self._BASE | self._MUX[ch]
        self.bus.write_i2c_block_data(self.addr, self.REG_CONFIG,
                                      [(cfg >> 8) & 0xFF, cfg & 0xFF])
        time.sleep(self.CONV_TIME)
        hi, lo = self.bus.read_i2c_block_data(self.addr, self.REG_CONVERSION, 2)
        raw = (hi << 8) | lo
        return raw - (1 << 16) if raw & 0x8000 else raw

    def read_pair(self):
        """VRx on A1, VRy on A3 -- matches this build's wiring."""
        return self.read(1), self.read(3)


DEFAULT_CAL = {"x": {"min": 0, "center": 13200, "max": 26400},
               "y": {"min": 0, "center": 13200, "max": 26400}}


def load_cal():
    try:
        with open(CAL_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return dict(DEFAULT_CAL)


def run_calibration(ads):
    input("Center the stick and press Enter... ")
    s = [ads.read_pair() for _ in range(50)]
    cx = sum(v[0] for v in s) // len(s)
    cy = sum(v[1] for v in s) // len(s)
    print(f"center x={cx} y={cy}")
    input("Now roll the stick around its edge for 5s, press Enter... ")
    xs, ys = [cx], [cy]
    end = time.time() + 5
    while time.time() < end:
        x, y = ads.read_pair()
        xs.append(x); ys.append(y)
    cal = {"x": {"min": min(xs), "center": cx, "max": max(xs)},
           "y": {"min": min(ys), "center": cy, "max": max(ys)}}
    with open(CAL_FILE, "w") as f:
        json.dump(cal, f, indent=2)
    try:
        os.chmod(CAL_FILE, 0o644)
    except OSError:
        pass
    print(f"x {min(xs)}..{max(xs)}   y {min(ys)}..{max(ys)}")
    print(f"Saved {CAL_FILE}")


def norm(raw, cal, invert):
    c = cal["center"]
    span = (cal["max"] - c) if raw >= c else (c - cal["min"])
    if span <= 0:
        return 0.0
    v = max(-1.0, min(1.0, (raw - c) / span))
    return -v if invert else v


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--calibrate", action="store_true")
    args = ap.parse_args()

    bus = SMBus(I2C_BUS)
    ads = ADS1115(bus)

    if args.calibrate:
        run_calibration(ads)
        return

    cal = load_cal()
    buttons = {p: Button(p, pull_up=True, bounce_time=0.008)
               for p in BUTTON_KEYS}

    ui = None
    if not args.test:
        caps = {e.EV_KEY: sorted(set(BUTTON_KEYS.values())
                                 | set(DIR_KEYS.values())
                                 | {e.KEY_ESC})}
        ui = UInput(caps, name="Trax Arcade Keyboard",
                    vendor=0x1209, product=0xA002, bustype=e.BUS_USB)
        print("Virtual keyboard active.")
        print("Stick = arrows, A = X, B = Z, Start = Enter, "
              "Select = RShift, Start+Select = Escape")
    else:
        print("Test mode. Ctrl-C to stop.\n")

    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    prev = {k: False for k in
            list(DIR_KEYS) + [f"btn{p}" for p in BUTTON_KEYS]}
    combo_since = None
    esc_sent = False
    period = 1.0 / POLL_HZ

    try:
        while running:
            t0 = time.perf_counter()
            rx, ry = ads.read_pair()
            x = norm(rx, cal["x"], INVERT_X)
            y = norm(ry, cal["y"], INVERT_Y)

            state = {
                "left": x < -DEADZONE, "right": x > DEADZONE,
                "up": y < -DEADZONE, "down": y > DEADZONE,
            }
            for p, b in buttons.items():
                state[f"btn{p}"] = b.is_pressed

            if args.test:
                d = "".join(k[0].upper() if state[k] else "."
                            for k in ("up", "down", "left", "right"))
                bs = " ".join(f"{p}:{int(state[f'btn{p}'])}"
                              for p in sorted(BUTTON_KEYS))
                print(f"\rraw({rx:6d},{ry:6d}) dir[{d}] {bs}",
                      end="", flush=True)
            else:
                dirty = False
                for name, code in DIR_KEYS.items():
                    if state[name] != prev[name]:
                        ui.write(e.EV_KEY, code, 1 if state[name] else 0)
                        dirty = True
                for p, code in BUTTON_KEYS.items():
                    k = f"btn{p}"
                    if state[k] != prev[k]:
                        ui.write(e.EV_KEY, code, 1 if state[k] else 0)
                        dirty = True

                if all(state[f"btn{p}"] for p in ESCAPE_COMBO):
                    combo_since = combo_since or time.time()
                    if not esc_sent and time.time() - combo_since >= ESCAPE_HOLD:
                        ui.write(e.EV_KEY, e.KEY_ESC, 1)
                        ui.syn()
                        ui.write(e.EV_KEY, e.KEY_ESC, 0)
                        dirty, esc_sent = True, True
                else:
                    combo_since, esc_sent = None, False

                if dirty:
                    ui.syn()

            prev = state
            slack = period - (time.perf_counter() - t0)
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
    if os.geteuid() != 0 and not {"--test", "--calibrate"} & set(sys.argv):
        print("Needs /dev/uinput -- run with sudo.", file=sys.stderr)
    main()
