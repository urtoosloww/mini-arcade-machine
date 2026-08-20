#!/bin/bash
# setup_panel.sh -- configures the ILI9341 SPI panel on a Raspberry Pi 5.
# Run from the directory containing ili9341.bin:   sudo bash setup_panel.sh

set -e
CFG=/boot/firmware/config.txt

if [ "$EUID" -ne 0 ]; then echo "Run with sudo."; exit 1; fi
if [ ! -f "$(dirname "$0")/../firmware/ili9341.bin" ]; then echo "firmware/ili9341.bin not found"; exit 1; fi

echo "==> Backing up config.txt to ${CFG}.bak"
cp "$CFG" "${CFG}.bak"

echo "==> Installing panel firmware to /lib/firmware/"
cp "$(dirname "$0")/../firmware/ili9341.bin" /lib/firmware/

echo "==> Removing conflicting SPI lines"
# spi0-0cs frees GPIO7/8, but GPIO8/CE0 is the LCD chip select -- must go.
sed -i '/^dtoverlay=spi0-0cs/d' "$CFG"
sed -i '/^dtparam=spi=off/d' "$CFG"
# Drop any previous run of this script
sed -i '/# --- T-ARCADE DISPLAY ---/,/# --- END T-ARCADE ---/d' "$CFG"

echo "==> Ensuring base params come before any overlay"
# dtparam lines AFTER a dtoverlay are treated as params TO that overlay,
# so the bus enables must sit above everything else.
sed -i '/^dtparam=spi=on/d;/^dtparam=i2c_arm=on/d' "$CFG"
sed -i '1i dtparam=spi=on\ndtparam=i2c_arm=on' "$CFG"

echo "==> Appending display overlay"
cat >> "$CFG" <<'EOF'

# --- T-ARCADE DISPLAY ---
dtoverlay=mipi-dbi-spi,spi0-0,speed=48000000
dtparam=compatible=ili9341\0panel-mipi-dbi-spi
dtparam=write-only,cpha,cpol
dtparam=width=320,height=240,width-mm=65,height-mm=49
dtparam=reset-gpio=25,dc-gpio=24,backlight-gpio=18
# --- END T-ARCADE ---
EOF

echo
echo "Done. Reboot, then check:"
echo "    dmesg | grep -i -e mipi -e panel"
echo "    ls /dev/dri/"
echo
echo "If it goes wrong, restore with:  sudo cp ${CFG}.bak $CFG"
