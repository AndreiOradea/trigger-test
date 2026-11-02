#!/usr/bin/env python3
"""
colorbot.py - watch a region of the screen for a color and click it.

Intended for single-player games, idle games and repetitive app tasks.
Do not use it in online multiplayer games where it would break the rules.

Usage examples:
  python colorbot.py --pick                      # find the hex of a color on screen
  python colorbot.py --color "#ff0000"           # look for red, click it
  python colorbot.py --color "#ff0000" --move-to-match  # move to the match, then click
  python colorbot.py --color "#00ff88" --tolerance 25 --region 100,200,800,600
  python colorbot.py --color "#ff0000" --no-click   # only detect, print position
  python colorbot.py --color "#8a2be2" --center 30x30   # purple at the crosshair, right-click
  python colorbot.py --color "#0000ff" --crosshair --button left  # left-click on exact center pixel

Hotkeys while running:
  F8  start / pause
  F9  quit
"""
import argparse
import math
import sys
import threading
import time

import mss
import numpy as np
from pynput import keyboard, mouse


def parse_color(text):
    text = text.strip()
    if text.startswith("#"):
        text = text[1:]
    if len(text) != 6 or any(char not in "0123456789abcdefABCDEF" for char in text):
        raise argparse.ArgumentTypeError("color must look like #RRGGBB")
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def parse_region(text):
    try:
        parts = [int(p.strip()) for p in text.split(",")]
    except ValueError:
        raise argparse.ArgumentTypeError("region must be left,top,width,height")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region must be left,top,width,height")
    left, top, width, height = parts
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("region width and height must be positive")
    return {"left": left, "top": top, "width": width, "height": height}


def parse_size(text):
    try:
        w, h = (int(p) for p in text.lower().split("x"))
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 40x40")
    if w <= 0 or h <= 0:
        raise argparse.ArgumentTypeError("size width and height must be positive")
    return w, h


def center_region(monitor, size):
    """A WxH box centered on the given monitor."""
    w, h = size
    return {
        "left": monitor["left"] + (monitor["width"] - w) // 2,
        "top": monitor["top"] + (monitor["height"] - h) // 2,
        "width": w,
        "height": h,
    }


def region_fits(region, bounds):
    return (
        region["left"] >= bounds["left"]
        and region["top"] >= bounds["top"]
        and region["left"] + region["width"] <= bounds["left"] + bounds["width"]
        and region["top"] + region["height"] <= bounds["top"] + bounds["height"]
    )


def grab_rgb(sct, region):
    """Grab a screen region as an (H, W, 3) RGB uint8 array."""
    shot = sct.grab(region)
    bgra = np.array(shot, dtype=np.uint8)
    return bgra[:, :, 2::-1]  # BGRA -> RGB


def find_color(img, target, tolerance, min_pixels):
    """Return (x, y) of a matching pixel near the middle of the matches, or None."""
    diff = np.abs(img.astype(np.int16) - np.array(target, dtype=np.int16))
    mask = diff.max(axis=2) <= tolerance
    ys, xs = np.nonzero(mask)
    if xs.size < min_pixels:
        return None
    cx, cy = xs.mean(), ys.mean()
    # Snap to the real matching pixel closest to the centroid so we never
    # click on a spot that doesn't have the color.
    i = np.argmin((xs - cx) ** 2 + (ys - cy) ** 2)
    return int(xs[i]), int(ys[i])


def pick_mode(delay):
    """Print the color under the mouse cursor after a short delay."""
    ctrl = mouse.Controller()
    print(f"Move the mouse over the color you want. Sampling in {delay}s...")
    time.sleep(delay)
    x, y = ctrl.position
    with mss.mss() as sct:
        px = grab_rgb(sct, {"left": int(x), "top": int(y), "width": 1, "height": 1})[0, 0]
    r, g, b = (int(v) for v in px)
    print(f"Position ({x}, {y})  RGB ({r}, {g}, {b})  Hex #{r:02x}{g:02x}{b:02x}")


