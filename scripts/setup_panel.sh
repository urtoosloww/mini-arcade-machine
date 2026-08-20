#!/bin/bash
# setup_panel.sh -- configure the ILI9341 SPI panel on a Raspberry Pi 5.
#
#     sudo bash scripts/setup_panel.sh
#
# Three things have to be true before the panel comes up, and each one
# fails silently on its own:
#
#   1. The legacy fbtft drivers must be blacklisted. The overlay's
#      compatible string starts with "ili9341", so the kernel matches
#      fb_ili9341 before it reaches panel-mipi-dbi-spi. fbtft then fails
#      on a property it does not understand and leaves the panel
#      unclaimed:  fb_ili9341 spi0.0: error -EINVAL: buswidth is not set
#
#   2. The init sequence blob must be in /lib/firmware. panel-mipi-dbi is
#      panel-agnostic; without the blob it has no power-on sequence.
#
#   3. dtparam lines must come BEFORE any dtoverlay line. A dtparam after
#      a dtoverlay is parsed as a parameter TO that overlay, so
#      `dtparam=spi=on` at the bottom of the file enables nothing.
#
# Reversible: config.txt is backed up first, and the block this adds is
# fenced with markers so a re-run replaces it rather than duplicating it.

set -e

CFG=/boot/firmware/config.txt
BLACKLIST=/etc/modprobe.d/blacklist-fbtft.conf
FW_DIR=/lib/firmware
REPO="$(cd "$(dirname "$0")/.." && pwd)"
BLOB="$REPO/firmware/ili9341.bin"

SPI_SPEED=48000000       # 320x240x16bpp = 153,600 B/frame ~= 26ms at this rate
RESET_GPIO=25
DC_GPIO=24
WIDTH=320
HEIGHT=240
WIDTH_MM=65
HEIGHT_MM=49

if [ "$EUID" -ne 0 ]; then echo "Run with sudo."; exit 1; fi
if [ ! -f "$BLOB" ]; then echo "Missing $BLOB"; exit 1; fi

echo "==> Backing up config.txt to ${CFG}.bak"
cp "$CFG" "${CFG}.bak"

echo "==> Blacklisting the legacy fbtft drivers"
# Without this, fb_ili9341 wins the match and the panel stays black.
cat > "$BLACKLIST" <<'BL'
# The mipi-dbi-spi overlay declares compatible = "ili9341", which the old
# fbtft driver also matches -- and matches first. It then fails with
# "buswidth is not set" and the modern panel-mipi-dbi-spi driver never
# gets the device. See docs/DEBUGGING.md, Bug 1.
blacklist fb_ili9341
blacklist fbtft
BL

echo "==> Installing the panel init blob to $FW_DIR"
cp "$BLOB" "$FW_DIR/"

echo "==> Removing conflicting SPI lines"
# spi0-0cs frees GPIO7/8, but GPIO8/CE0 is the LCD chip select -- must go.
sed -i '/^dtoverlay=spi0-0cs/d' "$CFG"
sed -i '/^dtparam=spi=off/d' "$CFG"
# Drop any previous run of this script.
sed -i '/# --- T-ARCADE DISPLAY ---/,/# --- END T-ARCADE ---/d' "$CFG"

echo "==> Ensuring base params come before any overlay"
sed -i '/^dtparam=spi=on/d;/^dtparam=i2c_arm=on/d' "$CFG"
sed -i '1i dtparam=spi=on\ndtparam=i2c_arm=on' "$CFG"

echo "==> Appending the display overlay"
cat >> "$CFG" <<OVL

# --- T-ARCADE DISPLAY ---
dtoverlay=mipi-dbi-spi,spi0-0,speed=$SPI_SPEED
dtparam=compatible=ili9341\0panel-mipi-dbi-spi
dtparam=write-only,cpha,cpol
dtparam=width=$WIDTH,height=$HEIGHT,width-mm=$WIDTH_MM,height-mm=$HEIGHT_MM
dtparam=reset-gpio=$RESET_GPIO,dc-gpio=$DC_GPIO
# No backlight-gpio: the backlight is wired to the 3.3V rail, because at
# 60-80mA it exceeds what a GPIO can source and trips the RP1's
# protection. See docs/DEBUGGING.md, Bug 4.
# --- END T-ARCADE ---
OVL

update-initramfs -u 2>/dev/null || true

echo
echo "Done. Reboot, then check:"
echo "    dmesg | grep -i -e mipi -e panel -e fbtft"
echo "    ls /dev/dri/                 # a new card* should appear"
echo "    wlr-randr                    # expect an output named SPI-1"
echo
echo "You should see NO fb_ili9341 lines in dmesg. If you do, the"
echo "blacklist did not take -- check $BLACKLIST and rebuild the initramfs."
echo
echo "If it goes wrong, restore with:  sudo cp ${CFG}.bak $CFG"
