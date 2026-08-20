#!/usr/bin/env python3
"""
btaudio.py -- Bluetooth speaker management for T-ARCADE.

Wraps bluetoothctl so the launcher can scan, pair, and connect without
dropping to a terminal. Long operations run on a worker thread so the
menu keeps redrawing.

Once a device is paired AND trusted, bluez reconnects it automatically
on boot, so this is normally a one-time setup per speaker.

    python3 btaudio.py --scan       # list what's nearby
    python3 btaudio.py --connect MAC
    python3 btaudio.py --status
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time

LAST_DEVICE = os.path.expanduser("~/.tarcade_btaudio.json")
SCAN_SECS = 10


def _run(args, timeout=20):
    try:
        return subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout).stdout
    except Exception:
        return ""


def _bt(*cmd, timeout=20):
    return _run(["bluetoothctl"] + list(cmd), timeout=timeout)


def _looks_like_mac(text):
    """True if a 'name' is really just the address repeated back."""
    t = text.strip().replace("-", ":").upper()
    return bool(re.fullmatch(r"(?:[0-9A-F]{2}:){5}[0-9A-F]{2}", t))


class BTAudio:
    """Threaded Bluetooth control. Poll .status / .devices from the UI."""

    def __init__(self):
        self.devices = []          # [(mac, name, connected)]
        self.status = ""
        self.busy = False
        self._thread = None
        self._names = {}           # mac -> name learned while scanning

    # -- availability ------------------------------------------------------
    @staticmethod
    def supported():
        return shutil.which("bluetoothctl") is not None

    @staticmethod
    def powered():
        out = _bt("show", timeout=8)
        return "Powered: yes" in out

    @staticmethod
    def power_on():
        _bt("power", "on", timeout=8)

    # -- background work ---------------------------------------------------
    def _start(self, fn):
        if self.busy:
            return False
        self.busy = True
        self._thread = threading.Thread(target=self._wrap, args=(fn,),
                                        daemon=True)
        self._thread.start()
        return True

    def _wrap(self, fn):
        try:
            fn()
        except Exception as err:
            self.status = f"error: {err}"
        finally:
            self.busy = False

    # -- operations --------------------------------------------------------
    def scan(self):
        return self._start(self._scan)

    def _scan(self):
        if not self.powered():
            self.status = "turning bluetooth on..."
            self.power_on()
            time.sleep(1.5)
        self.status = "scanning..."
        # `bluetoothctl devices` often reports a MAC-shaped placeholder
        # because names arrive asynchronously during discovery. The scan
        # stream itself carries them, so harvest names from there.
        out = _bt("--timeout", str(SCAN_SECS), "scan", "on",
                  timeout=SCAN_SECS + 10)
        for line in out.splitlines():
            mm = re.search(r"Device ([0-9A-F:]{17})\s+(.+)", line, re.I)
            if not mm:
                continue
            mac, nm = mm.group(1).upper(), mm.group(2).strip()
            if nm.lower().startswith(("name:", "alias:")):
                nm = nm.split(":", 1)[1].strip()
            elif ":" in nm and not _looks_like_mac(nm):
                # e.g. "RSSI: -60" -- not a name
                if nm.split(":", 1)[0] in ("RSSI", "TxPower", "ServiceData",
                                           "ManufacturerData", "UUIDs",
                                           "Connected", "Paired", "Trusted",
                                           "Icon", "Class", "Appearance"):
                    continue
            if nm and not _looks_like_mac(nm):
                self._names[mac] = nm
        self.refresh()
        named = sum(1 for d in self.devices if d[1] != "(unnamed)")
        self.status = f"{len(self.devices)} found, {named} named"

    def refresh(self, deep=True, max_lookups=14):
        """Rebuild the device list.

        Names come from three places, in order of reliability: what the
        scan stream reported, what `bluetoothctl devices` says, and
        finally a per-device `info` lookup for anything still unnamed.
        Audio devices sort to the top so speakers are easy to find among
        the phones and watches that also answer a scan.
        """
        out = _bt("devices", timeout=10)
        connected = _bt("devices", "Connected", timeout=10)
        conn_macs = set(m.upper() for m in
                        re.findall(r"Device ([0-9A-F:]{17})", connected,
                                   re.I))
        found = []
        unnamed = []
        for line in out.splitlines():
            m = re.match(r"Device ([0-9A-F:]{17})\s*(.*)", line.strip(),
                         re.I)
            if not m:
                continue
            mac = m.group(1).upper()
            name = (m.group(2) or "").strip()
            if _looks_like_mac(name) or not name:
                name = self._names.get(mac, "")
            if not name:
                unnamed.append(mac)
            found.append([mac, name, mac in conn_macs, False])

        # Ask bluez directly for the ones we still cannot name, and learn
        # which entries are audio devices while we are in there.
        if deep:
            lookups = 0
            for row in found:
                if lookups >= max_lookups:
                    break
                need_name = not row[1]
                info = _bt("info", row[0], timeout=6)
                if not info:
                    continue
                lookups += 1
                if need_name:
                    am = re.search(r"^\s*Alias:\s*(.+)$", info, re.M)
                    nm = re.search(r"^\s*Name:\s*(.+)$", info, re.M)
                    cand = (nm or am)
                    if cand:
                        v = cand.group(1).strip()
                        if not _looks_like_mac(v):
                            row[1] = v
                            self._names[row[0]] = v
                if re.search(r"Icon:\s*audio", info) or \
                        re.search(r"Class:\s*0x[0-9a-f]*(04|24)[0-9a-f]{2}",
                                  info, re.I):
                    row[3] = True

        for row in found:
            if not row[1]:
                row[1] = "(unnamed)"

        # connected first, then audio devices, then named, then the rest
        found.sort(key=lambda d: (not d[2], not d[3],
                                  d[1] == "(unnamed)", d[1].lower()))
        self.devices = [(r[0], r[1], r[2]) for r in found]
        return self.devices

    def connect(self, mac, name=""):
        return self._start(lambda: self._connect(mac, name))

    def _connect(self, mac, name=""):
        try:
            self.__connect(mac, name)
        except Exception as err:
            self.status = f"failed: {str(err)[:24]}"

    def __connect(self, mac, name=""):
        label = name or mac
        self.status = f"pairing {label[:14]}..."
        _bt("pair", mac, timeout=30)
        # Trust makes bluez reconnect it automatically on future boots.
        _bt("trust", mac, timeout=10)
        self.status = f"connecting {label[:14]}..."
        out = _bt("connect", mac, timeout=30)
        ok = "successful" in out.lower() or self.is_connected(mac)
        if ok:
            self.status = f"connected: {label[:16]}"
            self.remember(mac, name)
            time.sleep(1.5)
            self.route_audio(mac)
        else:
            self.status = "connect failed"
        self.refresh()

    def disconnect(self, mac):
        return self._start(lambda: self._disconnect(mac))

    def _disconnect(self, mac):
        self.status = "disconnecting..."
        _bt("disconnect", mac, timeout=15)
        self.refresh()
        self.status = "disconnected"

    @staticmethod
    def is_connected(mac):
        return "Connected: yes" in _bt("info", mac, timeout=10)

    # -- audio routing -----------------------------------------------------
    @staticmethod
    def route_audio(mac):
        try:
            return BTAudio._route_audio(mac)
        except Exception as err:
            print(f"audio routing skipped: {err}")
            return False

    @staticmethod
    def _route_audio(mac):
        """Make the Bluetooth sink the default output.

        PipeWire runs as the desktop user, so when the launcher is root we
        have to reach into that user's session to set the default sink.
        """
        user = os.environ.get("SUDO_USER")
        node = "bluez_output." + mac.replace(":", "_")
        base = ["wpctl"]
        if user and os.geteuid() == 0:
            uid = subprocess.run(["id", "-u", user], capture_output=True,
                                 text=True).stdout.strip()
            base = ["sudo", "-u", user, "env",
                    f"XDG_RUNTIME_DIR=/run/user/{uid}", "wpctl"]
        out = _run(base + ["status"], timeout=10)
        for line in out.splitlines():
            if node in line or ("bluez" in line and mac[-5:] in line):
                m = re.search(r"(\d+)\.", line)
                if m:
                    _run(base + ["set-default", m.group(1)], timeout=10)
                    return True
        # Fall back to any bluez sink
        for line in out.splitlines():
            if "bluez" in line.lower():
                m = re.search(r"(\d+)\.", line)
                if m:
                    _run(base + ["set-default", m.group(1)], timeout=10)
                    return True
        return False

    # -- persistence -------------------------------------------------------
    @staticmethod
    def remember(mac, name=""):
        try:
            with open(LAST_DEVICE, "w") as f:
                json.dump({"mac": mac, "name": name}, f)
            os.chmod(LAST_DEVICE, 0o666)
        except OSError:
            pass

    @staticmethod
    def last():
        try:
            with open(LAST_DEVICE) as f:
                d = json.load(f)
            return d.get("mac"), d.get("name", "")
        except (OSError, ValueError):
            return None, ""

    def reconnect_last(self):
        mac, name = self.last()
        if not mac:
            return False
        if self.is_connected(mac):
            self.status = f"connected: {name[:16] or mac}"
            self.route_audio(mac)
            return True
        return self._start(lambda: self._connect(mac, name))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--connect", metavar="MAC")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()

    bt = BTAudio()
    if not bt.supported():
        raise SystemExit("bluetoothctl not found: sudo apt install bluez")

    if a.scan:
        bt.scan()
        while bt.busy:
            print("\r" + bt.status.ljust(40), end="", flush=True)
            time.sleep(0.4)
        print()
        for mac, name, conn in bt.devices:
            print(f"  {'*' if conn else ' '} {mac}  {name}")
    elif a.connect:
        bt.connect(a.connect)
        while bt.busy:
            print("\r" + bt.status.ljust(40), end="", flush=True)
            time.sleep(0.4)
        print("\n" + bt.status)
    else:
        bt.refresh()
        for mac, name, conn in bt.devices:
            print(f"  {'CONNECTED' if conn else '         '} {mac}  {name}")
        mac, name = bt.last()
        if mac:
            print(f"\nremembered: {name or mac}")
