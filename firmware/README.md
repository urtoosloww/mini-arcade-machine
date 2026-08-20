# Panel init firmware

`panel-mipi-dbi` is panel-agnostic: it knows how to talk MIPI DBI over
SPI, but not how to wake up any particular controller. The power-on
command sequence comes from a binary blob in `/lib/firmware`.

- `ili9341.txt` — the sequence, commented, in `mipi-dbi-cmd` source form
- `ili9341.bin` — the compiled blob, installed by `scripts/setup_panel.sh`

## Rebuilding

```bash
git clone https://github.com/notro/panel-mipi-dbi.git
cd panel-mipi-dbi
./mipi-dbi-cmd /path/to/firmware/ili9341.bin /path/to/firmware/ili9341.txt
sudo cp ili9341.bin /lib/firmware/
sudo reboot
```

## Orientation

`command 0x36` is MADCTL, and its parameter sets rotation and colour
order. If the image comes out mirrored or upside down, that byte is what
to change:

| Value | Orientation |
|-------|-------------|
| `0x28` | landscape (used here — MV \| BGR) |
| `0xE8` | landscape, flipped |
| `0x48` | portrait |
| `0x88` | portrait, flipped |

This is trial and error; there are only four values to try.

`command 0x3A 0x55` sets 16 bits per pixel (RGB565), which the overlay's
`width`/`height` parameters and the framebuffer format both assume. Do not
change it without changing those.
