# Wiring

Raspberry Pi 5, Raspberry Pi OS Trixie. Pin numbers below are **BCM**,
which is what `config.py` and `pinctrl` use.

Three of these connections are not free choices — they are the ones that
cost time to get wrong. They are marked and explained.

---

## ILI9341 3.2" SPI LCD — 320×240, RGB565

| LCD pin | Pi 5 pin | BCM | Note |
|---------|----------|-----|------|
| VCC | 17 | 3V3 | |
| GND | 20 | GND | |
| CS | 24 | **GPIO8 / CE0** | **Not a free choice — see below** |
| RESET | 22 | GPIO25 | set in the overlay |
| DC / RS | 18 | GPIO24 | set in the overlay |
| SDI / MOSI | 19 | GPIO10 | SPI0 |
| SCK | 23 | GPIO11 | SPI0 |
| LED / BL | 1 | **3V3 rail** | **Not a GPIO — see below** |
| SDO / MISO | — | — | not connected; the panel is write-only |

### CS must be GPIO8/CE0

The `mipi-dbi-spi` overlay is instantiated as `spi0-0` — SPI0, chip
select 0 — and CE0 is GPIO8. The driver asserts that specific line. Any
other pin leaves the panel unselected and silent.

This also rules out `dtoverlay=spi0-0cs`, which frees GPIO7/8 for general
use. `setup_panel.sh` removes that line if it finds it.

### The backlight is on the 3.3 V rail, not a GPIO

It draws **60–80 mA**. A Pi 5 GPIO sources about **16 mA**. Wiring it to
GPIO18 tripped the RP1's overcurrent protection and powered the board off
mid-game — see [DEBUGGING.md, Bug 4](DEBUGGING.md#bug-4-random-shutdowns).

The cost of the fix is that the backlight is always on and not dimmable.
To get brightness control back, put a MOSFET between the backlight and
3.3 V with GPIO18 on the gate; the GPIO then switches the load instead of
sourcing it.

Also make sure `dtoverlay=pwm` is **not** in `config.txt`. It defaults to
GPIO18 and will silently take the pin from `gpio-backlight`
([Bug 2](DEBUGGING.md#bug-2-backlight-ignored-sysfs-but-responded-to-direct-pin-writes)).

---

## ADS1115 16-bit ADC — I²C, address 0x48

| ADS1115 pin | Pi 5 pin | BCM | Note |
|-------------|----------|-----|------|
| VDD | 1 | 3V3 | |
| GND | 6 | GND | |
| SCL | 5 | GPIO3 | I²C1 |
| SDA | 3 | GPIO2 | I²C1 |
| ADDR | — | GND | selects 0x48 |
| A1 | joystick VRx | | horizontal |
| A3 | joystick VRy | | vertical |

`ADDR` to GND/VDD/SDA/SCL gives 0x48/0x49/0x4A/0x4B respectively. Confirm
with:

```bash
sudo apt install i2c-tools
i2cdetect -y 1        # expect 48
```

The ADC is configured for single-shot conversions at 860 SPS on the
±4.096 V range. Single-shot rather than continuous because one input mux
is shared by four channels, and continuous mode would just re-read
whichever one was last selected.

---

## Analog joystick

| Joystick pin | Goes to | Note |
|--------------|---------|------|
| VCC | **3.3 V** | **Not 5 V — see below** |
| GND | GND | |
| VRx | ADS1115 A1 | |
| VRy | ADS1115 A3 | |
| SW | — | not used |

### VCC must be 3.3 V

The ADS1115's absolute maximum input is VDD + 0.3 V = **3.6 V**. On a 5 V
rail the stick presents up to 5 V to that input — 1.4 V over the rating,
continuously. It produces plausible-looking numbers while doing so, which
is why this is worth checking rather than assuming
([Bug 3](DEBUGGING.md#bug-3-joystick-over-driving-the-adc)).

Quick check: run `python3 src/arcade_input.py --test` and read the printed
centre. On 3.3 V it should be near **13200**. Near **20377** means the
stick is on 5 V.

### The stick is mounted rotated 90°

Corrected in software, in one place, because the bracket is glued:

```
physical UP    -> LEFT
physical RIGHT -> UP
physical DOWN  -> RIGHT
physical LEFT  -> DOWN
```

Both the launcher and the input bridge import
[`padmap.py`](../src/padmap.py) for this, so the mapping cannot drift
between the menu and the game — which it did, once, and is the reason the
module exists. Set `ROTATE_90 = False` in `config.py` if your stick is
mounted straight; the [rotation test](../tests/test_padmap.py) covers both.

---

## Buttons

Four tactile buttons, one side to the GPIO, the other to GND. Active-low
against gpiozero's internal pull-ups, so no external resistors.

| Button | Pi 5 pin | BCM |
|--------|----------|-----|
| A | 29 | GPIO5 |
| B | 31 | GPIO6 |
| Start | 38 | GPIO20 |
| Select | 40 | GPIO21 |

What each does depends on the game — see the profiles in
[`arcade_input.py`](../src/arcade_input.py). Two combinations are global:

| Input | Effect |
|-------|--------|
| Start + Select, tapped | sends `Y` (answers Doom's quit prompt) |
| Start + Select, held 2 s | force-kills the running game |
| Start held 3 s (in the menu) | shutdown prompt |
| Select held 2 s (in the menu) | quit to the desktop |

---

## Pin summary

Everything the cabinet occupies:

```
GPIO2  SDA    ADS1115
GPIO3  SCL    ADS1115
GPIO5  A      button
GPIO6  B      button
GPIO8  CE0    LCD chip select   (fixed by the overlay)
GPIO10 MOSI   LCD
GPIO11 SCLK   LCD
GPIO20 Start  button
GPIO21 Select button
GPIO24 DC     LCD
GPIO25 RESET  LCD
GPIO18        DELIBERATELY UNUSED -- see Bug 4
```

`config.py` holds all of these. `PANEL_BACKLIGHT_GPIO` is `None` there on
purpose, with the reason next to it.
