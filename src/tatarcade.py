#!/usr/bin/env python3
"""
T-ATARCADE
==========
Cabinet front-end. Pick a game with the stick, launch with A.

The launcher reads the joystick and buttons directly. When a game
starts it releases the GPIO pins, hands them to arcade_input.py with
that game's key profile, runs the game, then takes the pins back when
the game exits.

Only installed games appear in the menu.

    sudo python3 tatarcade.py            # on the panel (HDMI off)
    sudo python3 tatarcade.py --monitor  # test on the monitor
"""

import argparse
import os
import subprocess
import sys
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame
from smbus2 import SMBus
from gpiozero import Button

W, H, FPS = 320, 240, 30
DIR = os.path.dirname(os.path.abspath(__file__))

BLACK = (10, 8, 16)
WHITE = (238, 238, 240)
GREY = (105, 105, 122)
DIM = (60, 60, 76)
RED = (232, 62, 52)
AMBER = (250, 176, 46)
CYAN = (68, 212, 236)
GREEN = (86, 216, 108)
PINK = (238, 104, 186)

WAD = "/usr/share/games/doom/freedoom1.wad"
WAD2 = "/usr/share/games/doom/freedoom2.wad"

# name, command, key profile, accent colour, availability test
GAMES = [
    ("DOOM",              ["chocolate-doom", "-iwad", WAD, "-fullscreen"],
     "doom", RED, lambda: os.path.exists(WAD)),
    ("DOOM II",           ["chocolate-doom", "-iwad", WAD2, "-fullscreen"],
     "doom", RED, lambda: os.path.exists(WAD2)),
    ("OPENTYRIAN",        ["opentyrian"], "shooter", CYAN, None),
    ("PRINCE OF PERSIA",  ["sdlpop"], "platform", AMBER, None),
    ("NJAM",              ["njam"], "menu", PINK, None),
    ("KOBO DELUXE",       ["kobodl"], "shooter", GREEN, None),
    ("CHROMIUM B.S.U.",   ["chromium-bsu"], "shooter", CYAN, None),
    ("SUPERTUX",          ["supertux2"], "platform", GREEN, None),
]

MUX = {0: 0x4000, 1: 0x5000, 2: 0x6000, 3: 0x7000}
BASE = 0x8000 | 0x0200 | 0x0100 | 0x00E0 | 0x0003
SPAN, DZ = 9400, 0.50
BTN_PINS = {"a": 5, "b": 6, "start": 20, "select": 21}


class Pad:
    """Joystick + buttons, read straight from the hardware."""

    def __init__(self):
        self.bus = SMBus(1)
        self.btn = {}
        self.open_pins()
        sx = sy = 0
        for _ in range(25):
            sx += self.adc(1); sy += self.adc(3)
        self.cx, self.cy = sx // 25, sy // 25

    def adc(self, ch):
        cfg = BASE | MUX[ch]
        self.bus.write_i2c_block_data(0x48, 1,
                                      [(cfg >> 8) & 0xFF, cfg & 0xFF])
        time.sleep(0.0015)
        hi, lo = self.bus.read_i2c_block_data(0x48, 0, 2)
        r = (hi << 8) | lo
        return r - 65536 if r & 0x8000 else r

    def open_pins(self):
        for name, pin in BTN_PINS.items():
            self.btn[name] = Button(pin, pull_up=True, bounce_time=0.02)

    def release_pins(self):
        """Free the GPIO so a game's input bridge can claim it."""
        for b in self.btn.values():
            b.close()
        self.btn.clear()

    def read(self):
        x = -(self.adc(1) - self.cx) / SPAN
        y = -(self.adc(3) - self.cy) / SPAN
        s = {"left": x < -DZ, "right": x > DZ,
             "up": y < -DZ, "down": y > DZ}
        for name in BTN_PINS:
            s[name] = self.btn[name].is_pressed if name in self.btn else False
        return s

    def close(self):
        self.release_pins()
        self.bus.close()


def available(entry):
    name, cmd, prof, col, test = entry
    if test is not None:
        return test()
    return subprocess.call(["which", cmd[0]],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) == 0


def font(sz, cache={}):
    if sz not in cache:
        cache[sz] = pygame.font.Font(None, sz)
    return cache[sz]


def text(surf, s, x, y, col=WHITE, sz=16, center=False):
    img = font(sz).render(s, False, col)
    r = img.get_rect()
    if center:
        r.center = (x, y)
    else:
        r.topleft = (x, y)
    surf.blit(img, r)
    return r


