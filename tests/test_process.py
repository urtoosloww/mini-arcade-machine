"""
Process teardown.

A game that survives into the menu is worse than a game that crashes:
its window sits over the launcher, it holds the display, and the only
way out is a power cycle. kill_tree therefore escalates -- SIGTERM the
process group so the game can save, SIGKILL the group if it ignores
that, then sweep by name for anything that escaped the group.

These tests spawn real processes that really ignore SIGTERM, because
that is the case the escalation exists for and a mock would only assert
that the code calls the functions it obviously calls.
"""

import os
import signal
import subprocess
import sys
import time

import pytest

import tarcade

# Unique enough that the name sweep cannot match anything else on the
# machine running the tests.
MARKER = "tarcade_killtree_fixture_9f3a2c"

# A parent that ignores SIGTERM and forks a child that also ignores it.
# Both spin until killed. The child is the interesting one: killing only
# the parent leaves it holding the display.
IGNORES_SIGTERM = f"""
import os, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if os.fork() == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    while True:
        time.sleep(0.05)
while True:
    time.sleep(0.05)
# {MARKER}
"""


def running(pattern):
    return subprocess.call(["pgrep", "-f", pattern],
                           stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL) == 0


def pids(pattern):
    out = subprocess.run(["pgrep", "-f", pattern],
                         capture_output=True, text=True).stdout
    return [int(p) for p in out.split()]


def spawn(new_group=True):
    proc = subprocess.Popen([sys.executable, "-c", IGNORES_SIGTERM],
                            start_new_session=new_group)
    deadline = time.time() + 5
    while time.time() < deadline and len(pids(MARKER)) < 2:
        time.sleep(0.05)
    return proc


@pytest.fixture
def stubborn():
    """A SIGTERM-ignoring parent plus its child. Cleaned up either way."""
    proc = spawn()
    yield proc
    for pid in pids(MARKER):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


def test_spawn_fixture_really_ignores_sigterm(stubborn):
    """Guard the guard: if SIGTERM killed it, the test below proves nothing."""
    assert len(pids(MARKER)) == 2
    os.killpg(os.getpgid(stubborn.pid), signal.SIGTERM)
    time.sleep(0.5)
    assert stubborn.poll() is None
    assert len(pids(MARKER)) == 2


def test_kill_tree_escalates_to_sigkill(stubborn):
    """SIGTERM is ignored, so the group must be SIGKILLed."""
    tarcade.kill_tree(stubborn, [MARKER], timeout=1.0)
    assert stubborn.poll() is not None
    assert not running(MARKER)


def test_kill_tree_takes_the_child_too(stubborn):
    """The child is a separate pid; killing by group is what catches it."""
    before = pids(MARKER)
    assert len(before) == 2

    tarcade.kill_tree(stubborn, [MARKER], timeout=1.0)

    for pid in before:
        with pytest.raises(OSError):
            os.kill(pid, 0)


def test_name_sweep_catches_what_the_group_missed():
    """A process outside the group is only reachable by name.

    Several of these games re-exec or fork a helper into a new session,
    which the process-group kill cannot reach. Passing proc=None here
    exercises the sweep on its own.
    """
    orphan = spawn()
    try:
        tarcade.kill_tree(None, [MARKER])
        assert not running(MARKER)
    finally:
        for pid in pids(MARKER):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        orphan.wait(timeout=5)


def test_kill_tree_tolerates_an_already_dead_process():
    """Called in a finally block, so it runs after crashes too."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    tarcade.kill_tree(proc, ["a_name_that_matches_nothing_5b1e"])


def test_kill_tree_accepts_no_process_and_no_names():
    tarcade.kill_tree(None, [])
