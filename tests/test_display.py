"""
Output re-enable escalation.

Turning the HDMI output back on is the single most important thing the
launcher does, because failing it leaves the user with a dark monitor
and no way to see what happened. `wlr-randr --output X --on` is refused
often enough -- "could not apply config", with no detail -- that a bare
attempt is not good enough.

The escalation, in order:

    1. --on
    2. --on --mode <preferred>          some compositors need a mode
    3. --on --pos 0,0                   and some need a position
    4. the whole layout in one call     validated as a unit, so it
                                        succeeds where an incremental
                                        change is rejected

Each step is a real failure that was observed, not a guess. The runner
is injected so the chain can be tested without a compositor.
"""

import subprocess

import pytest

import config
import tarcade

WLR_LIST = """HDMI-A-1 "Dell Inc. DELL U2412M ABCD"
  Enabled: no
  Modes:
    1920x1080 px, 60.000000 Hz (preferred)
    1280x1024 px, 60.000000 Hz
SPI-1 "Unknown Unknown Unknown"
  Enabled: yes
  Modes:
    320x240 px, 0.000000 Hz (preferred, current)
"""

REFUSED = "could not apply configuration"


class FakeWlr:
    """Records attempts and fails the first `fail_first` of them.

    The no-argument call is enable_output probing for the preferred
    mode; it is answered with a listing and not counted as an attempt.
    """

    def __init__(self, fail_first=0, listing=WLR_LIST):
        self.fail_first = fail_first
        self.listing = listing
        self.attempts = []

    def __call__(self, *args, timeout=10):
        if not args:
            return subprocess.CompletedProcess([], 0, self.listing, "")
        self.attempts.append(list(args))
        if len(self.attempts) <= self.fail_first:
            return subprocess.CompletedProcess(list(args), 1, "", REFUSED)
        return subprocess.CompletedProcess(list(args), 0, "", "")


def test_plain_on_is_tried_first_and_nothing_else_runs():
    fake = FakeWlr(fail_first=0)
    ok, detail = tarcade.enable_output("HDMI-A-1", _run=fake)

    assert ok is True
    assert fake.attempts == [["--output", "HDMI-A-1", "--on"]]
    assert detail == "--on"


def test_falls_back_to_the_preferred_mode():
    fake = FakeWlr(fail_first=1)
    ok, detail = tarcade.enable_output("HDMI-A-1", _run=fake)

    assert ok is True
    assert fake.attempts[1] == ["--output", "HDMI-A-1", "--on",
                                "--mode", "1920x1080"]
    assert "1920x1080" in detail


def test_falls_back_to_positioning_at_the_origin():
    fake = FakeWlr(fail_first=2)
    ok, _ = tarcade.enable_output("HDMI-A-1", _run=fake)

    assert ok is True
    assert fake.attempts[2] == ["--output", "HDMI-A-1", "--on",
                                "--pos", "0,0"]


def test_last_resort_describes_the_whole_layout():
    fake = FakeWlr(fail_first=3)
    ok, _ = tarcade.enable_output("HDMI-A-1", _run=fake)

    assert ok is True
    assert fake.attempts[3] == ["--output", "HDMI-A-1", "--on",
                                "--pos", "0,0",
                                "--output", "SPI-1",
                                "--pos", config.PANEL_LAYOUT_POS]


def test_every_step_is_tried_before_giving_up():
    fake = FakeWlr(fail_first=99)
    ok, detail = tarcade.enable_output("HDMI-A-1", _run=fake)

    assert ok is False
    assert len(fake.attempts) == 4
    assert REFUSED[:20] in detail


def test_failure_reports_what_wlr_randr_said():
    """The message ends up on the panel, so it has to be the real one."""
    fake = FakeWlr(fail_first=99)
    _, detail = tarcade.enable_output("HDMI-A-1", _run=fake)
    assert detail == REFUSED[:40]


def test_no_mode_advertised_skips_that_step():
    """An output with no preferred mode has one fewer thing to try."""
    listing = "HDMI-A-1 \"x\"\n  Enabled: no\n  Modes:\n    1920x1080 px\n"
    fake = FakeWlr(fail_first=99, listing=listing)
    ok, _ = tarcade.enable_output("HDMI-A-1", _run=fake)

    assert ok is False
    assert len(fake.attempts) == 3
    assert not any("--mode" in a for a in fake.attempts)


def test_no_output_name_fails_immediately():
    """find_outputs returns None when the compositor is unreachable."""
    fake = FakeWlr()
    ok, detail = tarcade.enable_output(None, _run=fake)

    assert (ok, detail) == (False, "no output")
    assert fake.attempts == []


def test_a_probe_that_raises_does_not_stop_the_escalation():
    """If listing the outputs fails, the other steps still get tried."""
    class Exploding(FakeWlr):
        def __call__(self, *args, timeout=10):
            if not args:
                raise RuntimeError("compositor went away")
            return super().__call__(*args, timeout=timeout)

    fake = Exploding(fail_first=99)
    ok, _ = tarcade.enable_output("HDMI-A-1", _run=fake)
    assert ok is False
    assert len(fake.attempts) == 3


# --- output discovery -------------------------------------------------------

def test_find_outputs_separates_the_panel_from_the_monitor():
    fake = FakeWlr()
    big, panel = tarcade.find_outputs(_run=fake)
    assert (big, panel) == ("HDMI-A-1", "SPI-1")


def test_find_outputs_with_only_the_panel():
    listing = "SPI-1 \"Unknown\"\n  Enabled: yes\n"
    big, panel = tarcade.find_outputs(_run=FakeWlr(listing=listing))
    assert (big, panel) == (None, "SPI-1")


def test_find_outputs_when_the_compositor_is_unreachable():
    big, panel = tarcade.find_outputs(_run=FakeWlr(listing=""))
    assert (big, panel) == (None, None)
