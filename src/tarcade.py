#!/usr/bin/env python3
"""
T-ARCADE
========
Cabinet front-end. Pick a game with the stick, launch with A.

Design notes:
  * Direction decoding lives in padmap.py, shared with arcade_input.py,
    so the 90-degree stick rotation applies identically everywhere.
  * The launcher owns the GPIO for the menu, fully releases it (closing
    the gpiozero pin factory, not just the Buttons) before a game runs,
    then reclaims it afterwards.
  * Games run as the desktop user, not root, so their configs live in
    one place and settings made outside the launcher still apply.
  * Games start in their own process group and are torn down by group,
    then swept by name, so nothing survives into the menu.
  * The SDL display is rebuilt after every game -- a game that takes the
    display invalidates our surface and the menu would otherwise vanish.

    sudo -E python3 tarcade.py            # panel (HDMI off)
    sudo -E python3 tarcade.py --monitor  # test on the monitor
"""

import argparse
import getpass
import os
import pwd
import shutil
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ["SDL_AUDIODRIVER"] = "dummy"      # never hold the audio device

import pygame
from smbus2 import SMBus
from gpiozero import Button, Device

import padmap
from btaudio import BTAudio

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

# ---------------------------------------------------------------------------
# Terminal games
# ---------------------------------------------------------------------------
# Debian Trixie no longer ships the legacy bitmap fonts (4x6 etc), so we
# use a scalable Xft font. xterm's -fullscreen is only a request to the
# window manager and XWayland ignores it -- the window keeps its default
# 80x24 whatever font is set, starving games that need more rows. So the
# grid is computed from the live screen resolution and passed as an
# explicit -geometry instead.

TERM_FONT = "DejaVu Sans Mono"
TERM_MIN_COLS = 80
TERM_MIN_ROWS = 24

# DejaVu Sans Mono advances 0.602 em; at 96 dpi an Xft size of S points
# gives a cell roughly S * (96/72) * 0.602 = S * 0.803 px wide.
_CELL_W_PER_PT = 0.803
_CELL_H_PER_PT = 1.70


def active_screen_size():
    """Resolution of whichever output is currently enabled."""
    try:
        out = subprocess.check_output(["wlr-randr"], text=True,
                                      stderr=subprocess.DEVNULL, timeout=5)
    except Exception:
        return (W, H)
    name = None
    info = {}
    for line in out.splitlines():
        if line and line[0].isalpha():
            name = line.split()[0]
            info[name] = {"on": False, "mode": None}
        elif name:
            t = line.strip()
            if t.startswith("Enabled:"):
                info[name]["on"] = t.lower().endswith("yes")
            elif "current" in t and "px" in t and info[name]["mode"] is None:
                info[name]["mode"] = t.split()[0]
    for n, d in info.items():
        if d["on"] and d["mode"] and "x" in d["mode"]:
            try:
                sw, sh = d["mode"].split("x")
                return (int(sw), int(sh))
            except ValueError:
                pass
    return (W, H)


def term(binary, min_cols=None, min_rows=None):
    """Build an xterm argv that fills the active screen."""
    cols_min = min_cols or TERM_MIN_COLS
    rows_min = min_rows or TERM_MIN_ROWS
    sw, sh = active_screen_size()

    size = max(3, int(min(sw / (cols_min * _CELL_W_PER_PT),
                          sh / (rows_min * _CELL_H_PER_PT))))
    cols = max(cols_min, int(sw / (size * _CELL_W_PER_PT)))
    rows = max(rows_min, int(sh / (size * _CELL_H_PER_PT)))

    print(f"  {os.path.basename(binary)}: screen {sw}x{sh} -> "
          f"{size}pt -> {cols}x{rows} chars (needs {cols_min}x{rows_min})")

    return ["/usr/bin/xterm",
            "-fa", TERM_FONT,
            "-fs", str(size),
            "-geometry", f"{cols}x{rows}+0+0",
            "+sb", "-b", "0", "-bw", "0",
            "-bg", "black", "-fg", "white",
            "-e", binary]


def have(path):
    return lambda: os.path.exists(path)


