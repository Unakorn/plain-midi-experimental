"""Plain MIDI: a small desktop front end for the editable generator."""
from __future__ import annotations

import json
import os
from pathlib import Path
import queue
import random
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from workflow import APP_DIR, GENRE_LABELS, ROLE_LABELS, build_desktop_session, desktop_directory
from generator import GENRES, midi_bytes, validate_arrangement
from evolution import evolve_song, reorder_song, shuffle_song, validate_song
from file_drag import drag_file

COLORS = {"bg": "#13171c", "panel": "#1d242c", "text": "#eef2f6",
          "muted": "#9baab9", "accent": "#7ce0c2", "line": "#35414e"}
PALETTE = ["#f3b774", "#729cda", "#e8c875", "#8fcca9", "#ad91e3", "#ed90a5", "#69b7cb", "#5c97a8", "#77848e"]


def create_root():
    return tk.Tk()


class App:
    def __init__(self, root):
        self.root = root
        self.root.title("Plain MIDI — from idea to song")
        self.root.geometry("1140x960")
        self.root.minsize(1080, 900)
        self.root.configure(bg=COLORS["bg"])
        self.events = queue.Queue()
        self.busy = False
        self.result = None
        self.drag_origin = None
        self.repeat_seed = tk.BooleanVar(value=False)
        self.evolution_amount = tk.DoubleVar(value=.5)
        self.order_vars = [tk.StringVar(value=letter) for letter in "ABCD"]
        self.header_text = tk.StringVar(value="8 BARS  /  9 PARTS  /  32 DRUMS")
        self.var = {
            "genre": tk.StringVar(value="House"), "key": tk.StringVar(value="A"),
            "mode": tk.StringVar(value="minor"), "bpm": tk.StringVar(value=str(GENRES["house"]["bpm"])),
            "progression": tk.StringVar(value=GENRES["house"]["progression"]), "seed": tk.StringVar(value="42"),
            "density": tk.DoubleVar(value=.5), "swing": tk.DoubleVar(value=GENRES["house"]["swing"]),
            "variation": tk.DoubleVar(value=.3), "chord_style": tk.StringVar(value="block"),
            "auto_chords": tk.BooleanVar(value=True), "chord_creativity": tk.DoubleVar(value=.5),
            "lead_style": tk.StringVar(value="lead"), "drum_variants": tk.BooleanVar(value=True),
        }
        self.keep = {role: tk.BooleanVar(value=False) for role in ROLE_LABELS}
        self.last_genre = "house"
        self.status = tk.StringVar(value="Choose a genre and generate. All nine parts arrive together.")
        self.chords = tk.StringVar(value="One progression. Different jobs for each instrument.")
        self.harmony_hint = tk.StringVar()
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
        ttk.Label(header, textvariable=self.header_text, foreground=COLORS["accent"]).pack(side="right", pady=12)
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
        ttk.Checkbutton(row, text="Generate chords", variable=self.var["auto_chords"]).pack(side="left", padx=(0, 12))
        ttk.Label(row, text="Manual sequence", style="Small.TLabel").pack(side="left", padx=(0, 8))
        self.progression_entry = ttk.Entry(row, textvariable=self.var["progression"], width=32)
        self.progression_entry.pack(side="left", fill="x", expand=True, padx=(0, 12))
        ttk.Label(row, text="Seed", style="Small.TLabel").pack(side="left", padx=(8, 6))
        ttk.Entry(row, textvariable=self.var["seed"], width=10).pack(side="left")
        ttk.Checkbutton(row, text="Repeat seed", variable=self.repeat_seed).pack(side="left", padx=(10, 0))
        knobs = ttk.Frame(outer)
        knobs.pack(fill="x", pady=(16, 8))
        for i, (label, key, maximum) in enumerate([("Chord creativity", "chord_creativity", 1), ("Density", "density", 1), ("Swing", "swing", .6), ("Phrase variation", "variation", 1)]):
            knobs.columnconfigure(i, weight=1)
            panel = ttk.Frame(knobs)
            panel.grid(row=0, column=i, sticky="ew", padx=(0, 18))
            number = tk.StringVar()
            def refresh(*args, k=key, n=number, l=label):
                n.set(f"{l}   {self.var[k].get():.0%}")
            self.var[key].trace_add("write", refresh)
            refresh()
            ttk.Label(panel, textvariable=number, style="Small.TLabel").pack(anchor="w")
            slider = ttk.Scale(panel, from_=.1 if key == "density" else 0, to=maximum, variable=self.var[key])
            slider.pack(fill="x")
            if key == "chord_creativity":
                self.chord_slider = slider
        ttk.Label(outer, textvariable=self.harmony_hint, style="Small.TLabel", wraplength=1000).pack(anchor="w", pady=(0, 7))
        self.var["auto_chords"].trace_add("write", self.refresh_harmony_controls)
        self.var["chord_creativity"].trace_add("write", self.refresh_harmony_controls)
        for flag in self.keep.values():
            flag.trace_add("write", self.refresh_harmony_controls)
        self.refresh_harmony_controls()
        ttk.Checkbutton(outer, text="Use the other drum sounds for accents and fills", variable=self.var["drum_variants"]).pack(anchor="w")
        self.chord_label = ttk.Label(outer, textvariable=self.chords, foreground=COLORS["accent"], font=("Segoe UI", 11, "bold"), wraplength=1000)
        self.chord_label.pack(anchor="w", fill="x", pady=(12, 8))
        outer.bind("<Configure>", lambda event: self.chord_label.configure(wraplength=max(800, event.width-48)))
        self.canvas = tk.Canvas(outer, height=240, bg=COLORS["panel"], highlightthickness=1, highlightbackground=COLORS["line"])
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda event: self.draw())
        keep_row = ttk.Frame(outer)
        keep_row.pack(fill="x", pady=(12, 8))
        ttk.Label(keep_row, text="Keep parts:", style="Small.TLabel").pack(side="left", padx=(0, 8))
        for role, label in ROLE_LABELS.items():
            if role == "NOISE":
                continue
            ttk.Checkbutton(keep_row, text=label, variable=self.keep[role]).pack(side="left", padx=3)
        song_row = ttk.Frame(outer)
        song_row.pack(fill="x", pady=(6, 4))
        self.evolve_button = ttk.Button(song_row, text="Evolve to 32 bars", command=self.evolve, state="disabled")
        self.evolve_button.pack(side="left")
        evolution_panel = ttk.Frame(song_row)
        evolution_panel.pack(side="left", padx=12)
        evolution_label = tk.StringVar()
        def update_evolution(*args):
            evolution_label.set(f"Evolution   {self.evolution_amount.get():.0%}")
        self.evolution_amount.trace_add("write", update_evolution)
        update_evolution()
        ttk.Label(evolution_panel, textvariable=evolution_label, style="Small.TLabel").pack(anchor="w")
        ttk.Scale(evolution_panel, from_=0, to=1, variable=self.evolution_amount, length=155).pack()
        self.shuffle_button = ttk.Button(song_row, text="Shuffle sections", command=self.shuffle, state="disabled")
        self.shuffle_button.pack(side="left", padx=(0, 12))
        ttk.Label(song_row, text="Order", style="Small.TLabel").pack(side="left", padx=(0, 5))
        self.order_boxes = []
        for value in self.order_vars:
            box = ttk.Combobox(song_row, textvariable=value, values=list("ABCD"), width=2, state="disabled")
            box.pack(side="left", padx=2)
            self.order_boxes.append(box)
        self.order_button = ttk.Button(song_row, text="Apply order", command=self.apply_order, state="disabled")
        self.order_button.pack(side="left", padx=(8, 0))
        ttk.Label(outer, text="Evolve uses your saved theme. Higher Evolution changes harmony and phrases more; kept melodic parts keep the harmony.", style="Small.TLabel").pack(anchor="w", pady=(0, 4))
        actions = ttk.Frame(outer)
        actions.pack(fill="x", pady=(6, 10))
        self.generate_button = ttk.Button(actions, text="Generate 8 bars", command=self.generate, style="Accent.TButton")
        self.generate_button.pack(side="left")
        self.variation_button = ttk.Button(actions, text="New 8-bar idea", command=lambda: self.generate(new_seed=True))
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
        self.load_button = ttk.Button(export_row, text="Open saved idea", command=self.open_saved)
        self.load_button.pack(side="right")
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
        self.refresh_actions()
        self.draw()

    def change_genre(self, event=None):
        key = next(k for k, v in GENRE_LABELS.items() if v == self.var["genre"].get())
        self.var["bpm"].set(str(GENRES[key]["bpm"]))
        self.var["swing"].set(GENRES[key]["swing"])
        if self.var["progression"].get() == GENRES[self.last_genre]["progression"]:
            self.var["progression"].set(GENRES[key]["progression"])
        self.last_genre = key

    def refresh_harmony_controls(self, *args):
        automatic = self.var["auto_chords"].get()
        self.progression_entry.configure(state="disabled" if automatic else "normal")
        self.chord_slider.configure(state="normal" if automatic else "disabled")
        if not automatic:
            hint = "Manual: enter up to 32 chords using scale degrees 1–7 or names, e.g. 1 6 3 7 or Am F C G. They fill eight bars."
        elif any(flag.get() for role, flag in self.keep.items() if role not in ("DRUMS", "NOISE")):
            hint = "Kept melodic parts also keep their harmony. Uncheck them to explore new chords or creativity settings."
        elif self.var["chord_creativity"].get() < .3:
            hint = "Simple harmony: a fresh progression on each generation. Raise Chord creativity for more changes and color."
        elif self.var["chord_creativity"].get() < .55:
            hint = "Richer harmony: more chord changes, sevenths and ninths, with connected voicings across eight bars."
        else:
            hint = "Adventurous harmony: up to 32 changes, borrowed chords and shared-note overlaps. Some notes can leave the base scale."
        self.harmony_hint.set(hint)

    def configuration(self):
        config = {k: value.get() for k, value in self.var.items()}
        config["genre"] = next(k for k, v in GENRE_LABELS.items() if v == config["genre"])
        config["bpm"] = float(config["bpm"])
        config["seed"] = int(config["seed"])
        return config

    def source_theme(self):
        if not self.result:
            return None
        arrangement = self.result["arrangement"]
        return arrangement["section_bank"]["A"]["arrangement"] if arrangement["bars"] == 32 else arrangement

    def next_seed(self, force=False):
        if force or not self.repeat_seed.get():
            old = self.var["seed"].get()
            seed = str(random.SystemRandom().randrange(1, 2**31))
            while seed == old:
                seed = str(random.SystemRandom().randrange(1, 2**31))
            self.var["seed"].set(seed)
        return int(self.var["seed"].get())

    def refresh_actions(self):
        ready = self.result is not None and not self.busy
        song = ready and self.result["arrangement"]["bars"] == 32
        for button in (self.generate_button, self.variation_button, self.load_button):
            button.configure(state="disabled" if self.busy else "normal")
        for button in (self.folder_button, self.evolve_button):
            button.configure(state="normal" if ready else "disabled")
        can_preview = ready and self.result.get("media_matches", True) and (self.result.get("preview") or (Path(self.result["folder"]) / "kit" / "kit.json").is_file())
        self.play_button.configure(state="normal" if can_preview else "disabled")
        self.project_button.configure(state="normal" if ready and self.result.get("project") else "disabled")
        self.drag_button.configure(state="normal" if ready and self.result.get("midi") else "disabled")
        for button in (self.shuffle_button, self.order_button):
            button.configure(state="normal" if song else "disabled")
        for box in self.order_boxes:
            box.configure(state="readonly" if song else "disabled")
        self.evolve_button.configure(text="Re-evolve song" if song else "Evolve to 32 bars")

    def run_job(self, job):
        self.stop()
        self.busy = True
        self.refresh_actions()
        self.status.set("Building your idea…")
        def worker():
            try:
                self.events.put(("done", job()))
            except Exception as error:
                self.events.put(("error", f"{type(error).__name__}: {error}"))
        threading.Thread(target=worker, daemon=True).start()

    def generate(self, new_seed=None):
        if self.busy:
            return
        try:
            if new_seed is not False:
                self.next_seed(force=new_seed is True)
            config = self.configuration()
            keep = [role for role, flag in self.keep.items() if flag.get()]
            if keep and self.result is None:
                raise ValueError("Generate your first idea before keeping parts.")
        except ValueError as error:
            messagebox.showerror("Check the settings", str(error), parent=self.root)
            return
        prior = self.source_theme()
        self.run_job(lambda: build_desktop_session(config, previous=prior, keep_roles=keep,
                        status=lambda text: self.events.put(("status", text))))

    def publish_song(self, factory):
        def job():
            song = factory()
            return build_desktop_session(song["config"], prepared_arrangement=song,
                            status=lambda text: self.events.put(("status", text)))
        self.run_job(job)

    def evolve(self):
        if self.busy or not self.result:
            return
        try:
            seed = self.next_seed()
            amount = self.evolution_amount.get()
            source = self.source_theme()
            keep = [role for role, flag in self.keep.items() if flag.get()]
        except ValueError as error:
            messagebox.showerror("Check the seed", str(error), parent=self.root)
            return
        self.publish_song(lambda: evolve_song(source, seed=seed, amount=amount, keep_roles=keep))

    def shuffle(self):
        if self.busy or not self.result or self.result["arrangement"]["bars"] != 32:
            return
        # A shuffle always changes the order, including with Repeat seed enabled.
        seed = random.SystemRandom().randrange(1, 2**31)
        song = self.result["arrangement"]
        self.publish_song(lambda: shuffle_song(song, seed=seed))

    def apply_order(self):
        if self.busy or not self.result or self.result["arrangement"]["bars"] != 32:
            return
        order = [value.get() for value in self.order_vars]
        if sorted(order) != list("ABCD"):
            messagebox.showerror("Section order", "Use A, B, C and D once each.", parent=self.root)
            return
        song = self.result["arrangement"]
        if order == song["section_order"]:
            self.status.set("That order is already saved. Choose another order or Shuffle sections.")
            return
        self.publish_song(lambda: reorder_song(song, order))

    def open_saved(self, filename=None):
        if self.busy:
            return
        filename = filename or filedialog.askopenfilename(parent=self.root, title="Open a saved Plain MIDI idea",
                        initialdir=desktop_directory() / "Plain MIDI Files",
                        filetypes=[("Plain MIDI arrangement", "*.json")])
        if not filename:
            return
        try:
            path = Path(filename).resolve()
            arrangement = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(arrangement, dict):
                raise ValueError("Choose an arrangement.json saved by Plain MIDI.")
            validate_arrangement(arrangement)
            if arrangement["bars"] == 32:
                validate_song(arrangement)
            from generator import validate_config
            config = validate_config(arrangement["config"])
            folder = path.parent
            midi = next((p for p in (folder / (folder.name + ".mid"), folder / "arrangement.mid") if p.is_file()), None)
            media_matches = midi is not None and midi.read_bytes() == midi_bytes(arrangement)
            flp = folder / "Automation Ready.flp"
            project = flp if flp.is_file() else midi
            result = {"arrangement": arrangement, "folder": str(folder), "midi": str(midi) if midi else None,
                      "flp": str(flp) if flp.is_file() else None,
                      "project": str(project) if project else None,
                      "preview": str(folder / "preview.wav") if (folder / "preview.wav").is_file() else None,
                      "media_matches": media_matches}
            if not media_matches:
                result.update(midi=None, flp=None, project=None, preview=None)
        except (OSError, ValueError, KeyError, TypeError, AttributeError) as error:
            messagebox.showerror("Could not open idea", str(error), parent=self.root)
            return
        self.stop()
        for flag in self.keep.values():
            flag.set(False)
        for key, variable in self.var.items():
            value = config[key]
            variable.set(GENRE_LABELS[value] if key == "genre" else value)
        self.last_genre = config["genre"]
        self.var["seed"].set(str(arrangement["seed"]))
        self.evolution_amount.set(arrangement.get("evolution", {}).get("amount", .5))
        self.show_result(result)
        self.status.set(f"Opened {arrangement['bars']}-bar idea: {folder.name}")
        if not media_matches:
            self.status.set("Loaded JSON notes. Matching exported MIDI was not found; evolve or rearrange to save matching files.")

    def show_result(self, result):
        result.setdefault("project", result.get("flp") or result.get("midi"))
        self.result = result
        arrangement = result["arrangement"]
        bars = arrangement["bars"]
        self.header_text.set(f"{bars} BARS  /  9 PARTS  /  32 DRUMS")
        if bars == 32:
            self.chords.set(f"{len(arrangement['progression'])} chord changes / 32 bars   ·   " +
                    "  →  ".join(f"{letter} {arrangement['section_bank'][letter]['label']}" for letter in arrangement["section_order"]))
            for variable, letter in zip(self.order_vars, arrangement["section_order"]):
                variable.set(letter)
        else:
            self.chords.set(f"{len(arrangement['progression'])} chords / 8 bars   ·   " +
                    "  →  ".join(p["symbol"] for p in arrangement["progression"]))
        self.refresh_actions()
        self.draw()

    def poll(self):
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "status":
                    self.status.set(payload)
                    continue
                self.busy = False
                if kind == "error":
                    self.status.set("Could not finish this action. " + payload)
                    messagebox.showerror("Could not finish", payload, parent=self.root)
                elif kind == "preview_done":
                    self.result["preview"] = payload
                    self.status.set("Playing the simple synth preview. Your FL Studio instruments will sound different.")
                    self.play()
                else:
                    self.show_result(payload)
                    self.status.set(f"Saved to Desktop: {Path(payload['project']).name}")
                self.refresh_actions()
        except queue.Empty:
            pass
        self.root.after(100, self.poll)

    def draw(self):
        self.canvas.delete("all")
        width, height = max(self.canvas.winfo_width(), 850), max(self.canvas.winfo_height(), 190)
        arrangement = self.result["arrangement"] if self.result else None
        bars = arrangement["bars"] if arrangement else 8
        beats = bars * 4
        left, top = 105, 47 if bars == 32 else 25
        row_height = (height - top - 10) / 9
        usable = width - left - 16
        tracks = {t["role"]: t for t in arrangement["tracks"]} if arrangement else {}
        for bar in range(bars + 1):
            x = left + usable * bar / bars
            self.canvas.create_line(x, top - 3, x, height - 8,
                    fill="#698779" if bars == 32 and bar % 8 == 0 else COLORS["line"])
            if bar < bars and (bars == 8 or bar % 4 == 0):
                self.canvas.create_text(x + 5, top - 13, anchor="w", text=f"{bar+1}", fill=COLORS["muted"], font=("Segoe UI", 9))
        if bars == 32:
            for index, letter in enumerate(arrangement["section_order"]):
                x = left + usable * index / 4
                color = PALETTE[list("ABCD").index(letter)]
                self.canvas.create_rectangle(x, 1, x + usable/4 - 2, 20, fill="#26353b", outline="")
                self.canvas.create_text(x+6, 10, anchor="w",
                        text=f"{letter}  {arrangement['section_bank'][letter]['label']}  ·  {index*8+1}–{index*8+8}",
                        fill=color, font=("Segoe UI", 9, "bold"))
        elif arrangement:
            for chord in arrangement["progression"]:
                beat = chord["bar"] * 4
                if beat % 4:
                    x = left + usable * beat / beats
                    self.canvas.create_line(x, top, x, height - 8, fill="#354a46", dash=(2, 4))
        for index, (role, label) in enumerate(ROLE_LABELS.items()):
            y = top + index * row_height
            color = PALETTE[index]
            self.canvas.create_text(10, y + row_height/2, anchor="w", text=label, fill=color, font=("Segoe UI", 9))
            notes = tracks.get(role, {}).get("notes", [])
            pitches = [n["pitch"] for n in notes] or [60]
            low, high = min(pitches), max(pitches)
            for note in notes:
                x1 = left + usable * note["start"] / beats
                x2 = left + usable * (note["start"] + note["duration"]) / beats
                fraction = (note["pitch"]-low) / max(1, high-low)
                note_y = y + 4 + (1-fraction) * max(1, row_height-11)
                self.canvas.create_rectangle(x1, note_y, min(left+usable, max(x1+2, x2)), note_y+3, fill=color, outline="")
        if not self.result:
            self.canvas.create_text(left + usable/2, height/2, text="Generate eight bars, then evolve your idea into a song", fill=COLORS["muted"], font=("Segoe UI", 13))

    def play(self):
        if self.busy or not self.result or not self.result.get("media_matches", True):
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
        self.refresh_actions()
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
        self.refresh_actions()
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
            self.refresh_actions()
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
