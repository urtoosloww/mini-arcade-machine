#!/usr/bin/env python3
"""
fbmirror.py
===========
Copies the main framebuffer (/dev/fb0) onto the SPI panel (/dev/fb1).

Why this exists: emulators expect a GPU-backed display. The ILI9341 has
no GPU, so pointing RetroArch at it directly is a fight. Instead, let
every app render normally to the main display -- where everything works
-- and mirror those pixels to the panel. The app never knows the panel
is there.

This is the fbcp-ili9341 idea, written against /dev/fb* instead of
BCM2835 registers, so it works on the Pi 5's RP1.

Usage:
    sudo python3 fbmirror.py                 # mirror fb0 -> fb1
    sudo python3 fbmirror.py --fps 20        # cap the refresh rate
    sudo python3 fbmirror.py --info          # just print framebuffer info
    sudo python3 fbmirror.py --test          # draw color bars on the panel

Dependencies: sudo apt install python3-numpy
"""

import argparse
import mmap
import os
import signal
import struct
import sys
import time

import numpy as np

# ---- fbdev ioctls (from linux/fb.h)
FBIOGET_VSCREENINFO = 0x4600
FBIOGET_FSCREENINFO = 0x4602


def fb_info(dev):
    """Return (width, height, bits_per_pixel, line_length) for /dev/fbN."""
    import fcntl
    with open(dev, "rb") as f:
        # struct fb_var_screeninfo: first fields are xres, yres,
        # xres_virtual, yres_virtual, xoffset, yoffset, bits_per_pixel
        var = fcntl.ioctl(f, FBIOGET_VSCREENINFO, b"\0" * 160)
        xres, yres, xv, yv, xo, yo, bpp = struct.unpack_from("<7I", var, 0)
        # struct fb_fix_screeninfo: line_length is at offset 48
        fix = fcntl.ioctl(f, FBIOGET_FSCREENINFO, b"\0" * 80)
        line_length = struct.unpack_from("<I", fix, 48)[0]
    return xres, yres, bpp, line_length


def open_fb(dev, height, line_length, write=False):
    flags = os.O_RDWR if write else os.O_RDONLY
    fd = os.open(dev, flags)
    size = height * line_length
    prot = mmap.PROT_READ | (mmap.PROT_WRITE if write else 0)
    mm = mmap.mmap(fd, size, mmap.MAP_SHARED, prot)
    return fd, mm, size


def to_rgb565(rgb):
    """(H,W,3) uint8 RGB -> (H,W) uint16 RGB565."""
    r = (rgb[:, :, 0].astype(np.uint16) >> 3) << 11
    g = (rgb[:, :, 1].astype(np.uint16) >> 2) << 5
    b = rgb[:, :, 2].astype(np.uint16) >> 3
    return r | g | b