POWER_OFF = "__poweroff__"
MONITOR_ON = "__monitoron__"
AUDIO_DEV  = "__audiodev__"

# name, argv, key profile, colour, availability test, env, extra kill names
# argv is a command list, or a ("TERM", binary[, cols, rows]) marker that
# resolve() expands at launch time.
GAMES = [
    ("DOOM",       ["/usr/games/chocolate-doom", "-iwad", WAD, "-fullscreen"],
     "doom", RED, lambda: os.path.exists(WAD), {}),
    ("OPENTYRIAN", ["/usr/games/opentyrian"],
     "shooter", CYAN, None, {}),
    ("NJAM",       ["/usr/games/njam"],
     "menu", PINK, None, {"SDL_VIDEODRIVER": "x11"}),
    ("SUPERTUX",   ["/usr/games/supertux2", "--fullscreen",
                    "--geometry", "320x240"],
     "platform", GREEN, have("/usr/games/supertux2"), {}),

    ("BURGERTIME", ["/usr/games/burgerspace"],
     "shooter", AMBER, have("/usr/games/burgerspace"), {}),
    ("XGALAGA",    ["/usr/games/xgalaga", "-geometry", "320x240+0+0"],
     "shooter", CYAN, have("/usr/games/xgalaga"), {}),
    ("CHROMIUM BSU", ["/usr/games/chromium-bsu"],
     "shooter", GREEN, have("/usr/games/chromium-bsu"), {}),

    # ncurses games, run inside xterm
    ("NINVADERS",  ("TERM", "/usr/games/ninvaders"),
     "shooter", GREEN, have("/usr/games/ninvaders"), {}, ["ninvaders"]),
    ("BASTET",     ("TERM", "/usr/games/bastet"),
     "shooter", CYAN, have("/usr/games/bastet"), {}, ["bastet"]),
]


def resolve(cmd):
    """Expand a ("TERM", binary[, cols, rows]) marker into an xterm argv.

    Deferred to launch time so the font is sized for the output that is
    live right then, not whatever was active at import.
    """
    if isinstance(cmd, tuple) and cmd and cmd[0] == "TERM":
        return term(cmd[1],
                    cmd[2] if len(cmd) > 2 else None,
                    cmd[3] if len(cmd) > 3 else None)
    return cmd


def available(entry):
    cmd, test = entry[1], entry[4]
    if cmd in (POWER_OFF, MONITOR_ON, AUDIO_DEV):
        return True
    if isinstance(cmd, tuple) and cmd and cmd[0] == "TERM":
        return os.path.exists(cmd[1]) and os.path.exists("/usr/bin/xterm")
    if test is not None:
        return test()
    if cmd[0].startswith("/"):
        return os.path.exists(cmd[0])
    return subprocess.call(["which", cmd[0]],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) == 0


def real_user():
    """The desktop user, even when running under sudo."""
    v = os.environ.get("SUDO_USER")
    if v and v != "root":
        return v
    try:
        return pwd.getpwuid(os.stat(DIR).st_uid).pw_name
    except Exception:
        return getpass.getuser()


USER = real_user()


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

class Pad:
    """Joystick + buttons, read straight from the hardware."""

    def __init__(self):
        self.bus = SMBus(1)
        self.btn = {}
        self.open_pins()
        self.ch, self.cv = padmap.calibrate(self.bus)
        print(f"stick center h={self.ch} v={self.cv}")

    def open_pins(self):
        for name, pin in padmap.BTN_PINS.items():
            self.btn[name] = Button(pin, pull_up=True, bounce_time=0.02)

    def release_pins(self):
        """Fully free the GPIO so the input bridge can claim it.

        Button.close() is not enough: gpiozero's lgpio backend holds the
        chip handle at the factory level, leaving pins busy.
        """
        for b in self.btn.values():
            try:
                b.close()
            except Exception:
                pass
        self.btn.clear()
        try:
            if Device.pin_factory is not None:
                Device.pin_factory.close()
        except Exception:
            pass
        Device.pin_factory = None

    def read(self):
        s = padmap.decode(self.bus, self.ch, self.cv)
        for name in padmap.BTN_PINS:
            s[name] = self.btn[name].is_pressed if name in self.btn else False
        return s

    def close(self):
        self.release_pins()
        try:
            self.bus.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Process teardown
