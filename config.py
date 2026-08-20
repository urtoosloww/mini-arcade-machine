"""
config.py -- every value that depends on this particular cabinet.

Nothing here is imported for its own sake; each entry is something that
was previously hardcoded in a source file and had to be edited in two
places when the hardware changed. If you are porting this to different
wiring, a different panel, or a different distribution, this file should
be the only one you touch.

Grouped by what would make you change it:

    PATHS       different distro, or games installed somewhere else
    I2C / ADC   different ADC, different joystick, different rail
    GPIO        different wiring
    DISPLAY     different panel
    TIMING      feel, or a slower/faster machine
    TERMINAL    different font available, or a bigger screen
    THEME       taste
"""

import os

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# Debian puts games in /usr/games, which is not on root's PATH -- hence the
# absolute paths throughout. On a distribution that uses /usr/bin, change
# GAMES_DIR and the entries in tarcade.GAMES follow.
GAMES_DIR = "/usr/games"

# Doom needs an IWAD. freedoom1 is the freely redistributable one Debian
# ships; point this at doom.wad or doom2.wad if you own them.
DOOM_WAD = "/usr/share/games/doom/freedoom1.wad"

XTERM = "/usr/bin/xterm"
PYTHON = "/usr/bin/python3"

# The launcher re-executes itself and the input bridge by absolute path,
# because sudoers rules match on the literal command line.
REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(REPO_ROOT, "src")

# Logs. /tmp is deliberate: these are diagnostic, not worth surviving a
# reboot, and writing them to the SD card on every frame is not free.
BRIDGE_LOG = "/tmp/bridge.log"
BOOT_LOG = "/tmp/tarcade_boot.log"

# Remembered Bluetooth speaker. Written by the launcher (running as root)
# but read by the desktop user, so it is chmod 666 on write.
BT_STATE_FILE = os.path.expanduser("~/.tarcade_btaudio.json")

# ---------------------------------------------------------------------------
# I2C / ADC  (ADS1115)
# ---------------------------------------------------------------------------
I2C_BUS = 1               # Pi 5 exposes the header I2C as bus 1
ADS_ADDR = 0x48           # ADDR pin tied to GND. GND/VDD/SDA/SCL = 48/49/4A/4B

# ADS1115 config register: single-shot, +/-4.096V PGA, 860 SPS, comparator
# disabled. The MUX bits for each single-ended channel are OR'd in per read.
ADS_MUX = {0: 0x4000, 1: 0x5000, 2: 0x6000, 3: 0x7000}
ADS_CONFIG_BASE = 0x8000 | 0x0200 | 0x0100 | 0x00E0 | 0x0003

# Which ADC channel each joystick axis is wired to.
ADC_CH_H = 1              # VRx -> A1
ADC_CH_V = 3              # VRy -> A3

# Time for one single-shot conversion at 860 SPS (~1.16ms) plus margin.
ADC_CONVERSION_DELAY = 0.0015

# Counts from centre to full deflection. Measured, not calculated: the
# stick's usable travel is well short of rail-to-rail. Re-measure with
# `python3 src/arcade_input.py --test` if you swap the stick.
#
# On a 3.3V rail with the +/-4.096V PGA, centre should read about 13200.
# If it reads ~20400 the stick is on 5V and is over-driving the ADC's
# absolute maximum input of VDD+0.3V. See docs/DEBUGGING.md, Bug 3.
ADC_SPAN = 9400
ADC_CENTER_EXPECTED_3V3 = 13200   # sanity reference, not used for logic

# Fraction of ADC_SPAN a direction must exceed to register. High because
# this is a menu, not an analogue control -- diagonal slop is worse than
# a slightly stiff-feeling stick.
DEADZONE = 0.50

# Samples averaged at startup to find the resting position, so drift does
# not read as permanent input.
CALIBRATION_SAMPLES = 30
CALIBRATION_SETTLE = 0.3

# Wiring gives reversed axes on this cabinet.
INVERT_H = True
INVERT_V = True

# The stick is physically mounted rotated 90 degrees. Corrected in software
# rather than by remounting, because the bracket is glued.
ROTATE_90 = True

# ---------------------------------------------------------------------------
# GPIO
# ---------------------------------------------------------------------------
# Tactile buttons, active-low against the internal pull-ups.
BTN_PINS = {"a": 5, "b": 6, "start": 20, "select": 21}

