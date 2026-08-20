#!/usr/bin/env python3
"""
arcade_input.py
===============
Joystick + buttons -> virtual keyboard, with a key profile per game.

Why a keyboard and not a gamepad: a virtual gamepad only works once udev
permissions, the SDL input driver and the emulator's autodetection all
agree, and each of those fails differently. Every game already reads a
keyboard. Presenting one removed an entire class of failure -- see
docs/DEBUGGING.md.

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
    python3 arcade_input.py --test      # no uinput, no root, prints state
"""

import argparse
import os
import signal
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from smbus2 import SMBus
from gpiozero import Button, Device
from evdev import UInput, ecodes as e

import config
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


def release_gpio():
    """Fully release the pins.

    Button.close() releases the Button but not the pins: gpiozero's lgpio
    backend keeps the chip handle open at the factory level, and the next
    process to ask for GPIO5 gets 'GPIO busy'. Closing the factory and
    dropping the reference is what actually frees them.
    """
    try:
        if Device.pin_factory is not None:
            Device.pin_factory.close()
    except Exception:
        pass
    Device.pin_factory = None


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="doom", choices=sorted(PROFILES))
    ap.add_argument("--test", action="store_true",
                    help="print decoded state instead of emitting keys")
    ap.add_argument("--quit-target", default=None, metavar="PROC",
                    help="process name killed by holding Start+Select")
    args = ap.parse_args()

    keymap = PROFILES[args.profile]
    bus = SMBus(config.I2C_BUS)

    print("Calibrating -- leave the stick alone...")
    ch, cv = padmap.calibrate(bus)
    print(f"center h={ch} v={cv}")
    if abs(ch - config.ADC_CENTER_EXPECTED_3V3) > 4000:
        print(f"  note: centre is far from the ~{config.ADC_CENTER_EXPECTED_3V3}"
              " expected on a 3.3V rail -- check the joystick's VCC")

    btns = {n: Button(p, pull_up=True, bounce_time=config.BUTTON_BOUNCE_GAME)
            for n, p in padmap.BTN_PINS.items()}

    ui = None
    if not args.test:
        caps = sorted(set(list(keymap.values()) + list(DIR_KEYS.values())
                          + [e.KEY_Y, e.KEY_N]))
        ui = UInput({e.EV_KEY: caps}, name="T-Arcade Keyboard")
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

            # --- Start + Select: tap sends Y, hold force-quits.
            # Doom asks "quit? (y/n)" and there is no Y button on the
            # cabinet, so the tap answers it. The hold is the escape
            # hatch for games that ignore Escape entirely.
            if s["start"] and s["select"]:
                if combo_since is None:
                    combo_since = time.time()
                    combo_fired = False
                elif (not combo_fired
                      and time.time() - combo_since >= config.COMBO_HOLD_SECS):
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
                # Only emit on change: a held direction is one keydown,
                # not one per poll, or the game sees key repeat storms.
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
            time.sleep(config.INPUT_POLL_INTERVAL)
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
