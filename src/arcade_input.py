#!/usr/bin/env python3
"""
arcade_input.py
===============
Joystick + buttons -> virtual keyboard, with a key profile per game.

Direction decoding lives in padmap.py, shared with the launcher, so the
90-degree stick rotation is applied identically everywhere.

    doom       A=LeftCtrl(fire)  B=Space(use)     Start=Enter  Select=Esc
    shooter    A=Space(fire)     B=LeftCtrl(alt)  Start=Enter  Select=Esc
    platform   A=Space(jump)     B=LeftShift(run) Start=Enter  Select=Esc
    menu       A=Enter           B=Esc            Start=Enter  Select=Esc

Start+Select tap  -> sends Y (confirms Doom's quit prompt)
Start+Select hold -> force-kills --quit-target

Usage:
    sudo python3 arcade_input.py --profile doom --quit-target chocolate-doom
    python3 arcade_input.py --test
"""

import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smbus2 import SMBus
from gpiozero import Button, Device
from evdev import UInput, ecodes as e

import padmap

PROFILES = {
    "doom":     {"a": e.KEY_LEFTCTRL, "b": e.KEY_SPACE,
                 "start": e.KEY_ENTER, "select": e.KEY_ESC},
    "shooter":  {"a": e.KEY_SPACE, "b": e.KEY_LEFTCTRL,
                 "start": e.KEY_ENTER, "select": e.KEY_ESC},
    "platform": {"a": e.KEY_SPACE, "b": e.KEY_LEFTSHIFT,
                 "start": e.KEY_ENTER, "select": e.KEY_ESC},
    "menu":     {"a": e.KEY_ENTER, "b": e.KEY_ESC,
                 "start": e.KEY_ENTER, "select": e.KEY_ESC},
}

DIR_KEYS = {"up": e.KEY_UP, "down": e.KEY_DOWN,
            "left": e.KEY_LEFT, "right": e.KEY_RIGHT}

HOLD_SECS = 2.0


def release_gpio():
    """Fully release the pins. Button.close() alone leaves the lgpio
    chip handle open at the factory level, so pins stay busy."""
    try:
        if Device.pin_factory is not None:
            Device.pin_factory.close()
    except Exception:
        pass
    Device.pin_factory = None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="doom", choices=sorted(PROFILES))
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--quit-target", default=None, metavar="PROC")
    args = ap.parse_args()

    keymap = PROFILES[args.profile]
    bus = SMBus(1)

    print("Calibrating -- leave the stick alone...")
    ch, cv = padmap.calibrate(bus)
    print(f"center h={ch} v={cv}")

    btns = {n: Button(p, pull_up=True, bounce_time=0.01)
            for n, p in padmap.BTN_PINS.items()}

    ui = None
    if not args.test:
        caps = sorted(set(list(keymap.values()) + list(DIR_KEYS.values())
                          + [e.KEY_Y, e.KEY_N]))
        ui = UInput({e.EV_KEY: caps}, name="Trax Arcade Keyboard")
        print(f"Bridge live [{args.profile}]"
              + (f" quit-target={args.quit_target}" if args.quit_target else ""))
    else:
        print("TEST MODE -- Ctrl-C to stop")

    run = True

    def stop(*_):
        nonlocal run
        run = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    prev = {}
    combo_since = None
    combo_fired = False

    try:
        while run:
            s = padmap.decode(bus, ch, cv)
            for n, b in btns.items():
                s[n] = b.is_pressed

            # --- Start + Select: tap sends Y, hold force-quits
            if s["start"] and s["select"]:
                if combo_since is None:
                    combo_since = time.time()
                    combo_fired = False
                elif not combo_fired and time.time() - combo_since >= HOLD_SECS:
                    if args.quit_target:
                        print(f"\nforce-quitting {args.quit_target}")
                        os.system(f"pkill -TERM -f {args.quit_target} 2>/dev/null")
                        time.sleep(1.0)
                        os.system(f"pkill -KILL -f {args.quit_target} 2>/dev/null")
                    combo_fired = True
            else:
                if combo_since is not None and not combo_fired and ui:
                    ui.write(e.EV_KEY, e.KEY_Y, 1); ui.syn()
                    time.sleep(0.05)
                    ui.write(e.EV_KEY, e.KEY_Y, 0); ui.syn()
                    print("sent Y")
                combo_since = None
                combo_fired = False

            if args.test:
                d = "".join(k[0].upper() if s[k] else "."
                            for k in ("up", "down", "left", "right"))
                print(f"\rdir[{d}] " +
                      " ".join(f"{n}:{int(s[n])}" for n in
                               sorted(padmap.BTN_PINS)),
                      end="", flush=True)
            else:
                dirty = False
                pairs = [(k, DIR_KEYS[k]) for k in DIR_KEYS]
                pairs += [(n, keymap[n]) for n in keymap]
                for name, code in pairs:
                    if s[name] != prev.get(name):
                        ui.write(e.EV_KEY, code, 1 if s[name] else 0)
                        dirty = True
                if dirty:
                    ui.syn()

            prev = s
            time.sleep(0.016)
    finally:
        if ui:
            ui.close()
        for b in btns.values():
            try:
                b.close()
            except Exception:
                pass
        release_gpio()
        bus.close()
        print("\nbridge stopped")


if __name__ == "__main__":
    if os.geteuid() != 0 and "--test" not in sys.argv:
        print("Needs /dev/uinput -- run with sudo.", file=sys.stderr)
    main()
