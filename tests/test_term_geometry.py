"""
Terminal font sizing.

Debian Trixie dropped the legacy bitmap fonts, so ncurses games run in
xterm with a scalable Xft font. Two things then go wrong on their own:
xterm's -fullscreen is only a request to the window manager and XWayland
ignores it, and a font chosen for a 320x240 panel is unreadable on a
1080p monitor.

So the grid is computed from the live screen size and passed as an
explicit -geometry. The invariant that matters is that the game's
minimum grid always fits, at every resolution the cabinet runs at.
"""

import pytest

import config
import tarcade

# 320x240 is the panel; 640x480 and 1920x1080 are what you get when the
# arcade is run on the monitor for debugging.
SCREENS = [
    pytest.param(320, 240, id="panel-320x240"),
    pytest.param(640, 480, id="vga-640x480"),
    pytest.param(1920, 1080, id="hd-1920x1080"),
]


@pytest.mark.parametrize("w,h", SCREENS)
def test_minimum_grid_always_fits(w, h):
    """80x24 is the floor; below it ncurses games corrupt their display."""
    size, cols, rows = tarcade.term_geometry(w, h)
    assert cols >= config.TERM_MIN_COLS
    assert rows >= config.TERM_MIN_ROWS


@pytest.mark.parametrize("w,h", SCREENS)
def test_grid_does_not_overflow_the_screen(w, h):
    """The whole point of computing this: the window must fit on screen."""
    size, cols, rows = tarcade.term_geometry(w, h)
    assert cols * size * config.TERM_CELL_W_PER_PT <= w
    assert rows * size * config.TERM_CELL_H_PER_PT <= h


@pytest.mark.parametrize("w,h", SCREENS)
def test_font_is_never_degenerate(w, h):
    size, _, _ = tarcade.term_geometry(w, h)
    assert size >= config.TERM_MIN_FONT_PT


def test_font_grows_with_the_screen():
    """A 4pt font is right on the panel and unreadable on a monitor."""
    panel = tarcade.term_geometry(320, 240)[0]
    vga = tarcade.term_geometry(640, 480)[0]
    hd = tarcade.term_geometry(1920, 1080)[0]
    assert panel < vga < hd


def test_a_game_that_needs_a_bigger_grid_gets_a_smaller_font():
    """ninvaders wants more rows than the 80x24 default; the font shrinks."""
    default_size, _, _ = tarcade.term_geometry(640, 480)
    big_size, _, rows = tarcade.term_geometry(640, 480,
                                              min_cols=100, min_rows=40)
    assert big_size < default_size
    assert rows >= 40


def test_absurdly_small_screen_clamps_instead_of_dividing_by_zero():
    """max(TERM_MIN_FONT_PT, ...) is load-bearing, not defensive noise."""
    size, cols, rows = tarcade.term_geometry(64, 48)
    assert size == config.TERM_MIN_FONT_PT
    assert cols >= config.TERM_MIN_COLS
    assert rows >= config.TERM_MIN_ROWS


def test_term_argv_carries_the_computed_geometry(monkeypatch):
    """xterm is told the grid explicitly, because it will not ask."""
    monkeypatch.setattr(tarcade, "active_screen_size", lambda: (640, 480))
    argv = tarcade.term("/usr/games/ninvaders")

    assert argv[0] == config.XTERM
    size, cols, rows = tarcade.term_geometry(640, 480)
    assert "-geometry" in argv
    assert argv[argv.index("-geometry") + 1] == f"{cols}x{rows}+0+0"
    assert argv[argv.index("-fs") + 1] == str(size)
    assert argv[argv.index("-fa") + 1] == config.TERM_FONT
    assert argv[-2:] == ["-e", "/usr/games/ninvaders"]


# --- wlr-randr parsing, which feeds the above -------------------------------

WLR_OUTPUT = """HDMI-A-1 "Dell Inc. DELL U2412M ABCD"
  Make: Dell Inc.
  Model: DELL U2412M
  Enabled: yes
  Modes:
    1920x1080 px, 60.000000 Hz (preferred, current)
    1280x1024 px, 60.000000 Hz
SPI-1 "Unknown Unknown Unknown"
  Enabled: yes
  Modes:
    320x240 px, 0.000000 Hz (preferred, current)
"""

WLR_HDMI_OFF = WLR_OUTPUT.replace("HDMI-A-1 \"Dell Inc. DELL U2412M ABCD\"\n"
                                  "  Make: Dell Inc.\n"
                                  "  Model: DELL U2412M\n"
                                  "  Enabled: yes",
                                  "HDMI-A-1 \"Dell Inc. DELL U2412M ABCD\"\n"
                                  "  Make: Dell Inc.\n"
                                  "  Model: DELL U2412M\n"
                                  "  Enabled: no")


def test_active_mode_is_the_enabled_output():
    assert tarcade.parse_active_mode(WLR_OUTPUT) == (1920, 1080)


def test_active_mode_skips_a_disabled_output():
    """After the launcher turns HDMI off, the panel is what remains."""
    assert tarcade.parse_active_mode(WLR_HDMI_OFF) == (320, 240)


def test_active_mode_of_nothing_is_none():
    assert tarcade.parse_active_mode("") is None
