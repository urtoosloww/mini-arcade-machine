# Pi 5 Mini Arcade — Software Setup

## 0. Enable the buses

`/boot/firmware/config.txt`:

```
dtparam=spi=on
dtparam=i2c_arm=on
dtparam=i2c_arm_baudrate=400000
```

Reboot, then confirm the ADS1115 answers:

```bash
sudo apt install i2c-tools
i2cdetect -y 1        # expect 48
```

---

## 1. Gamepad bridge

```bash
sudo apt install python3-evdev python3-smbus2 python3-gpiozero python3-lgpio
mkdir -p ~/arcade && cd ~/arcade
# copy arcade_gamepad.py here

python3 arcade_gamepad.py --test        # wiggle stick, mash buttons, sanity check
python3 arcade_gamepad.py --calibrate   # record center + range
sudo python3 arcade_gamepad.py          # should print "Virtual gamepad up"
```

In a second terminal:

```bash
ls /dev/input/js*
sudo apt install joystick && jstest /dev/input/js0
```

If an axis moves backwards, flip `INVERT_X` / `INVERT_Y` near the top of the script.
If the stick drifts at rest, raise `DEADZONE` to 0.18.

**Run at boot:**

```bash
sudo cp arcade-gamepad.service /etc/systemd/system/
sudo systemctl enable --now arcade-gamepad
systemctl status arcade-gamepad
```

**Running without sudo** (optional). Create `/etc/udev/rules.d/99-uinput.rules`:

```
KERNEL=="uinput", GROUP="input", MODE="0660", OPTIONS+="static_node=uinput"
```

Then `sudo usermod -aG input $USER` and reboot.

---

## 2. Display: ILI9341 as a real DRM device

`fbcp-ili9341` — the tool every tutorial recommends — **will not work on the Pi 5**.
It writes BCM2835 registers directly, and the Pi 5 moved GPIO and SPI onto the RP1
southbridge. The replacement is the in-kernel `panel-mipi-dbi` DRM driver, driven
by the `mipi-dbi-spi` overlay that ships with current Raspberry Pi OS.

### 2a. Build the init firmware blob

`panel-mipi-dbi` is panel-agnostic — it needs a binary file containing your panel's
power-on command sequence.

```bash
git clone https://github.com/notro/panel-mipi-dbi.git
cd panel-mipi-dbi
```

Save this as `ili9341.txt`:

```
# ILI9341 320x240, landscape, 16bpp
01              # SWRESET
delay 150
11              # SLPOUT
delay 150
3A 55           # COLMOD: 16 bits/pixel
36 28           # MADCTL: MV | BGR  (landscape)
2A 00 00 01 3F  # CASET  0..319
2B 00 00 00 EF  # PASET  0..239
13              # NORON
29              # DISPON
delay 100
```

```bash
./mipi-dbi-cmd ili9341.bin ili9341.txt
sudo cp ili9341.bin /lib/firmware/
```

If the image comes out mirrored or upside down, change the `36` parameter.
`28` and `E8` are the two usual landscape values; `48` and `88` are the portrait ones.

### 2b. Overlay

Append to `/boot/firmware/config.txt`:

```
dtoverlay=mipi-dbi-spi,spi0-0,speed=48000000
dtparam=compatible=ili9341\0panel-mipi-dbi-spi
dtparam=write-only,cpha,cpol
dtparam=width=320,height=240,width-mm=65,height-mm=49
dtparam=reset-gpio=25,dc-gpio=24,backlight-gpio=18
```

Reboot and verify:

```bash
dmesg | grep -i mipi
ls /dev/dri/          # a new card* should appear
sudo apt install kmsxx-utils && kmsprint
```

You're looking for a connector named `SPI-1`.

### 2c. Ceiling to expect

320 × 240 × 16 bpp = 153,600 bytes per frame. At 48 MHz that's about 26 ms of pure
bus time, so **roughly 30 fps is your hard ceiling** and mid-20s is realistic. Fine
for 1980s arcade, NES, Game Boy, Genesis. Painful past SNES.

