"""
Bluetooth name handling.

`bluetoothctl devices` frequently reports a device's name as its own MAC
address, because names arrive asynchronously during discovery and bluez
fills the gap with the address. A menu full of "AA:BB:CC:DD:EE:FF" is
useless, so those are treated as no-name-yet and looked up again.

_looks_like_mac is the whole decision, so it gets the whole test.
"""

import pytest

from btaudio import BTAudio, _looks_like_mac

MACS = [
    "AA:BB:CC:DD:EE:FF",
    "aa:bb:cc:dd:ee:ff",       # bluez is inconsistent about case
    "00:00:00:00:00:00",
    "12:34:56:78:9A:BC",
    "AA-BB-CC-DD-EE-FF",       # dashed form, seen in some tools
    "  AA:BB:CC:DD:EE:FF  ",   # surrounding whitespace from line parsing
]

NAMES = [
    "JBL Flip 5",
    "Soundcore 2",
    "Pixel Buds",
    "(unnamed)",
    "",
    "Speaker: Living Room",     # a colon does not make it an address
    "AA:BB:CC:DD:EE",           # five octets -- malformed, not an address
    "AA:BB:CC:DD:EE:FF:00",     # seven octets
    "AA:BB:CC:DD:EE:GG",        # G is not hex
    "MyMAC AA:BB:CC:DD:EE:FF",  # an address embedded in a real name
]


@pytest.mark.parametrize("text", MACS)
def test_addresses_are_recognised(text):
    assert _looks_like_mac(text) is True


@pytest.mark.parametrize("text", NAMES)
def test_real_names_are_not_mistaken_for_addresses(text):
    assert _looks_like_mac(text) is False


def test_remember_and_read_back(tmp_path, monkeypatch):
    """The last speaker survives a reboot, so it reconnects unattended."""
    state = tmp_path / "btaudio.json"
    monkeypatch.setattr("btaudio.LAST_DEVICE", str(state))

    assert BTAudio.last() == (None, "")
    BTAudio.remember("AA:BB:CC:DD:EE:FF", "JBL Flip 5")
    assert BTAudio.last() == ("AA:BB:CC:DD:EE:FF", "JBL Flip 5")


def test_corrupt_state_file_is_not_fatal(tmp_path, monkeypatch):
    """A truncated write must not stop the arcade from booting."""
    state = tmp_path / "btaudio.json"
    state.write_text('{"mac": "AA:BB')
    monkeypatch.setattr("btaudio.LAST_DEVICE", str(state))
    assert BTAudio.last() == (None, "")
