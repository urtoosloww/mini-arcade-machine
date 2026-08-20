#!/bin/bash
# start_arcade.sh -- launch T-ARCADE, intended for autostart at login.
#
# Runs inside the desktop session (not a system service) because the
# launcher needs WAYLAND_DISPLAY and XDG_RUNTIME_DIR to drive wlr-randr,
# and a system unit starts before any compositor exists.
#
#   bash scripts/start_arcade.sh          normal launch
#   bash scripts/start_arcade.sh --setup  install autostart + sudoers rule
#   bash scripts/start_arcade.sh --remove undo the autostart
#
# Escape hatch: create a DISABLE file in the repo root and the arcade
# will not start at boot. Handy if it ever comes up broken and you need
# the desktop back.

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/src"
USERNAME="$(id -un)"
PYTHON=/usr/bin/python3
LOG=/tmp/tarcade_boot.log
COMPOSITOR_WAIT=30      # seconds to wait for the compositor at boot
PANEL_SETTLE=3          # seconds after that, for the panel to come up

# ---------------------------------------------------------------- setup ---
if [ "${1:-}" = "--setup" ]; then
    echo "==> Passwordless sudo for the arcade scripts"
    # Scoped to these three command lines, not blanket sudo access.
    SUDOERS=/etc/sudoers.d/tarcade
    # SETENV is required. Without it `sudo -E` is refused with
    # "sorry, you are not allowed to preserve the environment", and the
    # launcher loses WAYLAND_DISPLAY/XDG_RUNTIME_DIR -- which means
    # wlr-randr cannot reach the compositor and the HDMI output never
    # comes back. See docs/DEBUGGING.md, Bug 8.
    sudo tee "$SUDOERS" > /dev/null <<SUDO
$USERNAME ALL=(ALL) NOPASSWD:SETENV: $PYTHON $SRC/tarcade.py
$USERNAME ALL=(ALL) NOPASSWD:SETENV: $PYTHON $SRC/tarcade.py *
$USERNAME ALL=(ALL) NOPASSWD:SETENV: $PYTHON $SRC/arcade_input.py *
SUDO
    sudo chmod 440 "$SUDOERS"
    sudo visudo -c -f "$SUDOERS" || { echo "sudoers syntax error"; exit 1; }

    echo "==> Autostart entry"
    mkdir -p ~/.config/autostart
    cat > ~/.config/autostart/tarcade.desktop <<DESK
[Desktop Entry]
Type=Application
Name=T-ARCADE
Comment=Arcade cabinet front-end
Exec=$REPO/scripts/start_arcade.sh
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=8
DESK
    chmod +x "$REPO/scripts/start_arcade.sh"
    echo
    echo "Done. The arcade will start on the next login."
    echo "  disable once : touch $REPO/DISABLE"
    echo "  disable fully: bash $0 --remove"
    echo "  boot log     : $LOG"
    exit 0
fi

if [ "${1:-}" = "--remove" ]; then
    rm -f ~/.config/autostart/tarcade.desktop
    sudo rm -f /etc/sudoers.d/tarcade
    echo "Autostart removed."
    exit 0
fi

# --------------------------------------------------------------- launch ---
exec >> "$LOG" 2>&1
echo "=== $(date) ==="

if [ -f "$REPO/DISABLE" ]; then
    echo "DISABLE file present -- not starting."
    exit 0
fi

# Wait for the compositor. wlr-randr fails before it is up, and the
# launcher's first act is to disable the HDMI output.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
for i in $(seq 1 $COMPOSITOR_WAIT); do
    if [ -z "${WAYLAND_DISPLAY:-}" ]; then
        for c in "$XDG_RUNTIME_DIR"/wayland-*; do
            case "$c" in *.lock) continue;; esac
            [ -S "$c" ] && export WAYLAND_DISPLAY="$(basename "$c")" && break
        done
    fi
    if [ -n "${WAYLAND_DISPLAY:-}" ] && wlr-randr > /dev/null 2>&1; then
        echo "compositor ready after ${i}s (WAYLAND_DISPLAY=$WAYLAND_DISPLAY)"
        break
    fi
    sleep 1
done

if ! wlr-randr > /dev/null 2>&1; then
    echo "compositor never became ready -- giving up."
    exit 1
fi

sleep $PANEL_SETTLE

cd "$REPO" || exit 1
echo "launching T-ARCADE"
exec sudo -E \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
    "$PYTHON" "$SRC/tarcade.py"