def create_parser():
    ap = argparse.ArgumentParser(description="Screen color-detection clicker")
    ap.add_argument("--pick", action="store_true", help="print the color under the cursor and exit")
    ap.add_argument("--pick-delay", type=float, default=3.0, help="seconds before --pick samples")
    ap.add_argument("--color", type=parse_color, help="target color as #RRGGBB")
    ap.add_argument("--tolerance", type=int, default=20, help="max per-channel difference (0-255)")
    ap.add_argument("--region", type=parse_region, help="left,top,width,height (default: first monitor)")
    ap.add_argument("--center", type=parse_size, default=None,
                    help="scan only a WxH box (pixels) around the center of the first monitor, e.g. 40x40")
    ap.add_argument("--crosshair", action="store_true",
                    help="trigger only when the target color is at the exact screen-center pixel")
    ap.add_argument("--min-pixels", type=int, default=4, help="min matching pixels to count as found")
    ap.add_argument("--interval", type=float, default=0,
                    help="seconds between scans (0 = scan continuously)")
    ap.add_argument("--cooldown", type=float, default=0, help="minimum seconds between clicks")
    ap.add_argument("--button", choices=["left", "right"], default="right")
    ap.add_argument("--no-click", action="store_true", help="only detect and print, don't click")
    ap.add_argument("--max-clicks", type=int, default=0, help="stop after N clicks (0 = unlimited)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="screen pixels per mouse unit; use 2 on Retina/HiDPI if clicks land off-target")
    ap.add_argument("--move-to-match", action="store_true",
                    help="move the cursor to the matching pixel before clicking")
    ap.add_argument("--move-duration", type=float, default=0.06,
                    help="seconds for smooth cursor movement (0 = instant)")
    ap.add_argument("--move-steps", type=int, default=6,
                    help="number of interpolation steps for smooth cursor movement")
    return ap


def validate_args(ap, args):
    if not 0 <= args.tolerance <= 255:
        ap.error("--tolerance must be between 0 and 255")
    if args.min_pixels < 1:
        ap.error("--min-pixels must be at least 1")
    if not math.isfinite(args.interval) or args.interval < 0:
        ap.error("--interval must be a finite non-negative number")
    if not math.isfinite(args.cooldown) or args.cooldown < 0:
        ap.error("--cooldown must be a finite non-negative number")
    if not math.isfinite(args.pick_delay) or args.pick_delay < 0:
        ap.error("--pick-delay must be a finite non-negative number")
    if args.max_clicks < 0:
        ap.error("--max-clicks cannot be negative")
    if not math.isfinite(args.scale) or args.scale <= 0:
        ap.error("--scale must be a finite positive number")
    if not math.isfinite(args.move_duration) or args.move_duration < 0:
        ap.error("--move-duration must be a finite non-negative number")
    if args.move_steps < 1:
        ap.error("--move-steps must be at least 1")


def move_smoothly(mouse_ctrl, target, scale, duration, steps, stop):
    target_x, target_y = (int(target[0] / scale), int(target[1] / scale))
    start_x, start_y = mouse_ctrl.position
    if duration == 0:
        mouse_ctrl.position = (target_x, target_y)
        return True

    for step in range(1, steps + 1):
        progress = step / steps
        eased = progress * progress * (3 - 2 * progress)
        mouse_ctrl.position = (
            round(start_x + (target_x - start_x) * eased),
            round(start_y + (target_y - start_y) * eased),
        )
        if stop.wait(duration / steps):
            return False
    return True


def run_scanner(args, running, stop, report=print):
    mouse_ctrl = mouse.Controller()
    button = mouse.Button.left if args.button == "left" else mouse.Button.right
    clicks = 0
    target_latched = False

    report(f"Target color #{args.color[0]:02x}{args.color[1]:02x}{args.color[2]:02x}, "
           f"tolerance {args.tolerance}")
    with mss.mss() as sct:
        if len(sct.monitors) < 2:
            raise RuntimeError("No screen monitor is available")
        monitor = sct.monitors[1]
        virtual_screen = sct.monitors[0]
        if args.crosshair:
            region = center_region(monitor, (1, 1))
            min_pixels = 1
        elif args.region:
            region = args.region
            min_pixels = args.min_pixels
            if not region_fits(region, virtual_screen):
                raise ValueError("--region must fit within the virtual screen")
        elif args.center:
            region = center_region(monitor, args.center)
            min_pixels = args.min_pixels
            if not region_fits(region, monitor):
                raise ValueError("--center size must fit within the first monitor")
        else:
            region = monitor
            min_pixels = args.min_pixels
        report(f"Scanning region: {region}")
        last_click_time = float("-inf")
        while not stop.is_set():
            if not running.is_set():
                stop.wait(0.05)
                continue

            img = grab_rgb(sct, region)
            hit = find_color(img, args.color, args.tolerance, min_pixels)

            if hit:
                sx = region["left"] + hit[0]
                sy = region["top"] + hit[1]
                if args.no_click:
                    report(f"Found at ({sx}, {sy})")
                elif not target_latched:
                    now = time.monotonic()
                    if now - last_click_time < args.cooldown:
                        stop.wait(args.interval)
                        continue
                    if args.move_to_match:
                        movement_finished = move_smoothly(
                            mouse_ctrl,
                            (sx, sy),
                            args.scale,
                            args.move_duration,
                            args.move_steps,
                            stop,
                        )
                        if not movement_finished:
                            break
                    mouse_ctrl.click(button)
                    target_latched = True
                    last_click_time = time.monotonic()
                    clicks += 1
                    location = f"({sx}, {sy})" if args.move_to_match else "current cursor position"
                    report(f"Color seen at ({sx}, {sy}) -> clicked {location} [{clicks}]")
                    if args.max_clicks and clicks >= args.max_clicks:
                        report("Reached max clicks, stopping.")
                        stop.set()
                        break
                else:
                    stop.wait(args.interval)
            else:
                target_latched = False
                stop.wait(args.interval)


def main():
    ap = create_parser()
    args = ap.parse_args()
    validate_args(ap, args)
    if args.pick:
        pick_mode(args.pick_delay)
        return
    if args.color is None:
        ap.error("--color is required (or use --pick to find one)")

    running = threading.Event()
    stop = threading.Event()

    def on_press(key):
        if key == keyboard.Key.f8:
            if running.is_set():
                running.clear()
                print("PAUSED")
            else:
                running.set()
                print("RUNNING")
        elif key == keyboard.Key.f9:
            stop.set()
            return False

    print("Press F8 to start/pause, F9 to quit.")

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        run_scanner(args, running, stop)
    finally:
        listener.stop()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
