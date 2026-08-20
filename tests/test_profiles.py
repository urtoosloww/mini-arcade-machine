"""
Key profiles and the game table.

Four buttons have to mean different things per genre -- fire in Doom is
Ctrl, jump in a platformer is Space -- and the mapping is data, so it
can drift out of sync with the games that reference it. These tests are
cheap and catch a typo that would otherwise only show up as a button
that does nothing, mid-game, on the cabinet.
"""

import pytest

import arcade_input
import config
import tarcade

BUTTONS = set(config.BTN_PINS)


def test_every_profile_maps_every_button():
    for name, keymap in arcade_input.PROFILES.items():
        assert set(keymap) == BUTTONS, f"{name} is missing a button"


def test_every_game_names_a_profile_that_exists():
    for entry in tarcade.GAMES:
        profile = entry[2]
        assert profile in arcade_input.PROFILES, \
            f"{entry[0]} wants profile {profile!r}"


def test_direction_keys_cover_all_four_directions():
    assert set(arcade_input.DIR_KEYS) == {"up", "down", "left", "right"}


def test_game_entries_are_well_formed():
    for entry in tarcade.GAMES:
        assert len(entry) in (6, 7), f"{entry[0]} has {len(entry)} fields"
        name, cmd, profile, colour, test, env = entry[:6]
        assert isinstance(name, str) and name
        assert isinstance(colour, tuple) and len(colour) == 3
        assert isinstance(env, dict)
        assert test is None or callable(test)


def test_game_names_are_unique():
    names = [e[0] for e in tarcade.GAMES]
    assert len(names) == len(set(names))


def test_terminal_games_declare_a_kill_name():
    """xterm outlives its child unless the child is swept by name too."""
    for entry in tarcade.GAMES:
        if isinstance(entry[1], tuple) and entry[1][0] == "TERM":
            assert len(entry) == 7 and entry[6], \
                f"{entry[0]} runs in xterm but names nothing to kill"


def test_binaries_are_absolute_paths():
    """root's PATH does not include /usr/games, so relative names fail."""
    for entry in tarcade.GAMES:
        cmd = entry[1]
        binary = cmd[1] if isinstance(cmd, tuple) else cmd[0]
        assert binary.startswith("/"), f"{entry[0]}: {binary}"


def test_menu_entries_are_distinct_sentinels():
    sentinels = (tarcade.POWER_OFF, tarcade.MONITOR_ON, tarcade.AUDIO_DEV)
    assert len(set(sentinels)) == 3
    # Membership is by ==, not hash: an entry's argv is a list.
    for entry in tarcade.GAMES:
        assert entry[1] not in sentinels


@pytest.mark.parametrize("sentinel", ["POWER_OFF", "MONITOR_ON", "AUDIO_DEV"])
def test_menu_sentinels_are_always_available(sentinel):
    value = getattr(tarcade, sentinel)
    assert tarcade.available(("X", value, None, (0, 0, 0), None, {}))