# Debounce. The launcher can afford to be slower than the in-game bridge;
# a missed menu press is annoying, a missed jump loses a life.
BUTTON_BOUNCE_MENU = 0.02
BUTTON_BOUNCE_GAME = 0.01

# Panel control pins, for reference -- these are set in the device tree by
# scripts/setup_panel.sh, not driven from Python.
PANEL_RESET_GPIO = 25
PANEL_DC_GPIO = 24
PANEL_CS_GPIO = 8         # CE0. The mipi-dbi-spi overlay is bound to spi0-0.
#
# The backlight is NOT on a GPIO. It draws 60-80mA, and a Pi 5 GPIO is
# rated for about 16mA; sustained 4-5x overcurrent trips the RP1's
# protection and cuts power to the board. It is wired to the 3.3V rail
# instead. See docs/DEBUGGING.md, Bug 4.
PANEL_BACKLIGHT_GPIO = None

# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
PANEL_W = 320
PANEL_H = 240
FPS = 30                  # see LIMITATIONS: the SPI bus caps out near here

# 48MHz is the highest this panel runs reliably. 320*240*2 bytes = 153,600
# bytes per frame, about 26ms of bus time, so ~30fps is the hard ceiling.
SPI_SPEED_HZ = 48_000_000

# How the SPI panel's output is named by the compositor. Everything else
# in the layout is treated as "the monitor".
PANEL_OUTPUT_MATCH = "SPI"

# Where the panel is placed when the whole layout has to be described in
# one wlr-randr call (the last escalation step in enable_output).
PANEL_LAYOUT_POS = "1920,0"

# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------
INPUT_POLL_INTERVAL = 0.016      # ~60Hz, twice the display rate

GPIO_HANDOFF_DELAY = 1.0         # after the launcher releases the pins
BRIDGE_START_DELAY = 2.5         # bridge calibrates before the game starts
GPIO_RECLAIM_DELAY = 1.2         # after the bridge exits
DISPLAY_REBUILD_DELAY = 0.4      # between pygame.display.quit() and init()

COMBO_HOLD_SECS = 2.0            # Start+Select held -> force-quit the game
POWEROFF_HOLD_SECS = 3.0         # Start held in the menu -> shutdown prompt
EXIT_HOLD_SECS = 2.0             # Select held in the menu -> quit to desktop

KILL_TIMEOUT = 6.0               # SIGTERM grace before SIGKILL
KILL_SWEEP_TRIES = 12            # pgrep/pkill rounds before giving up
KILL_SWEEP_TERM_TRIES = 3        # of those, how many use TERM before KILL
KILL_SWEEP_INTERVAL = 0.35

MONITOR_OFF_SETTLE = 1.5         # after disabling HDMI, before drawing

# ---------------------------------------------------------------------------
# Terminal games
# ---------------------------------------------------------------------------
# Debian Trixie no longer ships the legacy bitmap fonts (4x6, 5x7...), so
# ncurses games get a scalable Xft font instead. See DEBUGGING.md, Bug 9.
TERM_FONT = "DejaVu Sans Mono"
TERM_MIN_COLS = 80
TERM_MIN_ROWS = 24

# DejaVu Sans Mono advances 0.602 em. At 96 dpi an Xft size of S points is
# S * (96/72) * 0.602 = S * 0.803 px wide, and about 1.70 * S px tall.
# Change these if you change TERM_FONT.
TERM_CELL_W_PER_PT = 0.803
TERM_CELL_H_PER_PT = 1.70
TERM_MIN_FONT_PT = 3

# ---------------------------------------------------------------------------
# Bluetooth
# ---------------------------------------------------------------------------
BT_SCAN_SECS = 10
BT_MAX_INFO_LOOKUPS = 14         # `bluetoothctl info` is slow; cap the sweep

# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------
BLACK = (10, 8, 16)
WHITE = (238, 238, 240)
GREY = (105, 105, 122)
DIM = (60, 60, 76)
RED = (232, 62, 52)
AMBER = (250, 176, 46)
CYAN = (68, 212, 236)
GREEN = (86, 216, 108)
PINK = (238, 104, 186)

# Menu rows visible at once at 320x240 with FONT_ROW-sized text.
MENU_VISIBLE_ROWS = 6
FONT_TITLE = 40
FONT_ROW = 20
FONT_ROW_DIM = 18
FONT_SMALL = 15
FONT_TINY = 13
