#!/bin/bash
# start_arcade.sh -- launch T-ARCADE, intended for autostart at login.
#
# Runs inside the desktop session (not a system service) because the
# launcher needs WAYLAND_DISPLAY and XDG_RUNTIME_DIR to drive wlr-randr.
#
#   bash start_arcade.sh          normal launch
#   bash start_arcade.sh --setup  install autostart + passwordless sudo
#   bash start_arcade.sh --remove undo the autostart
#
# Escape hatch: create ~/arcade/DISABLE and the arcade will not start at
# boot. Handy if it ever comes up broken and you need the desktop back.

DIR="$(cd "$(dirname "$0")" && pwd)"
USERNAME="$(id -un)"
LOG=/tmp/tarcade_boot.log

# ---------------------------------------------------------------- setup ---
if [ "${1:-}" = "--setup" ]; then
    echo "==> Passwordless sudo for the arcade scripts"
    # Only these two commands, not blanket sudo access.
    SUDOERS=/etc/sudoers.d/tarcade
    # SETENV is required, otherwise `sudo -E` is refused with
    # "sorry, you are not allowed to preserve the environment" -- and the
    # launcher needs WAYLAND_DISPLAY/XDG_RUNTIME_DIR to drive wlr-randr.
    sudo tee "$SUDOERS" > /dev/null <<EOF
$USERNAME ALL=(ALL) NOPASSWD:SETENV: /usr/bin/python3 $DIR/tarcade.py
$USERNAME ALL=(ALL) NOPASSWD:SETENV: /usr/bin/python3 $DIR/tarcade.py *
$USERNAME ALL=(ALL) NOPASSWD:SETENV: /usr/bin/python3 $DIR/arcade_input.py *
EOF
    sudo chmod 440 "$SUDOERS"
    sudo visudo -c -f "$SUDOERS" || { echo "sudoers syntax error"; exit 1; }

    echo "==> Autostart entry"
    mkdir -p ~/.config/autostart
    cat > ~/.config/autostart/tarcade.desktop <<EOF
[Desktop Entry]
Type=Application
Name=T-ARCADE
Comment=Arcade cabinet front-end
Exec=$DIR/start_arcade.sh
Terminal=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=8
EOF
    chmod +x "$DIR/start_arcade.sh"
    echo
    echo "Done. The arcade will start on the next login."
    echo "  disable once : touch $DIR/DISABLE"
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

if [ -f "$DIR/DISABLE" ]; then
    echo "DISABLE file present -- not starting."
    exit 0
fi

# Wait for the compositor to be ready; wlr-randr fails before that.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
for i in $(seq 1 30); do
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

sleep 3          # let the panel finish coming up

cd "$DIR" || exit 1
echo "launching T-ARCADE"
exec sudo -E \
    XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR" \
    WAYLAND_DISPLAY="$WAYLAND_DISPLAY" \
    /usr/bin/python3 "$DIR/tarcade.py"
