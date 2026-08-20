#!/bin/bash
# doom_panel.sh -- run Doom on the SPI panel with the joystick and buttons.
#
# Wayland won't let an app place itself on a secondary output, so instead
# the panel becomes the ONLY output while the game runs. Fullscreen then
# lands there with no positioning needed.
#
# The monitor is ALWAYS restored: normal exit, crash, Ctrl-C, or watchdog.
#
#     bash ~/arcade/doom_panel.sh              # play on the panel
#     bash ~/arcade/doom_panel.sh --no-blank   # test on the monitor first

set -u
DIR="$(cd "$(dirname "$0")" && pwd)"
WAD=/usr/share/games/doom/freedoom1.wad
WATCHDOG=1800
NOBLANK=0
[ "${1:-}" = "--no-blank" ] && NOBLANK=1

SESSION="${XDG_SESSION_TYPE:-unknown}"
if [ "$SESSION" = "wayland" ]; then
    RANDR=wlr-randr
    command -v wlr-randr >/dev/null || sudo apt install -y wlr-randr
elif [ "$SESSION" = "x11" ]; then
    RANDR=xrandr
else
    RANDR=""
fi

BIG=""; PANEL=""
if [ -n "$RANDR" ]; then
    if [ "$RANDR" = wlr-randr ]; then
        OUTS=$(wlr-randr 2>/dev/null | grep -E '^[A-Za-z]' | awk '{print $1}')
    else
        OUTS=$(xrandr 2>/dev/null | awk '/ connected/{print $1}')
    fi
    for o in $OUTS; do
        case "$o" in
            *SPI*|*spi*) PANEL="$o" ;;
            *) [ -z "$BIG" ] && BIG="$o" ;;
        esac
    done
fi
echo "Session: $SESSION   monitor: ${BIG:-none}   panel: ${PANEL:-none}"
[ -z "$PANEL" ] && echo "WARNING: no SPI panel found in the display list."

restore() {
    if [ -n "${BIG:-}" ] && [ -n "$RANDR" ] && [ "$NOBLANK" = 0 ]; then
        echo; echo "Restoring $BIG ..."
        [ "$RANDR" = wlr-randr ] && wlr-randr --output "$BIG" --on 2>/dev/null \
                                 || xrandr --output "$BIG" --auto 2>/dev/null
    fi
    sudo pkill -9 -f doom_input.py 2>/dev/null
    [ -n "${WDPID:-}" ] && kill "$WDPID" 2>/dev/null
}
trap restore EXIT INT TERM HUP

if [ -n "${BIG:-}" ] && [ "$NOBLANK" = 0 ]; then
    ( sleep $WATCHDOG
      [ "$RANDR" = wlr-randr ] && wlr-randr --output "$BIG" --on 2>/dev/null \
                               || xrandr --output "$BIG" --auto 2>/dev/null ) &
    WDPID=$!
fi

# --- Doom settings for a 320x240 panel
CFG="$HOME/.local/share/chocolate-doom/default.cfg"
if [ -f "$CFG" ]; then
    sed -i 's/^fullscreen .*/fullscreen                    1/' "$CFG"
    sed -i 's/^aspect_ratio_correct .*/aspect_ratio_correct          0/' "$CFG"
    grep -q '^fullscreen' "$CFG" || echo "fullscreen                    1" >> "$CFG"
fi

# --- Input bridge (free the pins first)
sudo pkill -9 -f doom_input.py 2>/dev/null
sudo pkill -9 -f arcade_keyboard.py 2>/dev/null
sleep 1
echo "Starting controls ..."
sudo python3 "$DIR/doom_input.py" &
sleep 3

# --- Panel becomes the only output
if [ -n "${BIG:-}" ] && [ "$NOBLANK" = 0 ]; then
    echo "Switching to the panel. Hold START+SELECT 2s to quit."
    sleep 2
    [ "$RANDR" = wlr-randr ] && wlr-randr --output "$BIG" --off 2>/dev/null \
                             || xrandr --output "$BIG" --off 2>/dev/null
    sleep 1
fi

chocolate-doom -iwad "$WAD" -width 320 -height 240
