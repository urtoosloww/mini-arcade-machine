Build photos and recordings.

- `cabinet.jpg` — the assembled cabinet, panel showing the launcher menu
- `menu.gif` — menu, then four games, with the controls in shot

Both are referenced from the top of the README.

`menu.gif` is deliberately small (350 px, 9 fps, 72 colours) because it is
committed to the repository. It was cut from a longer phone recording with:

```bash
FILT="crop=1040:1330:20:330,fps=9,scale=350:-1:flags=lanczos"
ffmpeg -ss 8.2 -t 9.2 -i source.mov \
       -vf "${FILT},palettegen=max_colors=72:stats_mode=diff" pal.png
ffmpeg -ss 8.2 -t 9.2 -i source.mov -i pal.png \
       -lavfi "[0:v]${FILT}[x];[x][1:v]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle" \
       menu.gif
```

To record the panel itself rather than filming it:

```bash
wf-recorder -o SPI-1 -f menu.mp4        # while the arcade is running
```
