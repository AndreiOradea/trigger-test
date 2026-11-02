#!/usr/bin/env python3
"""Small graphical interface for the color detector."""
import argparse
import math
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk
from types import SimpleNamespace

import mss
from pynput import keyboard, mouse

from colorbot import grab_rgb, parse_color, parse_size, run_scanner


class ColorbotWindow:
    def __init__(self, root):
        self.root = root
        self.root.title("Colorbot")
        self.root.resizable(False, False)
        self.messages = queue.Queue()
        self.running = threading.Event()
        self.stop = threading.Event()
        self.worker = None
        self.keyboard_listener = None

        frame = ttk.Frame(root, padding=14)
        frame.grid()
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="Target color (hex):").grid(row=0, column=0, sticky="w")
        self.color = tk.StringVar(value="#e050ff")
        ttk.Entry(frame, textvariable=self.color, width=14).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Button(frame, text="Pick from screen", command=self.pick_color).grid(row=0, column=2)

        ttk.Label(frame, text="Tolerance (0-255):").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.tolerance = tk.StringVar(value="45")
        ttk.Entry(frame, textvariable=self.tolerance, width=8).grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))

        self.center_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(frame, text="Scan center area only", variable=self.center_only).grid(
            row=2, column=0, columnspan=2, sticky="w", pady=(10, 0)
        )
        self.center_size = tk.StringVar(value="180x200")
        ttk.Label(frame, text="Center area size:").grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Entry(frame, textvariable=self.center_size, width=10).grid(
            row=3, column=1, sticky="w", padx=8, pady=(6, 0)
        )

        ttk.Label(frame, text="Reaction interval (seconds; 0 = fastest):").grid(
            row=4, column=0, sticky="w", pady=(10, 0)
        )
        self.interval = tk.StringVar(value="0")
        ttk.Entry(frame, textvariable=self.interval, width=8).grid(
            row=4, column=1, sticky="w", padx=8, pady=(10, 0)
        )

        self.crosshair_only = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            frame,
            text="Trigger only when target color is on exact center pixel (1x1 px)",
            variable=self.crosshair_only,
        ).grid(row=5, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self.no_click = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Detection only (don't click)", variable=self.no_click).grid(
            row=6, column=0, columnspan=3, sticky="w", pady=(10, 0)
        )
        self.move_to_match = tk.BooleanVar(value=False)
        ttk.Checkbutton(frame, text="Move cursor to detected pixel before clicking", variable=self.move_to_match).grid(
            row=7, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

        ttk.Label(frame, text="Cursor movement time (seconds; lower = faster):").grid(
            row=8, column=0, sticky="w", pady=(10, 0)
        )
        self.move_duration = tk.StringVar(value="0.06")
        ttk.Entry(frame, textvariable=self.move_duration, width=8).grid(
            row=8, column=1, sticky="w", padx=8, pady=(10, 0)
        )
        ttk.Label(frame, text="Smoothness (movement steps):").grid(
            row=9, column=0, sticky="w", pady=(6, 0)
        )
        self.move_steps = tk.StringVar(value="6")
        ttk.Entry(frame, textvariable=self.move_steps, width=8).grid(
            row=9, column=1, sticky="w", padx=8, pady=(6, 0)
        )

        ttk.Label(frame, text="Click cooldown (seconds; 0 = no wait):").grid(
            row=10, column=0, sticky="w", pady=(10, 0)
        )
        self.cooldown = tk.StringVar(value="0")
        ttk.Entry(frame, textvariable=self.cooldown, width=8).grid(
            row=10, column=1, sticky="w", padx=8, pady=(10, 0)
        )
        ttk.Label(frame, text="Action: left-click when matching color is detected").grid(
            row=11, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        ttk.Label(frame, text="F8 pauses/resumes; F9 stops. The window minimizes after Start.").grid(
            row=12, column=0, columnspan=3, sticky="w", pady=(4, 0)
        )

        controls = ttk.Frame(frame)
        controls.grid(row=13, column=0, columnspan=3, sticky="w", pady=(14, 8))
        self.start_button = ttk.Button(controls, text="Start scanning", command=self.start_scanning)
        self.start_button.grid(row=0, column=0, padx=(0, 8))
        self.stop_button = ttk.Button(controls, text="Stop", command=self.stop_scanning, state="disabled")
        self.stop_button.grid(row=0, column=1)

        self.status = tk.StringVar(value="Ready. Start scanning to left-click on a color match.")
        ttk.Label(frame, textvariable=self.status, wraplength=430).grid(
            row=14, column=0, columnspan=3, sticky="w", pady=(0, 8)
        )
        self.log = tk.Text(frame, width=58, height=8, state="disabled", wrap="word")
        self.log.grid(row=15, column=0, columnspan=3)

        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(100, self.read_messages)

    def write_log(self, message):
        self.log.configure(state="normal")
        self.log.insert("end", message + "\n")
        line_count = int(self.log.index("end-1c").split(".")[0])
        if line_count > 100:
            self.log.delete("1.0", f"{line_count - 100}.0")
        self.log.see("end")
        self.log.configure(state="disabled")

    def pick_color(self):
        self.status.set("Move the pointer over the desired color; sampling in 3 seconds...")
        self.root.after(3000, self.sample_color)

    def sample_color(self):
        try:
            x, y = mouse.Controller().position
            with mss.mss() as sct:
                pixel = grab_rgb(
                    sct,
                    {"left": int(x), "top": int(y), "width": 1, "height": 1},
                )[0, 0]
            red, green, blue = (int(value) for value in pixel)
            sampled = f"#{red:02x}{green:02x}{blue:02x}"
            self.color.set(sampled)
            self.status.set(f"Sampled {sampled} at ({x}, {y}).")
        except Exception as exc:
            self.status.set(f"Could not sample the screen: {exc}")

    def start_scanning(self):
        try:
            target = parse_color(self.color.get())
            tolerance = int(self.tolerance.get())
            if not 0 <= tolerance <= 255:
                raise ValueError("Tolerance must be between 0 and 255.")
            cooldown = float(self.cooldown.get())
            if not math.isfinite(cooldown) or cooldown < 0:
                raise ValueError("Cooldown must be a finite non-negative number.")
            interval = float(self.interval.get())
            if not math.isfinite(interval) or interval < 0:
                raise ValueError("Reaction interval must be a finite non-negative number.")
            move_duration = float(self.move_duration.get())
            if not math.isfinite(move_duration) or move_duration < 0:
                raise ValueError("Cursor movement time must be a finite non-negative number.")
            move_steps = int(self.move_steps.get())
            if move_steps < 1:
                raise ValueError("Movement steps must be at least 1.")
            center = parse_size(self.center_size.get()) if self.center_only.get() else None
        except (ValueError, argparse.ArgumentTypeError) as exc:
            messagebox.showerror("Invalid setting", str(exc), parent=self.root)
            return

        args = SimpleNamespace(
            color=target,
            tolerance=tolerance,
            button="left",
            no_click=self.no_click.get(),
            move_to_match=self.move_to_match.get(),
            crosshair=self.crosshair_only.get(),
            scale=1.0,
            max_clicks=0,
            region=None,
            center=center,
            min_pixels=1 if self.crosshair_only.get() else 4,
            interval=interval,
            cooldown=cooldown,
            move_duration=move_duration,
            move_steps=move_steps,
        )
        self.stop.clear()
        self.running.clear()
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("Switch to your game. Scanning starts in 3 seconds.")
        if args.no_click:
            action = "detecting only"
        elif args.crosshair:
            action = "left-clicking when target color reaches the crosshair"
        else:
            action = "left-clicking on each match"
        self.write_log(f"Arming to start {action}. F8 pauses/resumes; F9 stops.")
        self.keyboard_listener = keyboard.Listener(on_press=self.on_hotkey)
        self.keyboard_listener.start()
        self.worker = threading.Thread(target=self.scan_worker, args=(args,), daemon=True)
        self.worker.start()
        self.root.iconify()
        self.root.after(3000, self.arm_scanning)

    def arm_scanning(self):
        if not self.stop.is_set() and self.worker and self.worker.is_alive():
            self.running.set()
            self.messages.put("Scanning started.")

    def on_hotkey(self, key):
        if key == keyboard.Key.f8:
            if self.running.is_set():
                self.running.clear()
                self.messages.put("Paused (F8).")
            elif not self.stop.is_set():
                self.running.set()
                self.messages.put("Scanning resumed (F8).")
        elif key == keyboard.Key.f9:
            self.stop.set()
            self.messages.put("Stopping (F9)...")
            return False

    def scan_worker(self, args):
        try:
            run_scanner(args, self.running, self.stop, self.messages.put)
        except Exception as exc:
            self.messages.put(f"ERROR: {exc}")
        finally:
            self.messages.put("__STOPPED__")

    def stop_scanning(self):
        self.stop.set()
        self.status.set("Stopping...")

    def read_messages(self):
        try:
            while True:
                message = self.messages.get_nowait()
                if message == "__STOPPED__":
                    self.start_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    if self.keyboard_listener:
                        self.keyboard_listener.stop()
                        self.keyboard_listener = None
                    if not self.status.get().startswith("ERROR:"):
                        self.status.set("Stopped." if self.stop.is_set() else "Ready.")
                else:
                    self.write_log(message)
                    if message.startswith("ERROR:"):
                        self.status.set(message)
                        self.stop.set()
                    elif message in ("Paused (F8).", "Scanning resumed (F8).", "Stopping (F9)...", "Scanning started."):
                        self.status.set(message)
        except queue.Empty:
            pass
        self.root.after(100, self.read_messages)

    def close(self):
        self.stop.set()
        if self.keyboard_listener:
            self.keyboard_listener.stop()
        self.root.destroy()


def main():
    root = tk.Tk()
    ColorbotWindow(root)
    root.mainloop()


if __name__ == "__main__":
    main()
