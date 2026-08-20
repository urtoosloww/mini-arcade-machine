# Debugging

Nine problems that cost real time. Each is written as symptom → what was
ruled out → how it was isolated → root cause → fix, because the ruling-out
is usually the expensive part and it is what tutorials omit.

Bug 4 is the one worth reading if you only read one.

**Contents**

| # | Symptom | Root cause |
|---|---------|------------|
| [1](#bug-1-panel-stayed-black-despite-correct-wiring) | Panel black, wiring correct | Two drivers racing for spi0.0 |
| [2](#bug-2-backlight-ignored-sysfs-but-responded-to-direct-pin-writes) | Backlight ignores sysfs | `dtoverlay=pwm` also claims GPIO18 |
| [3](#bug-3-joystick-over-driving-the-adc) | ADC centre reads 20377 | Joystick on 5V, ADC max is 3.6V |
| [4](#bug-4-random-shutdowns) | Random hard shutdowns | 60–80 mA backlight on a 16 mA GPIO |
| [5](#bug-5-gpiozero-does-not-release-pins-on-close) | `GPIO busy` on handoff | gpiozero holds the chip at factory level |
| [6](#bug-6-x11-vs-wayland-on-secondary-drm-devices) | Panel vanishes under X11 | modesetting drives one DRM device |
| [7](#bug-7-launcher-vanished-after-exiting-a-game) | Menu gone after a game | Game invalidated the SDL surface |
| [8](#bug-8-sudo--e-refused) | `sudo -E` refused | sudoers rule needs `SETENV` |
| [9](#bug-9-bitmap-fonts-gone-on-trixie) | Terminal games unreadable | Trixie dropped the bitmap fonts |
| [—](#the-approach-that-did-not-work-fbmirrorpy) | *(abandoned)* framebuffer mirror | Compositor owns the DRM device |

---

## Bug 1: Panel stayed black despite correct wiring

**Symptom.** Continuity checked, 3.3 V present at the panel, the overlay
loaded, and nothing on screen. No error at the console.

**What was ruled out.** Wiring, by meter. Power, by meter. The overlay
itself, since `dtoverlay -l` showed it loaded and the device tree node was
present. The firmware blob, by rebuilding it from a different vendor init
sequence with the same result.

**How it was isolated.** `dmesg` rather than the console:

```
fb_ili9341 spi0.0: error -EINVAL: buswidth is not set
```

That name is the tell. `fb_ili9341` is the legacy **fbtft** driver. The
panel was supposed to be driven by `panel-mipi-dbi-spi`, which is a DRM
driver and would never print that.

**Root cause.** Two drivers were racing for the same SPI device. The
overlay's compatible string is:

```
dtparam=compatible=ili9341\0panel-mipi-dbi-spi
```

The kernel walks that list in order. `ili9341` matches fbtft's table, so
fbtft binds first — and then fails, because it wants a `buswidth`
property that the mipi-dbi overlay does not set. A failed bind still
consumes the device. `panel-mipi-dbi-spi` was never reached, and nothing
reported that anything had gone wrong at a level above dmesg.

**Fix.** Blacklist the legacy driver so the modern one gets the device.

```
# /etc/modprobe.d/blacklist-fbtft.conf
blacklist fb_ili9341
blacklist fbtft
```

Applied by [`scripts/setup_panel.sh`](../scripts/setup_panel.sh). After a
reboot, `dmesg | grep -i fbtft` should print nothing at all.

**Why no tutorial covers this.** Every widely-referenced SPI-display
project for the Pi is Pi 4 or earlier — `rpi-fbcp`, `fbcp-ili9341`,
`waveshare_fbcp`, `BB-CP`. They all drive the panel from userspace via
DispmanX or PIGPIO, and both of those are gone on the Pi 5: the GPIO and
SPI controllers moved to the RP1 southbridge, and DispmanX went with the
old firmware stack. There is no drop-in replacement. The in-kernel
`panel-mipi-dbi` path is the only route, and it is new enough that the
fbtft collision has not been written up.

---

## Bug 2: Backlight ignored sysfs but responded to direct pin writes

**Symptom.** The panel was driving pixels, but dark. The backlight class
device existed and accepted writes:

```bash
$ cat /sys/class/backlight/*/bl_power      # 0  (on)
$ cat /sys/class/backlight/*/brightness    # 1  (on)
```

Both read back correctly. The backlight stayed off.

**How it was isolated.** Bypass the driver and drive the pin directly:

```bash
$ pinctrl set 18 op dh
```

The backlight lit instantly. So the pin worked, the panel worked, and the
wiring worked — only the *driver's* writes were going nowhere.

**Root cause.** `dtoverlay=pwm` was also in `config.txt`, left over from
an earlier experiment. It defaults to GPIO18 — the same pin as the
backlight. Two drivers claimed one pin, the PWM driver won, and
`gpio-backlight` lost silently. It kept a backlight class device and kept
accepting writes to it; they just did not reach the hardware.

**Fix.** Remove the conflicting overlay. There is a wider lesson here that
Bug 4 makes concrete: a sysfs write that returns success has told you the
*driver* accepted it, not that the hardware did anything.

---

## Bug 3: Joystick over-driving the ADC

**Symptom.** Calibration reported a resting centre of about **20377**.

**How it was isolated.** Arithmetic, before touching anything. The ADS1115
is configured with the ±4.096 V PGA, so full scale is 32768 counts:

```
20377 / 32768 × 4.096 V ≈ 2.55 V
```

2.55 V is the midpoint of a **5 V** rail. On the 3.3 V rail the joystick
was supposed to be on, the centre should read:

```
1.65 V / 4.096 V × 32768 ≈ 13200
```

**Root cause.** The joystick's VCC was on 5 V. The ADS1115's absolute
maximum input is VDD + 0.3 V = **3.6 V**, so at full deflection the stick
was presenting 5 V to an input rated for 3.6 V — 1.4 V over, continuously.
It worked, in the sense that it produced numbers. It was also outside the
part's absolute maximum ratings, which is the section of a datasheet that
describes damage rather than accuracy.

**Fix.** Move the joystick's VCC to 3.3 V. Recalibrate; the centre lands
near 13200.

The expected value is recorded in
[`config.py`](../config.py) as `ADC_CENTER_EXPECTED_3V3`, and
`arcade_input.py --test` prints a note when the measured centre is far
from it — so the same mistake announces itself next time.

---

## Bug 4: Random shutdowns

**Symptom.** The Pi powered off completely — anywhere from 15 seconds to a
few minutes in — but **only while a game was running**. Clean power-off,
no kernel panic, no message in the journal, no warning. The menu alone
could run indefinitely.

This is the one that took the longest, because every obvious cause was
wrong and the logs after each crash looked perfectly healthy.

### What was ruled out, by measurement

**Undervoltage.** The Pi records this in a sticky register that survives a
brownout:

```bash
$ vcgencmd get_throttled
throttled=0x0
```

`0x0` means it has never been undervolted, never throttled, never
capped — not since boot, not ever in this session. Not a supply sag at the
USB-C end.

**PMIC power event.** The Pi 5's PMIC latches the reason for the last
power-off:

```bash
$ sudo vcgencmd power_reset 0
```

All zeros. The PMIC did not record a fault of the kind it records —
no overvoltage, no watchdog reset.

**Thermal.** 56–58 °C under load, against a 85 °C throttle point. Not
close.

**The supply.** Official Raspberry Pi 27 W 5 V/5 A USB-C unit, which
negotiates the full 5 A budget. Not a marginal phone charger.

**A slow sag.** This was the real hypothesis, and it needed evidence
rather than a spot check, because the shutdown was the thing that would
stop any interactive measurement. So: [`scripts/pmicwatch.sh`](../scripts/pmicwatch.sh),
which samples the PMIC's ADCs at 5 Hz and `sync`s after every line, so the
final sample survives a hard power cut instead of dying in the page cache.

Across many crashes the log said the same thing every time: `EXT5V_V` held
**5.15–5.19 V right up to the last sample**. No decline, no dip, no
gradual anything. The board was healthy and then it was off.

That log is also the reason the answer was not obvious: it looked like
exoneration.

### How it was isolated

The measurements had ruled out everything except *something the game does
that the menu does not*. Rather than guess, remove one variable at a time
and time the result:

| Test | Configuration | Result |
|------|---------------|--------|
| A | Idle, nothing running | survived |
| B | Full I2C polling, LCD unplugged | survived 15 min |
| C | Everything running **except the backlight wire** | survived 15 min |

Test B clears the ADC, the I2C bus and the polling loop. Test C is the
one that matters: everything else identical, one wire removed, and the
crash stops. The fault is in the backlight, not the software.

### Root cause

The LCD backlight was wired to **GPIO18** and drawing **60–80 mA**.

A Pi 5 GPIO pin sources about **16 mA**. That is 4–5× over, sustained. The
RP1's overcurrent protection cuts the rail when it sees that, and it acts
in microseconds — far faster than a 5 Hz sampler, or any sampler. Which
explains the log exactly: there was never a degradation to catch. The
board was fine, then the protection tripped, then it was off.

The reason it only happened during games is that the menu redraws a mostly
dark 320×240 screen at 30 fps, and games drive a bright one continuously.
Higher average backlight duty, more current, faster trip.

### Fix

Move the backlight to the **3.3 V rail**, which is designed to source
hundreds of milliamps. One wire. The crash never recurred.

The proper fix, if you want software brightness control back, is a
MOSFET: backlight to the 3.3 V rail through the drain, GPIO18 on the
gate. The GPIO then switches milliamps of gate current instead of
sourcing the load itself. This build does not do that, which is why
[the backlight is not dimmable](../README.md#limitations).

### What generalises

- `vcgencmd get_throttled` returning `0x0` rules out supply-side
  undervoltage conclusively. It does not rule out a *local* overcurrent
  trip downstream of the PMIC's sensing. Those are different failures and
  only one of them is instrumented.
- Absence of evidence in a 5 Hz log is not evidence of absence when the
  mechanism operates in microseconds. Ask what the sampling rate can
  actually resolve before treating a clean log as exculpatory.
- Three timed A/B tests found in an afternoon what a week of reading logs
  did not. When the instrumentation says everything is fine and the thing
  is still broken, stop reading and start removing variables.

---

## Bug 5: gpiozero does not release pins on `close()`

**Symptom.** The launcher holds the buttons for menu navigation and hands
them to the input bridge when a game starts. The bridge died immediately:

```
lgpio.error: 'GPIO busy'
```

The launcher had called `Button.close()` on every button first.

**Root cause.** `Button.close()` closes the *Button*. gpiozero's lgpio
backend holds the GPIO chip handle one level up, at the **pin factory**,
and the factory outlives the objects that were created through it. The
pins stay claimed by the process even though nothing in it holds a
reference to them any more.

**Fix.** Close the factory and drop it:

```python
for b in self.btn.values():
    b.close()
self.btn.clear()
if Device.pin_factory is not None:
    Device.pin_factory.close()
Device.pin_factory = None
```

Setting `pin_factory = None` matters: gpiozero lazily recreates it on the
next `Button(...)`, which is exactly what the launcher wants when it
reclaims the pins after the game exits.

Implemented in `Pad.release_pins` in
[`src/tarcade.py`](../src/tarcade.py) and mirrored in `release_gpio` in
[`src/arcade_input.py`](../src/arcade_input.py), so both ends of the
handoff clean up the same way.

---

## Bug 6: X11 vs Wayland on secondary DRM devices

**Symptom.** Under Wayland (labwc) the SPI panel appears as a second
output, `SPI-1`, and behaves like a small monitor. Switching the session
to X11 to try a different approach made the panel disappear from `xrandr`
entirely — not disabled, not disconnected: absent.

**Root cause.** The SPI panel is a separate DRM device (`card2`,
alongside the GPU's `card0`/`card1`). X11's `modesetting` driver manages
**one** DRM device per screen. Everything on the second one is invisible
to it. Wayland compositors enumerate all DRM devices and expose every
connector they find.

**Consequence.** This project is Wayland-only, and that is not a
preference. It also removed a possible workaround: under X11 you can run
a second X server bound to the panel, which is what
[`attic/`-era scripts](../attic/) attempted; under Wayland the compositor
already owns the device and will not share it.

---

## Bug 7: Launcher vanished after exiting a game

**Symptom.** Quit a game and the launcher was gone — the desktop showed
through, the process was still alive, and the menu never came back. The
first launch always worked.

**Root cause.** A game that takes over the display invalidates the
launcher's SDL surface. The pygame window handle survives as an object but
no longer refers to anything the compositor will present; blits succeed
and go nowhere.

**Fix.** Tear the display down and rebuild it after every game, rather
than trying to detect whether it survived:

```python
pygame.display.quit()
time.sleep(config.DISPLAY_REBUILD_DELAY)
pygame.display.init()
pygame.font.init()
_fonts.clear()          # see below
screen = make_display()
```

`_fonts.clear()` is the part that is easy to miss. `pygame.font.quit()`
invalidates every existing `Font` object, and the launcher caches them by
size. Reinitialising the font module without clearing that cache leaves
dangling handles, and the next `render()` segfaults — several games later,
with no obvious connection to the display rebuild.

---

## Bug 8: `sudo -E` refused

**Symptom.**

```
sudo: sorry, you are not allowed to preserve the environment
```

**Root cause.** A `NOPASSWD:` sudoers rule does not permit `-E`. Preserving
the environment across a privilege change is a separate permission, and it
needs the `SETENV` tag.

**Why it matters here.** The launcher runs as root for GPIO, but drives
the display through `wlr-randr`, which needs `WAYLAND_DISPLAY` and
`XDG_RUNTIME_DIR` to find the compositor's socket. Without them it cannot
reach the compositor, which means it cannot turn the HDMI output back
on — the exact failure that leaves a user with a dark monitor.

**Fix.**

```
youruser ALL=(ALL) NOPASSWD:SETENV: /usr/bin/python3 /path/to/src/tarcade.py
```

Installed by `bash scripts/start_arcade.sh --setup`, scoped to the three
specific command lines the arcade needs rather than blanket sudo access.

The launcher also rebuilds those variables from the desktop user's
`/run/user/<uid>` directory (`wlr_env()` in
[`src/tarcade.py`](../src/tarcade.py)) as a second line of defence, since
a dark monitor is not a failure the user can debug.

---

## Bug 9: Bitmap fonts gone on Trixie

**Symptom.** Terminal games (`ninvaders`, `bastet`) ran in an 80×24 xterm
in the corner of the screen at a default font size — unplayable on the
panel, and comically small on the monitor. Setting `-fn 4x6`, which every
guide suggests, failed outright: Debian Trixie no longer ships the legacy
bitmap fonts.

**Second problem.** `xterm -fullscreen` did nothing. It is only a *request*
to the window manager, and XWayland ignores it. The window kept its 80×24
default at whatever font was set, which starves games that need more rows
than that.

**Fix.** Two parts, both in
[`term_geometry()`](../src/tarcade.py):

1. Use a scalable Xft font (`-fa "DejaVu Sans Mono" -fs <points>`) instead
   of a bitmap font that may not exist.
2. Compute the character grid from the live screen resolution and pass it
   as an explicit `-geometry`, rather than asking for fullscreen and
   hoping.

DejaVu Sans Mono advances 0.602 em, so at 96 dpi an *S*-point cell is
about `S × 0.803` px wide and `S × 1.70` px tall. Pick the largest *S*
whose minimum grid still fits, then report the grid that size actually
yields.

This is the one piece of layout arithmetic in the project, so it is the
one with a [test across three resolutions](../tests/test_term_geometry.py) —
320×240, 640×480 and 1080p.

---

## The approach that did not work: `fbmirror.py`

Kept at [`attic/fbmirror.py`](../attic/fbmirror.py) because the reason it
failed is more useful than the code.

**The idea.** Emulators expect a GPU-backed display; the ILI9341 has none,
so pointing RetroArch at it is a fight. Instead let everything render
normally to the main display, where it works, and copy those pixels onto
the panel — the `fbcp-ili9341` approach, rewritten against `/dev/fb*`
instead of BCM2835 registers so it would work on the RP1. It even does
dirty-band detection, so static menus cost almost nothing.

**Why it does not work.** Under Wayland the compositor owns the panel's
DRM device (`card2`) directly. `/dev/fb1` is an *emulated legacy view* of
that device, and writes to it land in a shadow buffer that nothing ever
scans out. The mirror runs, reports a healthy frame rate, and puts pixels
nowhere. There is no error — the writes succeed, exactly as in Bug 2.

**What replaced it.** Stop trying to put the panel somewhere it does not
want to be, and change what "the display" means instead: disable the HDMI
output so the panel is the *only* output, and let ordinary fullscreen land
on it with no positioning, no mirroring, and no DRM negotiation. That is
the four lines at the top of `main()` in
[`src/tarcade.py`](../src/tarcade.py), and it is the single largest
simplification in the project.