# ---------------------------------------------------------------------------

def force_fullscreen(pid, hints=(), tries=14, delay=0.7):
    """Make a game's window fill the screen after it opens.

    Games open at whatever size they were built for, and their own
    fullscreen flags are inconsistent (OpenTyrian has none, xterm's is
    ignored under XWayland). Rather than special-case each one, ask the
    window manager to fullscreen whatever window the process opened:
    they are X11 clients under XWayland, so the compositor honours the
    EWMH _NET_WM_STATE_FULLSCREEN hint even though the game never sets it.

    Windows are located by pid first, then by the name/class hints, then
    by whatever holds focus -- games normally take focus as they map.
    Retried for a few seconds because the window may not exist yet, and
    some games resize themselves shortly after mapping.
    """
    if not (shutil.which("wmctrl") and shutil.which("xdotool")):
        return None

    def find_window(env):
        for args in ([["xdotool", "search", "--pid", str(pid)]] if pid else []):
            wid = _xdotool_last(args, env)
            if wid:
                return wid
        for hint in hints:
            for flag in ("--name", "--class"):
                wid = _xdotool_last(
                    ["xdotool", "search", "--onlyvisible", flag, hint], env)
                if wid:
                    return wid
        return _xdotool_last(["xdotool", "getactivewindow"], env)

    def worker():
        seen = None
        for _ in range(tries):
            time.sleep(delay)
            env = wlr_env()
            env.setdefault("DISPLAY", ":0")
            wid = find_window(env)
            if not wid:
                continue
            if wid != seen:
                print(f"  fullscreening window {wid}")
                seen = wid
            for cmd in (["wmctrl", "-i", "-r", wid, "-b", "add,fullscreen"],
                        ["xdotool", "windowactivate", wid],
                        ["xdotool", "windowsize", wid, "100%", "100%"],
                        ["xdotool", "windowmove", wid, "0", "0"]):
                try:
                    subprocess.run(cmd, capture_output=True, timeout=5, env=env)
                except Exception:
                    pass
        if seen is None:
            print("  fullscreen: no window found")

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return t


def _xdotool_last(args, env):
    """Last window id printed by an xdotool call, or "" -- never raises."""
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=6, env=env).stdout
    except Exception:
        return ""
    ids = [w for w in out.split() if w.strip()]
    return ids[-1] if ids else ""


def kill_tree(proc, names, timeout=6.0):
    """Terminate a game and everything it spawned.

    Escalating: SIGTERM the process group so the game can save, SIGKILL
    the group if it survives, then sweep by name until pgrep is clean.
    """
    if proc is not None and proc.poll() is None:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:
            try:
                proc.terminate()
            except Exception:
                pass
        deadline = time.time() + timeout
        while time.time() < deadline and proc.poll() is None:
            time.sleep(0.15)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass

    for name in names:
        for attempt in range(12):
            alive = subprocess.call(["pgrep", "-f", name],
                                    stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL) == 0
            if not alive:
                break
            subprocess.call(["pkill", "-TERM" if attempt < 3 else "-KILL",
                             "-f", name],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL)
            time.sleep(0.35)
        else:
            print(f"warning: {name} still running after cleanup")