def nearest_resize(src, out_h, out_w):
    """Fast nearest-neighbour downscale using index arrays."""
    sh, sw = src.shape[:2]
    yi = (np.arange(out_h) * sh // out_h).clip(0, sh - 1)
    xi = (np.arange(out_w) * sw // out_w).clip(0, sw - 1)
    return src[yi][:, xi]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", default="/dev/fb0", help="source framebuffer")
    ap.add_argument("--dst", default="/dev/fb1", help="panel framebuffer")
    ap.add_argument("--fps", type=float, default=25.0,
                    help="max refresh rate (default 25)")
    ap.add_argument("--info", action="store_true",
                    help="print framebuffer details and exit")
    ap.add_argument("--test", action="store_true",
                    help="draw colour bars on the panel and exit")
    args = ap.parse_args()

    for dev in (args.src, args.dst):
        if not os.path.exists(dev):
            print(f"{dev} does not exist.", file=sys.stderr)
            if dev == args.dst:
                print("The panel driver may not be loaded: "
                      "sudo modprobe panel-mipi-dbi", file=sys.stderr)
            sys.exit(1)

    sw, sh, sbpp, sll = fb_info(args.src)
    dw, dh, dbpp, dll = fb_info(args.dst)
    print(f"source {args.src}: {sw}x{sh} {sbpp}bpp stride={sll}")
    print(f"panel  {args.dst}: {dw}x{dh} {dbpp}bpp stride={dll}")

    if args.info:
        return

    if dbpp != 16:
        print(f"Expected a 16bpp panel, got {dbpp}bpp. "
              "Adjust the conversion if this is wrong.", file=sys.stderr)

    dfd, dmm, _ = open_fb(args.dst, dh, dll, write=True)

    if args.test:
        bars = np.zeros((dh, dw, 3), dtype=np.uint8)
        colors = [(255, 255, 255), (255, 255, 0), (0, 255, 255),
                  (0, 255, 0), (255, 0, 255), (255, 0, 0), (0, 0, 255)]
        step = dw // len(colors)
        for i, c in enumerate(colors):
            bars[:, i * step:(i + 1) * step] = c
        out = to_rgb565(bars)
        row_px = dll // 2
        buf = np.zeros((dh, row_px), dtype=np.uint16)
        buf[:, :dw] = out
        dmm.seek(0)
        dmm.write(buf.tobytes())
        print("Colour bars written to the panel.")
        dmm.close(); os.close(dfd)
        return

    sfd, smm, _ = open_fb(args.src, sh, sll, write=False)

    src_px_per_row = sll // (sbpp // 8)
    dst_px_per_row = dll // 2
    period = 1.0 / args.fps if args.fps > 0 else 0.0

    running = True

    def stop(*_):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    print(f"Mirroring at up to {args.fps} fps. Ctrl-C to stop.")
    frames = 0
    dirty_rows = 0
    prev_px = None
    t_start = time.time()
    out_buf = np.zeros((dh, dst_px_per_row), dtype=np.uint16)

    try:
        while running:
            t0 = time.perf_counter()

            smm.seek(0)
            raw = np.frombuffer(smm, dtype=np.uint8, count=sh * sll)

            if sbpp == 32:
                arr = raw.reshape(sh, src_px_per_row, 4)[:, :sw, :]
                rgb = arr[:, :, [2, 1, 0]]        # BGRA -> RGB
            elif sbpp == 16:
                arr = raw.view(np.uint16).reshape(sh, src_px_per_row)[:, :sw]
                r = ((arr >> 11) & 0x1F).astype(np.uint8) << 3
                g = ((arr >> 5) & 0x3F).astype(np.uint8) << 2
                b = (arr & 0x1F).astype(np.uint8) << 3
                rgb = np.dstack([r, g, b])
            else:
                print(f"Unsupported source depth {sbpp}bpp", file=sys.stderr)
                break

            small = nearest_resize(rgb, dh, dw)
            new_px = to_rgb565(small)

            # Dirty-band detection (the BB-CP trick): only push rows that
            # actually changed. Static menus cost almost nothing; only
            # full-screen motion pays the whole SPI bill.
            if prev_px is None:
                out_buf[:, :dw] = new_px
                dmm.seek(0)
                dmm.write(out_buf.tobytes())
                prev_px = new_px.copy()
            else:
                changed = np.flatnonzero((new_px != prev_px).any(axis=1))
                if changed.size:
                    y0, y1 = int(changed[0]), int(changed[-1]) + 1
                    out_buf[y0:y1, :dw] = new_px[y0:y1]
                    dmm.seek(y0 * dll)
                    dmm.write(out_buf[y0:y1].tobytes())
                    prev_px[y0:y1] = new_px[y0:y1]
                    dirty_rows += (y1 - y0)

            frames += 1
            if frames % 100 == 0:
                el = time.time() - t_start
                fps = frames / el
                pct = 100.0 * dirty_rows / max(1, frames * dh)
                print(f"\r{fps:.1f} fps   {pct:.0f}% of rows redrawn   ",
                      end="", flush=True)

            slack = period - (time.perf_counter() - t0)
            if slack > 0:
                time.sleep(slack)
    finally:
        smm.close(); os.close(sfd)
        dmm.close(); os.close(dfd)
        print("\nstopped")


if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Needs root for /dev/fb* -- run with sudo.", file=sys.stderr)
    main()