def draw_menu(c, games, sel, frame, launching):
    c.fill(BLACK)

    # marquee
    hue = (frame * 2) % 360
    col = pygame.Color(0)
    col.hsva = (hue, 72, 100, 100)
    text(c, "T-ATARCADE", W // 2, 26, col, 40, center=True)
    pygame.draw.line(c, DIM, (24, 44), (W - 24, 44))

    if not games:
        text(c, "NO GAMES INSTALLED", W // 2, 110, GREY, 20, center=True)
        text(c, "sudo apt install opentyrian sdlpop njam",
             W // 2, 132, DIM, 12, center=True)
        return

    # window of 6 rows around the selection
    top = max(0, min(sel - 2, len(games) - 6))
    rows = games[top:top + 6]
    for i, (name, cmd, prof, accent, _t) in enumerate(rows):
        idx = top + i
        y = 60 + i * 22
        if idx == sel:
            pygame.draw.rect(c, (28, 28, 44), (18, y - 3, W - 36, 20))
            pygame.draw.rect(c, accent, (18, y - 3, 3, 20))
            text(c, name, 32, y, accent, 20)
        else:
            text(c, name, 32, y, GREY, 18)

    if len(games) > 6:
        text(c, f"{sel + 1}/{len(games)}", W - 26, 200, DIM, 14, center=True)

    if launching:
        pygame.draw.rect(c, BLACK, (40, 100, W - 80, 40))
        pygame.draw.rect(c, AMBER, (40, 100, W - 80, 40), 1)
        text(c, "LOADING...", W // 2, 120, AMBER, 24, center=True)
    else:
        pygame.draw.line(c, DIM, (24, 206), (W - 24, 206))
        if (frame // 14) % 2:
            text(c, "A = PLAY", W // 2, 218, WHITE, 18, center=True)
        else:
            text(c, "HOLD SELECT = QUIT", W // 2, 218, GREY, 16, center=True)


def run_game(entry, pad, on_panel):
    """Release the pins, run the game with its bridge, then take them back."""
    name, cmd, profile, accent, _t = entry
    print(f"\n=== {name} ===")

    pad.release_pins()
    time.sleep(0.3)

    bridge = subprocess.Popen(
        ["sudo", "python3", os.path.join(DIR, "arcade_input.py"),
         "--profile", profile],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(2.5)

    try:
        subprocess.call(cmd)
    except FileNotFoundError:
        print(f"{cmd[0]} not installed")
    finally:
        bridge.terminate()
        try:
            bridge.wait(timeout=3)
        except subprocess.TimeoutExpired:
            bridge.kill()
        subprocess.call(["sudo", "pkill", "-9", "-f", "arcade_input.py"],
                        stderr=subprocess.DEVNULL)
        time.sleep(1.0)
        pad.open_pins()
    print("=== back to menu ===")


def main():
    ap = argparse.ArgumentParser(description="T-ATARCADE")
    ap.add_argument("--monitor", action="store_true",
                    help="run on the monitor instead of the panel")
    args = ap.parse_args()

    games = [g for g in GAMES if available(g)]
    print(f"{len(games)} game(s) found")

    on_panel = not args.monitor
    big = None
    if on_panel:
        try:
            out = subprocess.check_output(["wlr-randr"], text=True)
            for line in out.splitlines():
                if line and line[0].isalpha() and "SPI" not in line:
                    big = line.split()[0]
                    break
        except Exception:
            on_panel = False
        if big:
            subprocess.call(["wlr-randr", "--output", big, "--off"])
            time.sleep(1.5)

    def restore():
        if big:
            subprocess.call(["wlr-randr", "--output", big, "--on"])

    pygame.init()
    screen = pygame.display.set_mode(
        (W, H), pygame.FULLSCREEN if on_panel else 0)
    pygame.display.set_caption("T-ATARCADE")
    pygame.mouse.set_visible(False)
    canvas = pygame.Surface((W, H))
    clock = pygame.time.Clock()

    pad = Pad()
    sel, frame, prev = 0, 0, {}
    hold_select = 0
    launching = 0
    running = True

    try:
        while running:
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_ESCAPE:
                    running = False

            s = pad.read()
            hit = lambda k: s[k] and not prev.get(k)

            if games and not launching:
                if hit("up"):
                    sel = (sel - 1) % len(games)
                if hit("down"):
                    sel = (sel + 1) % len(games)
                if hit("a") or hit("start"):
                    launching = 1

            if s["select"]:
                hold_select += 1
                if hold_select > FPS * 2:
                    running = False
            else:
                hold_select = 0

            draw_menu(canvas, games, sel, frame, launching)
            screen.blit(canvas, (0, 0))
            pygame.display.flip()

            if launching:
                launching += 1
                if launching > 8:
                    launching = 0
                    run_game(games[sel], pad, on_panel)
                    prev = {}
                    pygame.event.clear()
                    continue

            prev = s
            frame += 1
            clock.tick(FPS)
    finally:
        pad.close()
        pygame.quit()
        restore()
        print("T-ATARCADE closed")


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Run with sudo (needs GPIO).", file=sys.stderr)
    main()