def run_game(entry, pad):
    name, profile = entry[0], entry[2]
    cmd = resolve(entry[1])
    extra_env = entry[5] if len(entry) > 5 else {}
    extra_kill = list(entry[6]) if len(entry) > 6 else []
    print(f"\n=== {name} ===")

    target = os.path.basename(cmd[0])
    if target == "xterm" and "-e" in cmd:
        extra_kill.append(os.path.basename(cmd[cmd.index("-e") + 1]))

    pad.release_pins()
    time.sleep(1.0)

    bridge = game = None
    try:
        bridge = subprocess.Popen(
            ["sudo", "python3", os.path.join(DIR, "arcade_input.py"),
             "--profile", profile, "--quit-target", target],
            stdout=open("/tmp/bridge.log", "w"),
            stderr=subprocess.STDOUT,
            start_new_session=True)
        time.sleep(2.5)

        env = dict(os.environ)
        env.update(extra_env)
        env.pop("SDL_AUDIODRIVER", None)       # games may want audio
        if extra_env:
            print(f"env: {extra_env}")

        argv = cmd if os.geteuid() != 0 else ["sudo", "-u", USER, "-E"] + cmd
        game = subprocess.Popen(argv, env=env, start_new_session=True)

        # Fullscreen the window once it appears. Terminal games already
        # get an explicit geometry, so they are skipped.
        if target != "xterm":
            force_fullscreen(game.pid,
                             hints=[os.path.basename(cmd[0]),
                                    name.lower().split()[0]])

        game.wait()
    except FileNotFoundError:
        print(f"{cmd[0]} not installed")
    except Exception as err:
        print(f"launch error: {err}")
    finally:
        kill_tree(game, [target] + extra_kill)
        kill_tree(bridge, ["arcade_input.py"])
        time.sleep(1.2)
        try:
            pad.open_pins()
        except Exception as err:
            print(f"could not reclaim GPIO: {err}")
    print("=== back to menu ===")


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------

_fonts = {}


def font(sz):
    if sz not in _fonts:
        _fonts[sz] = pygame.font.Font(None, sz)
    return _fonts[sz]


def text(surf, s, x, y, col=WHITE, sz=16, center=False):
    img = font(sz).render(s, False, col)
    r = img.get_rect()
    if center:
        r.center = (x, y)
    else:
        r.topleft = (x, y)
    surf.blit(img, r)
    return r