---

## 3. Emulation

### RetroPie on Pi 5

There is no official Pi 5 image — the last one (v4.8) predates the board. You install
on top of Raspberry Pi OS Lite 64-bit (Bookworm) from source:

```bash
sudo apt update && sudo apt install -y git lsb-release
git clone --depth=1 https://github.com/RetroPie/RetroPie-Setup.git
cd RetroPie-Setup
sudo ./retropie_setup.sh
# -> Basic install (takes ~45-60 min)
```

Alternatively **Batocera** or **Lakka** ship prebuilt Pi 5 images and are a much
shorter path if you don't need RetroPie's customization.

### Pointing it at the SPI panel

The SPI panel is a GPU-less DRM device — there's no EGL on it, so RetroArch's `gl`
and `glcore` drivers won't initialize. Use the software path in
`/opt/retropie/configs/all/retroarch.cfg`:

```
video_driver = "sdl2"
video_fullscreen = "true"
video_smooth = "false"
video_threaded = "true"
video_vsync = "false"
```

and launch with SDL aimed at the right card:

```bash
export SDL_VIDEODRIVER=kmsdrm
export SDL_KMSDRM_DEVICE_INDEX=1     # try 0 and 1; whichever is SPI-1
```

Software rendering a 320×240 arcade core is trivial work for a Cortex-A76 — the
SPI bus is your only bottleneck, not the CPU.

**This step is the fiddly one.** If it fights you, the escape hatch is `wl-mirror`
(mirror an HDMI output onto `SPI-1`) — it works reliably but costs you several more
frames per second because every frame goes through a compositor first.

### Controls

Because the bridge presents a standard gamepad, EmulationStation's first-boot
controller wizard just picks it up. Press the buttons when prompted. Nothing
emulator-specific to configure.

Make sure `arcade-gamepad.service` starts *before* EmulationStation, or the wizard
won't see it — the `Before=` line in the unit file handles this.

---

## 4. Native pygame path

If you'd rather write your own games than emulate — genuinely worth considering,
since it dodges the entire display-driver problem:

```bash
sudo apt install python3-pygame
```

```python
import os, pygame
os.environ["SDL_VIDEODRIVER"] = "kmsdrm"
os.environ["SDL_KMSDRM_DEVICE_INDEX"] = "1"

pygame.init()
screen = pygame.display.set_mode((320, 240))
pygame.joystick.init()
stick = pygame.joystick.Joystick(0)   # your bridge, no extra code
stick.init()

x = stick.get_axis(0)                 # -1.0 .. 1.0
fire = stick.get_button(0)
```

The bridge means your game code never touches I2C or GPIO.

---

## 5. On ROMs

MAME ships a set of freely redistributable ROMs, and there's a healthy homebrew scene
for NES/Game Boy/Genesis with legally free titles. Beyond that, dumping carts you own
is the legitimate route — the legal status of downloaded commercial ROMs is not
ambiguous in the US regardless of whether you own the cartridge.

---

## Recommendation

Three ways to go, ranked by effort-to-result:

**1. HDMI display, SPI panel as marquee.** A 4–5" HDMI panel runs ~$25 and gives you
60 fps with zero driver work — RetroPie boots and just works. Then use the ILI9341
as a marquee, scoreboard, or attract-mode screen above the main display. That's a
better-looking cabinet *and* less work, and it's what I'd build.

**2. SPI panel as the main display.** Everything above works, and ~25 fps on 80s
arcade titles is legitimately playable. Budget an evening for section 2 — the
firmware blob and MADCTL orientation are trial and error.

**3. Skip emulation, write your own.** Pygame at 320×240 hitting 30 fps is easy, and
you end up with something that's actually yours. Also the only option with a clean
story if you ever want to post the build publicly.

The gamepad bridge is identical in all three. Build that first, confirm `jstest`
shows clean input, and then the display decision is reversible.
