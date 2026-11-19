"""Plain MIDI: a small desktop front end for the editable generator."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import random
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from workflow import APP_DIR, GENRE_LABELS, ROLE_LABELS, build_desktop_session, desktop_directory
from generator import GENRES
from file_drag import drag_file

COLORS = {"bg": "#13171c", "panel": "#1d242c", "text": "#eef2f6",
          "muted": "#9baab9", "accent": "#7ce0c2", "line": "#35414e"}
PALETTE = ["#f3b774", "#729cda", "#e8c875", "#8fcca9", "#ad91e3", "#ed90a5", "#69b7cb", "#5c97a8", "#77848e"]


def create_root():
    return tk.Tk()


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Plain MIDI — eight bars, your way")
        self.root.geometry("1100x840")
        self.root.minsize(920, 760)
        self.root.configure(bg=COLORS["bg"])
        self.events = queue.Queue()
        self.busy = False
        self.result = None
        self.drag_origin = None
        self.repeat_seed = tk.BooleanVar(value=False)
        self.var = {
            "genre": tk.StringVar(value="House"), "key": tk.StringVar(value="A"),
            "mode": tk.StringVar(value="minor"), "bpm": tk.StringVar(value=str(GENRES["house"]["bpm"])),
            "progression": tk.StringVar(value=GENRES["house"]["progression"]), "seed": tk.StringVar(value="42"),
            "density": tk.DoubleVar(value=.5), "swing": tk.DoubleVar(value=GENRES["house"]["swing"]),
            "variation": tk.DoubleVar(value=.3), "chord_style": tk.StringVar(value="block"),
            "lead_style": tk.StringVar(value="lead"), "drum_variants": tk.BooleanVar(value=True),
        }
        self.keep = {role: tk.BooleanVar(value=False) for role in ROLE_LABELS}
        self.last_genre = "house"
        self.status = tk.StringVar(value="Choose a genre and generate. All nine parts arrive together.")
        self.chords = tk.StringVar(value="One progression. Different jobs for each instrument.")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TFrame", background=COLORS["bg"])
        style.configure("Panel.TFrame", background=COLORS["panel"])
        style.configure("TLabel", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 10))
        style.configure("Small.TLabel", foreground=COLORS["muted"], font=("Segoe UI", 9))
        style.configure("Title.TLabel", font=("Segoe UI", 26, "bold"))
        style.configure("TButton", font=("Segoe UI", 10), padding=(12, 8), background=COLORS["panel"], foreground=COLORS["text"])
        style.map("TButton", background=[("active", "#34434e")], foreground=[("disabled", "#697783")])
        style.configure("Accent.TButton", background=COLORS["accent"], foreground="#10231d", font=("Segoe UI", 11, "bold"))
        style.map("Accent.TButton", background=[("active", "#a0f0d7"), ("disabled", "#344b45")])
        style.configure("TCheckbutton", background=COLORS["bg"], foreground=COLORS["text"], font=("Segoe UI", 9))
        style.map("TCheckbutton", background=[("active", COLORS["bg"])])
        style.configure("TCombobox", fieldbackground="#edf0f4", foreground="#172028", padding=5)
        style.configure("TEntry", fieldbackground="#edf0f4", foreground="#172028", padding=5)
        style.configure("Horizontal.TScale", background=COLORS["bg"], troughcolor=COLORS["panel"])
        outer = ttk.Frame(root, padding=24)
        outer.pack(fill="both", expand=True)
        header = ttk.Frame(outer)
        header.pack(fill="x")
        ttk.Label(header, text="Plain MIDI", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="8 BARS  /  9 PARTS  /  32 DRUMS", foreground=COLORS["accent"]).pack(side="right", pady=12)
        ttk.Label(outer, text="Simple musical building blocks. Every note is yours to change.", style="Small.TLabel").pack(anchor="w", pady=(0, 18))
        settings = ttk.Frame(outer)
        settings.pack(fill="x")
        for i in range(6):
            settings.columnconfigure(i, weight=1)
        choices = [("Genre", "genre", list(GENRE_LABELS.values())),
                   ("Key", "key", ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]),
                   ("Scale", "mode", ["minor", "major"]), ("Tempo", "bpm", None),
                   ("Chords", "chord_style", ["block", "rhythm"]),
                   ("Melody", "lead_style", ["lead", "pluck"])]
        for col, (label, key, values) in enumerate(choices):
            ttk.Label(settings, text=label, style="Small.TLabel").grid(row=0, column=col, sticky="w", padx=(0, 12))
            widget = ttk.Combobox(settings, textvariable=self.var[key], values=values, state="readonly", width=15 if key == "genre" else 10) if values else ttk.Entry(settings, textvariable=self.var[key], width=10)
            widget.grid(row=1, column=col, sticky="ew", padx=(0, 12), pady=(4, 12))
            if key == "genre":
                widget.bind("<<ComboboxSelected>>", self.change_genre)
        row = ttk.Frame(outer)
        row.pack(fill="x")
        ttk.Label(row, text="Progression", style="Small.TLabel").pack(side="left", padx=(0, 10))
        ttk.Entry(row, textvariable=self.var["progression"], width=26).pack(side="left")
        ttk.Label(row, text="Degrees or names: 1 6 3 7 / Am F C G", style="Small.TLabel").pack(side="left", padx=12)
        ttk.Label(row, text="Seed", style="Small.TLabel").pack(side="left", padx=(8, 6))
        ttk.Entry(row, textvariable=self.var["seed"], width=10).pack(side="left")
        ttk.Checkbutton(row, text="Repeat seed", variable=self.repeat_seed).pack(side="left", padx=(10, 0))
        knobs = ttk.Frame(outer)
        knobs.pack(fill="x", pady=(16, 8))
        for i, (label, key, maximum) in enumerate([("Density", "density", 1), ("Swing", "swing", .6), ("Variation", "variation", 1)]):
            knobs.columnconfigure(i, weight=1)
            panel = ttk.Frame(knobs)
            panel.grid(row=0, column=i, sticky="ew", padx=(0, 18))
            number = tk.StringVar()
            def refresh(*args, k=key, n=number, l=label):
                n.set(f"{l}   {self.var[k].get():.0%}")
            self.var[key].trace_add("write", refresh)
            refresh()
            ttk.Label(panel, textvariable=number, style="Small.TLabel").pack(anchor="w")
            ttk.Scale(panel, from_=.1 if key == "density" else 0, to=maximum, variable=self.var[key]).pack(fill="x")
        ttk.Checkbutton(outer, text="Use the other drum sounds for accents and fills", variable=self.var["drum_variants"]).pack(anchor="w")
        ttk.Label(outer, textvariable=self.chords, foreground=COLORS["accent"], font=("Segoe UI", 12, "bold")).pack(anchor="w", pady=(18, 8))
        self.canvas = tk.Canvas(outer, height=240, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        keep_row = ttk.Frame(outer)
        keep_row.pack(fill="x", pady=(12, 8))
        ttk.Label(keep_row, text="Keep on next variation:", style="Small.TLabel").pack(side="left", padx=(0, 8))
        for role, label in ROLE_LABELS.items():
            if role == "NOISE":
                continue
            ttk.Checkbutton(keep_row, text=label, variable=self.keep[role]).pack(side="left", padx=3)
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(6, 10))
        self.generate_button = ttk.Button(actions, text="Generate to Desktop", command=self.generate, style="Accent.TButton")
        self.generate_button.pack(side="left")
        self.variation_button = ttk.Button(actions, text="Regenerate", command=lambda: self.generate(new_seed=True))
        self.variation_button.pack(side="left", padx=8)
        self.play_button = ttk.Button(actions, text="Play preview", command=self.play, state="disabled")
        self.play_button.pack(side="left")
        ttk.Button(actions, text="Stop", command=self.stop).pack(side="left", padx=8)
        self.project_button = ttk.Button(actions, text="Open MIDI / project", command=self.open_project, state="disabled")
        self.project_button.pack(side="left")
        self.folder_button = ttk.Button(actions, text="Open files", command=self.open_folder, state="disabled")
        self.folder_button.pack(side="right")
        export_row = ttk.Frame(outer)
        export_row.pack(fill="x", pady=(0, 8))
        self.drag_button = ttk.Button(export_row, text="Drag MIDI", state="disabled", cursor="hand2")
        self.drag_button.pack(side="left")
        self.drag_button.bind("<ButtonPress-1>", self.drag_press)
        self.drag_button.bind("<B1-Motion>", self.drag_motion)
        self.drag_button.bind("<ButtonRelease-1>", self.drag_release)
        ttk.Label(export_row, text="Hold and drag all nine parts as one MIDI file into your DAW or a folder.", style="Small.TLabel").pack(side="left", padx=12)
        ttk.Label(outer, textvariable=self.status, wraplength=1000).pack(anchor="w", pady=(2, 5))
        footer = ttk.Frame(outer)
        footer.pack(fill="x")
        ttk.Label(footer, text="Experimental: random MIDI file on Desktop; samples in Desktop / Plain MIDI Files.", style="Small.TLabel").pack(side="left")
        ttk.Button(footer, text="Desktop", command=lambda: os.startfile(str(desktop_directory()))).pack(side="right")
        self.root.after(100, self.poll)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.draw()

    def change_genre(self, event=None):
        key = next(k for k, v in GENRE_LABELS.items() if v == self.var["genre"].get())
        self.var["bpm"].set(str(GENRES[key]["bpm"]))
        self.var["swing"].set(GENRES[key]["swing"])
        if self.var["progression"].get() == GENRES[self.last_genre]["progression"]:
            self.var["progression"].set(GENRES[key]["progression"])
        self.last_genre = key

    def configuration(self):
        config = {k: value.get() for k, value in self.var.items()}
        config["genre"] = next(k for k, v in GENRE_LABELS.items() if v == config["genre"])
        config["bpm"] = float(config["bpm"])
        config["seed"] = int(config["seed"])
        return config

    def generate(self, new_seed=None):
        if self.busy:
            return
        self.stop()
        if new_seed is None:
            new_seed = not self.repeat_seed.get()
        if new_seed:
            prior_seed = self.var["seed"].get()
            seed = str(random.SystemRandom().randrange(1, 2**31))
            while seed == prior_seed:
                seed = str(random.SystemRandom().randrange(1, 2**31))
            self.var["seed"].set(seed)
        try:
            config = self.configuration()
            keep = [role for role, flag in self.keep.items() if flag.get()]
            if keep and self.result is None:
                raise ValueError("Generate your first idea before keeping parts.")
        except ValueError as error:
            messagebox.showerror("Check the settings", str(error), parent=self.root)
            return
        self.busy = True
        for button in (self.generate_button, self.variation_button, self.play_button, self.drag_button):
            button.configure(state="disabled")
        prior = self.result["arrangement"] if self.result else None
        def worker():
            try:
                result = build_desktop_session(config, previous=prior, keep_roles=keep,
                                              status=lambda text: self.events.put(("status", text)))
                self.events.put(("done", result))
            except Exception as error:
                self.events.put(("error", f"{type(error).__name__}: {error}"))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.set(payload)
                    continue
                self.busy = False
                self.generate_button.configure(state="normal")
                self.variation_button.configure(state="normal")
                if kind == "error":
                    self.status.set("Could not finish this action. " + payload)
                    messagebox.showerror("Could not generate", payload, parent=self.root)
                elif kind == "preview_done":
                    self.result["preview"] = payload
                    self.status.set("Playing the simple synth preview. Your FL Studio instruments will sound different.")
                    self.play()
                else:
                    self.result = payload
                    arrangement = payload["arrangement"]
                    self.chords.set("  →  ".join(p["symbol"] for p in arrangement["progression"]))
                    notes = sum(len(t["notes"]) for t in arrangement["tracks"])
                    self.status.set(f"Saved to Desktop: {Path(payload['project']).name}")
                    self.folder_button.configure(state="normal")
                    self.project_button.configure(state="normal")
                    self.draw()
                if self.result:
                    self.play_button.configure(state="normal")
                    self.drag_button.configure(state="normal")
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def draw(self):
        self.canvas.delete("all")
        width, height = max(self.canvas.winfo_width(), 850), max(self.canvas.winfo_height(), 240)
        left, top = 105, 25
        row_height = (height - top - 10) / 9
        usable = width - left - 16
        tracks = {t["role"]: t for t in self.result["arrangement"]["tracks"]} if self.result else {}
        for bar in range(9):
            x = left + usable * bar / 8
            self.canvas.create_line(x, top - 3, x, height - 8, fill=COLORS["line"])
            if bar < 8:
                self.canvas.create_text(x + 5, 12, anchor="w", text=f"{bar+1}", fill=COLORS["muted"], font=("Segoe UI", 9))
        for index, (role, label) in enumerate(ROLE_LABELS.items()):
            y = top + index * row_height
            color = PALETTE[index]
            self.canvas.create_text(10, y + row_height/2, anchor="w", text=label, fill=color, font=("Segoe UI", 9))
            notes = tracks.get(role, {}).get("notes", [])
            pitches = [n["pitch"] for n in notes] or [60]
            low, high = min(pitches), max(pitches)
            for note in notes:
                x1 = left + usable * note["start"] / 32
                x2 = left + usable * (note["start"] + note["duration"]) / 32
                fraction = (note["pitch"]-low) / max(1, high-low)
                note_y = y + 4 + (1-fraction) * max(1, row_height-11)
                self.canvas.create_rectangle(x1, note_y, max(x1+2, x2), note_y+3, fill=color, outline="")
        if not self.result:
            self.canvas.create_text(left + usable/2, height/2, text="Your eight-bar arrangement will appear here", fill=COLORS["muted"], font=("Segoe UI", 13))

    def play(self):
        if self.busy or not self.result:
            return
        if self.result.get("preview"):
            try:
                import winsound
                winsound.PlaySound(self.result["preview"], winsound.SND_FILENAME | winsound.SND_ASYNC)
            except (ImportError, RuntimeError) as error:
                messagebox.showerror("Preview", str(error), parent=self.root)
            return
        target = Path(self.result["folder"]) / "preview.wav"
        if target.is_file():
            self.result["preview"] = str(target)
            self.play()
            return
        self.busy = True
        for button in (self.generate_button, self.variation_button, self.play_button, self.drag_button):
            button.configure(state="disabled")
        self.status.set("Rendering the simple sound preview. The MIDI / project is already saved on your Desktop.")
        arrangement = self.result["arrangement"]
        def render():
            try:
                from drum_synth import render_preview
                render_preview(arrangement, target.parent / "kit", target)
                self.events.put(("preview_done", str(target)))
            except Exception as error:
                self.events.put(("error", f"{type(error).__name__}: {error}"))
        threading.Thread(target=render, daemon=True).start()

    def stop(self):
        try:
            import winsound
            winsound.PlaySound(None, 0)
        except (ImportError, RuntimeError):
            pass

    def drag_press(self, event):
        if not self.busy and self.result:
            self.drag_origin = (event.x_root, event.y_root)
            self.drag_button.state(["pressed"])
        return "break"

    def drag_release(self, event=None):
        self.drag_origin = None
        self.drag_button.state(["!pressed"])
        return "break"

    def drag_motion(self, event):
        if self.busy or not self.result or self.drag_origin is None:
            return "break"
        x, y = self.drag_origin
        if max(abs(event.x_root - x), abs(event.y_root - y)) < 6:
            return "break"
        self.drag_origin = None
        self.busy = True
        for button in (self.generate_button, self.variation_button, self.play_button, self.drag_button):
            button.configure(state="disabled")
        try:
            path = Path(self.result["midi"])
            self.status.set("Drop the MIDI into your DAW or folder. Press Escape to cancel.")
            grabbed = self.root.grab_current()
            if grabbed is not None:
                grabbed.grab_release()
            self.root.update_idletasks()
            dropped = drag_file(path)
            self.status.set("MIDI drop completed: " + path.name if dropped else "MIDI drag cancelled. Your saved idea is still ready.")
        except Exception as error:
            self.status.set("Could not drag MIDI. Open files to access the saved MIDI.")
            messagebox.showerror("MIDI drag", str(error), parent=self.root)
        finally:
            self.busy = False
            self.drag_release()
            for button in (self.generate_button, self.variation_button, self.play_button, self.drag_button):
                button.configure(state="normal")
        return "break"

    def open_folder(self):
        if self.result:
            os.startfile(self.result["folder"])

    def open_project(self):
        if self.result and self.result.get("project"):
            os.startfile(self.result["project"])

    def close(self):
        if self.busy:
            self.status.set("Finishing the current action. You can close the window when it is ready.")
            return
        self.stop()
        self.root.destroy()


if __name__ == "__main__":
    root = create_root()
    App(root)
    root.mainloop()