def draw_menu(c, items, sel, frame, launching, confirm_off, off_hold):
    c.fill(BLACK)
    hue = (frame * 2) % 360
    col = pygame.Color(0)
    col.hsva = (hue, 72, 100, 100)
    text(c, "T-ARCADE", W // 2, 26, col, 40, center=True)
    pygame.draw.line(c, DIM, (24, 44), (W - 24, 44))

    if not items:
        text(c, "NO GAMES INSTALLED", W // 2, 110, GREY, 20, center=True)
        return

    top = max(0, min(sel - 2, len(items) - 6))
    for i, row in enumerate(items[top:top + 6]):
        idx = top + i
        y = 60 + i * 22
        name, accent = row[0], row[3]
        if idx == sel:
            pygame.draw.rect(c, (28, 28, 44), (18, y - 3, W - 36, 20))
            pygame.draw.rect(c, accent, (18, y - 3, 3, 20))
            text(c, name, 32, y, accent, 20)
        else:
            text(c, name, 32, y, GREY, 18)

    if len(items) > 6:
        text(c, f"{sel + 1}/{len(items)}", W - 26, 200, DIM, 14, center=True)

    if confirm_off:
        pygame.draw.rect(c, BLACK, (28, 88, W - 56, 64))
        pygame.draw.rect(c, RED, (28, 88, W - 56, 64), 1)
        text(c, "SHUT DOWN?", W // 2, 106, RED, 24, center=True)
        text(c, "A = YES    B = CANCEL", W // 2, 132, WHITE, 16, center=True)
    elif launching:
        pygame.draw.rect(c, BLACK, (40, 100, W - 80, 40))
        pygame.draw.rect(c, AMBER, (40, 100, W - 80, 40), 1)
        text(c, "LOADING...", W // 2, 120, AMBER, 24, center=True)
    else:
        pygame.draw.line(c, DIM, (24, 206), (W - 24, 206))
        if (frame // 14) % 2:
            text(c, "A = PLAY", W // 2, 218, WHITE, 18, center=True)
        else:
            text(c, "HOLD START = POWER OFF", W // 2, 218, GREY, 15,
                 center=True)

    if off_hold > 0:
        frac = min(1.0, off_hold / (FPS * 3))
        pygame.draw.rect(c, RED, (0, H - 4, int(W * frac), 4))


# ---------------------------------------------------------------------------

def draw_bt(c, bt, sel, frame):
    """Bluetooth speaker picker."""
    c.fill(BLACK)
    text(c, "AUDIO DEVICE", W // 2, 20, PINK, 28, center=True)
    pygame.draw.line(c, DIM, (24, 36), (W - 24, 36))

    devs = bt.devices
    if bt.busy:
        dots = "." * (1 + (frame // 8) % 3)
        text(c, bt.status.rstrip(".") + dots, W // 2, 110, AMBER, 18,
             center=True)
    elif not devs:
        text(c, "NO DEVICES FOUND", W // 2, 100, GREY, 18, center=True)
        text(c, "Put the speaker in pairing mode", W // 2, 122, DIM, 13,
             center=True)
        text(c, "then press B to scan", W // 2, 138, DIM, 13, center=True)
    else:
        top = max(0, min(sel - 2, len(devs) - 6))
        for i, (mac, name, conn) in enumerate(devs[top:top + 6]):
            idx = top + i
            y = 48 + i * 20
            label = (name or mac)[:26]
            if idx == sel:
                pygame.draw.rect(c, (28, 28, 44), (14, y - 3, W - 28, 19))
                pygame.draw.rect(c, PINK, (14, y - 3, 3, 19))
                text(c, label, 26, y, GREEN if conn else PINK, 17)
            else:
                text(c, label, 26, y, GREEN if conn else GREY, 15)
            if conn:
                text(c, "*", W - 24, y, GREEN, 17)

    if bt.status and not bt.busy:
        text(c, bt.status[:38], W // 2, 186, WHITE, 13, center=True)

    pygame.draw.line(c, DIM, (24, 198), (W - 24, 198))
    if bt.busy:
        text(c, "SELECT = BACK", W // 2, 212, CYAN, 16, center=True)
    elif (frame // 14) % 2:
        text(c, "A = CONNECT   B = SCAN", W // 2, 212, WHITE, 15,
             center=True)
    else:
        text(c, "SELECT = BACK TO ARCADE", W // 2, 212, CYAN, 15,
             center=True)


def wlr_env():
    """Environment wlr-randr needs, rebuilt rather than inherited.

    Plain `wlr-randr --output X --on` succeeds from a normal shell but
    can fail under sudo, because WAYLAND_DISPLAY / XDG_RUNTIME_DIR may
    not survive the privilege change. Reconstruct them from the desktop
    user's runtime directory.
    """
    env = dict(os.environ)
    user = os.environ.get("SUDO_USER") or USER
    try:
        uid = pwd.getpwnam(user).pw_uid
    except Exception:
        uid = os.getuid()
    rt = f"/run/user/{uid}"
    env["XDG_RUNTIME_DIR"] = rt
    if not env.get("WAYLAND_DISPLAY"):
        try:
            for f in sorted(os.listdir(rt)):
                if f.startswith("wayland-") and not f.endswith(".lock"):
                    env["WAYLAND_DISPLAY"] = f
                    break
        except OSError:
            pass
    return env


def wlr(*args, timeout=10):
    """Run wlr-randr with a known-good environment."""
    try:
        return subprocess.run(["wlr-randr"] + list(args),
                              capture_output=True, text=True,
                              timeout=timeout, env=wlr_env())
    except Exception as err:
        return subprocess.CompletedProcess(args, 1, "", str(err))


def enable_output(name):
    """Turn an output back on, escalating through what wlr-randr accepts.

    A bare --on is often refused with "could not apply config" after the
    output has been disabled, so fall back to naming its preferred mode
    and then to placing it at the origin.
    """
    if not name:
        return False, "no output"

    attempts = [["--output", name, "--on"]]

    mode = None
    try:
        out = wlr(timeout=6).stdout
        seen = False
        for line in out.splitlines():
            if line and line[0].isalpha():
                seen = line.split()[0] == name
            elif seen and "preferred" in line:
                mode = line.strip().split()[0]
                break
    except Exception:
        pass
    if mode:
        attempts.append(["--output", name, "--on", "--mode", mode])
    attempts.append(["--output", name, "--on", "--pos", "0,0"])
    # Describing the whole layout in one call often succeeds where an
    # incremental change is rejected.
    attempts.append(["--output", name, "--on", "--pos", "0,0",
                     "--output", "SPI-1", "--pos", "1920,0"])

    last = ""
    for cmd in attempts:
        r = wlr(*cmd)
        if r.returncode == 0:
            return True, " ".join(cmd[2:]) or "--on"
        msg = (r.stderr or r.stdout or "").strip().splitlines()
        last = msg[-1] if msg else f"rc={r.returncode}"
    return False, last[:40]


def find_outputs():
    out = wlr(timeout=8).stdout
    if not out:
        return None, None
    big = panel = None
    for line in out.splitlines():
        if line and line[0].isalpha():
            name = line.split()[0]
            if "SPI" in name.upper():
                panel = name
            elif big is None:
                big = name
    return big, panel


def safe_shutdown():
    print("\nShutting down cleanly...")
    subprocess.call(["sync"])
    subprocess.call(["systemctl", "poweroff"])


def main():
    ap = argparse.ArgumentParser(description="T-ARCADE")
    ap.add_argument("--monitor", action="store_true",
                    help="run on the monitor instead of the panel")
    args = ap.parse_args()

    items = [g for g in GAMES if available(g)]
    items.append(("POWER OFF", POWER_OFF, None, AMBER, None, {}))
    items.append(("MONITOR ON", MONITOR_ON, None, CYAN, None, {}))
    if BTAudio.supported():
        items.append(("AUDIO DEVICE", AUDIO_DEV, None, PINK, None, {}))
    print(f"{len(items) - 1} game(s) found; running games as '{USER}'")

    big = None
    if not args.monitor:
        big, _panel = find_outputs()
        if big:
            wlr("--output", big, "--off")
            time.sleep(1.5)

    def restore():
        if big:
            ok, detail = enable_output(big)
            print(f"restore {big}: {'ok' if ok else 'FAILED'} ({detail})")

    def make_display():
        """(Re)create the launcher window.

        A game that takes over the display invalidates our SDL surface,
        so the display subsystem is rebuilt after every game -- otherwise
        the menu vanishes and only the desktop shows through.
        """
        flags = pygame.FULLSCREEN if not args.monitor else 0
        sc = pygame.display.set_mode((W, H), flags)
        pygame.display.set_caption("T-ARCADE")
        pygame.mouse.set_visible(False)
        return sc

    pygame.display.init()
    pygame.font.init()
    screen = make_display()
    canvas = pygame.Surface((W, H))
    clock = pygame.time.Clock()

    pad = Pad()
    if BTAudio.supported():
        _mac, _nm = BTAudio.last()
        if _mac:
            print(f"reconnecting audio: {_nm or _mac}")
            BTAudio().reconnect_last()

    sel = frame = 0
    prev = {}
    launching = 0
    off_hold = 0
    sel_hold = 0
    monitor_msg = 0
    monitor_ok = True
    confirm_off = False
    bt = BTAudio()
    bt_screen = False
    bt_sel = 0
    bt_waiting = False
    bt_done_msg = 0
    bt_ok = False
    poweroff = False
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

            if bt_screen:
              try:
                # A pair/connect failure must never take the launcher
                # down -- report it on screen and stay in the menu.
                if bt_done_msg > 0:
                    bt_done_msg -= 1
                    if bt_done_msg == 0:
                        bt_screen = False
                # Select works even while scanning, so the screen is
                # never a place you can get stuck.
                if hit("select"):
                    bt_screen = False
                elif not bt.busy:
                    if hit("up") and bt.devices:
                        bt_sel = (bt_sel - 1) % len(bt.devices)
                    if hit("down") and bt.devices:
                        bt_sel = (bt_sel + 1) % len(bt.devices)
                    if hit("a") and bt.devices:
                        mac, nm, conn = bt.devices[bt_sel]
                        if conn:
                            bt.disconnect(mac)
                        else:
                            bt.connect(mac, nm)
                        bt_waiting = True
                    if hit("b"):
                        bt.scan()
                    if hit("start"):
                        bt_screen = False
                if bt_waiting and not bt.busy:
                    bt_waiting = False
                    bt_ok = "connect" in bt.status.lower() and \
                            "fail" not in bt.status.lower()
                    bt_ok = bt_ok or bt.status.lower().startswith("connected")
                    bt_done_msg = FPS * 2

                draw_bt(canvas, bt, bt_sel, frame)
                if bt_done_msg > 0:
                    col = GREEN if bt_ok else RED
                    msg = "PAIR SUCCESSFUL" if bt_ok else "PAIR FAILED"
                    pygame.draw.rect(canvas, BLACK, (26, 96, W - 52, 44))
                    pygame.draw.rect(canvas, col, (26, 96, W - 52, 44), 1)
                    text(canvas, msg, W // 2, 112, col, 22, center=True)
                    text(canvas, bt.status[:34], W // 2, 132, WHITE, 12,
                         center=True)
                screen.blit(canvas, (0, 0))
                pygame.display.flip()
                prev = s
                frame += 1
                clock.tick(FPS)
                continue
              except Exception as err:
                print(f"bluetooth screen error: {err}")
                bt.status = str(err)[:36]
                bt_screen = False
                bt_waiting = False
                bt_done_msg = 0

            if confirm_off:
                if hit("a"):
                    poweroff = True
                    running = False
                if hit("b") or hit("select"):
                    confirm_off = False
            elif not launching:
                if hit("up"):
                    sel = (sel - 1) % len(items)
                if hit("down"):
                    sel = (sel + 1) % len(items)
                if hit("a"):
                    if items[sel][1] == POWER_OFF:
                        confirm_off = True
                    elif items[sel][1] == AUDIO_DEV:
                        bt_screen = True
                        bt_sel = 0
                        bt.refresh()
                        if not bt.devices:
                            bt.scan()
                    elif items[sel][1] == MONITOR_ON:
                        # Re-enable HDMI without quitting, so the desktop
                        # comes back while the arcade keeps running.
                        target = big
                        if not target:
                            target, _p = find_outputs()
                        ok, detail = enable_output(target)
                        print(f"MONITOR ON {target}: "
                              f"{'ok' if ok else 'FAILED'} ({detail})")
                        monitor_ok = ok
                        monitor_msg = FPS * 3
                    else:
                        launching = 1

                # hold Start 3s -> shutdown prompt
                if s["start"]:
                    off_hold += 1
                    if off_hold > FPS * 3:
                        confirm_off = True
                        off_hold = 0
                else:
                    off_hold = 0

                # hold Select 2s -> exit to desktop
                if s["select"] and not s["start"]:
                    sel_hold += 1
                    if sel_hold > FPS * 2:
                        running = False
                else:
                    sel_hold = 0

            draw_menu(canvas, items, sel, frame, launching,
                      confirm_off, off_hold)
            if monitor_msg > 0:
                monitor_msg -= 1
                col = CYAN if monitor_ok else RED
                msg = "MONITOR ON" if monitor_ok else "MONITOR FAILED"
                pygame.draw.rect(canvas, BLACK, (34, 104, W - 68, 32))
                pygame.draw.rect(canvas, col, (34, 104, W - 68, 32), 1)
                text(canvas, msg, W // 2, 120, col, 22, center=True)
            screen.blit(canvas, (0, 0))
            pygame.display.flip()

            if launching:
                launching += 1
                if launching > 8:
                    launching = 0
                    run_game(items[sel], pad)
                    try:
                        pygame.display.quit()
                        time.sleep(0.4)
                        pygame.display.init()
                        pygame.font.init()
                        _fonts.clear()
                        screen = make_display()
                    except Exception as err:
                        print(f"display rebuild failed: {err}")
                    prev = {}
                    pygame.event.clear()
                    frame += 1
                    continue

            prev = s
            frame += 1
            clock.tick(FPS)
    finally:
        pad.close()
        pygame.quit()
        restore()
        print("T-ARCADE closed")
        if poweroff:
            safe_shutdown()


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Run with sudo -E (needs GPIO).", file=sys.stderr)
    main()
