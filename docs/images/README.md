Photos and recordings of the build go here.

The README expects two, and has the markdown for them commented out at
the top:

- `cabinet.jpg` — the assembled cabinet
- `menu.gif` — a recording of the launcher menu

To record the menu on the Pi:

```bash
wf-recorder -o SPI-1 -f menu.mp4        # while the arcade is running
ffmpeg -i menu.mp4 -vf "fps=15,scale=320:-1" menu.gif
```
