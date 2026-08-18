"""The control window.

Hidden by default: Aloud lives in the tray and is summoned by a hotkey. The
window is where you pick a voice, shape how it sounds, manage downloads and
rebind the hotkeys.

Tkinter is not thread-safe, so nothing outside the main thread touches a
widget. Background work (hotkeys, playback state, downloads) posts callables
onto a queue that `_pump` drains on the Tk event loop.
"""

from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Dict, List, Optional

from . import voices as voice_catalog
from . import winapi
from .config import LIMITS
from .engines import get_engine
from .voices import CatalogVoice, DownloadCancelled

_LOGGER = logging.getLogger(__name__)

_FONT = ("Segoe UI", 10)
_FONT_SMALL = ("Segoe UI", 9)
_FONT_HEADING = ("Segoe UI", 11, "bold")

# label, settings attribute, step, display format, explanation. The hint is
# only worth a row where the control is not self-explanatory.
_SLIDERS = [
    ("Speed", "speed", 0.05, "{:.2f}x", ""),
    ("Pitch", "pitch", 0.01, "{:.2f}x", "Higher or lower voice, at the same speed."),
    ("Volume", "volume", 0.05, "{:.0%}", ""),
    ("Expression", "expressiveness", 0.01, "{:.2f}",
     "Variation in pitch and energy. Low is flat and newsreaderly, high is animated."),
    ("Cadence", "cadence", 0.01, "{:.2f}",
     "Variation in timing. Low is metronomic, high has a looser, natural rhythm."),
    ("Sentence pause", "sentence_pause", 0.05, "{:.2f}s", ""),
]

_PLACEHOLDER = (
    "Highlight text anywhere and press your Speak hotkey, or type here and "
    "press Speak.")


class MainWindow:
    def __init__(self, app) -> None:
        self.app = app
        self._queue: "queue.Queue[Callable[[], None]]" = queue.Queue()
        self._updating = False          # guards against feedback loops
        self._download_cancel: Optional[threading.Event] = None
        self._catalog: List[CatalogVoice] = []
        self._visible = False

        self.root = tk.Tk()
        self.root.title("Aloud")
        self.root.geometry("740x700")
        self.root.minsize(640, 600)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)

        self._init_style()
        self._build()
        self._load_settings_into_widgets()
        self.refresh_voice_list()

        self.root.withdraw()
        self.root.after(50, self._pump)

    # -- thread-safe entry points -------------------------------------------

    def post(self, callback: Callable[[], None]) -> None:
        """Run `callback` on the UI thread. Safe to call from any thread."""
        self._queue.put(callback)

    def _pump(self) -> None:
        while True:
            try:
                callback = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                _LOGGER.exception("A queued UI action failed")
        self.root.after(50, self._pump)

    # -- window visibility ---------------------------------------------------

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self._visible = True
        try:
            # Tk's own lift() is not enough when another app owns the
            # foreground, which is exactly the case when a hotkey summons us.
            winapi.force_foreground(self._hwnd())
        except winapi.WindowsOnlyError:
            self.root.focus_force()
        except Exception:
            _LOGGER.debug("Could not force the window to the foreground", exc_info=True)
        self.text.focus_set()

    def _hwnd(self) -> int:
        """The window's top-level handle, which is what Win32 calls expect.

        `winfo_id` returns Tk's inner window; SetForegroundWindow only accepts
        a top-level, which is the frame Tk reports through `wm_frame`.
        """
        try:
            frame = self.root.wm_frame()
            return int(frame, 16) if frame.lower().startswith("0x") else int(frame)
        except (ValueError, tk.TclError):
            return self.root.winfo_id()

    def hide(self) -> None:
        self._visible = False
        self.root.withdraw()

    def toggle(self) -> None:
        if self._visible and self.root.state() != "withdrawn":
            self.hide()
        else:
            self.show()

    # -- construction --------------------------------------------------------

    def _init_style(self) -> None:
        style = ttk.Style()
        if "vista" in style.theme_names():
            style.theme_use("vista")
        style.configure("TLabel", font=_FONT)
        style.configure("TButton", font=_FONT)
        style.configure("TCheckbutton", font=_FONT)
        style.configure("Heading.TLabel", font=_FONT_HEADING)
        style.configure("Hint.TLabel", font=_FONT_SMALL, foreground="#666666")
        style.configure("Status.TLabel", font=_FONT_SMALL)

    def _build(self) -> None:
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        self.speak_tab = ttk.Frame(notebook, padding=10)
        self.voices_tab = ttk.Frame(notebook, padding=10)
        self.settings_tab = ttk.Frame(notebook, padding=10)
        notebook.add(self.speak_tab, text="  Speak  ")
        notebook.add(self.voices_tab, text="  Voices  ")
        notebook.add(self.settings_tab, text="  Settings  ")

        self._build_speak_tab()
        self._build_voices_tab()
        self._build_settings_tab()

        self.status_var = tk.StringVar(value="Ready")
        status = ttk.Label(self.root, textvariable=self.status_var,
                           style="Status.TLabel", anchor="w")
        status.pack(fill="x", padx=12, pady=(0, 8))

    # -- Speak tab -----------------------------------------------------------

    def _build_speak_tab(self) -> None:
        parent = self.speak_tab

        text_row = ttk.Frame(parent)
        text_row.pack(side="top", fill="both", expand=True)
        self.text = tk.Text(text_row, height=6, wrap="word", font=_FONT,
                            relief="solid", borderwidth=1, padx=8, pady=6)
        scrollbar = ttk.Scrollbar(text_row, command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        # The scrollbar goes down first so the text does not claim its width.
        scrollbar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        # Remember the theme's own text colour so the placeholder grey can be
        # undone later; passing an empty colour back is not valid Tk.
        self._text_foreground = self.text.cget("foreground")
        self.text.insert("1.0", _PLACEHOLDER)
        self.text.configure(foreground="#888888")
        self.text.bind("<FocusIn>", self._clear_placeholder)
        # Ctrl+Enter speaks, matching the "send" convention of chat apps.
        self.text.bind("<Control-Return>", lambda event: (self.speak_text(), "break")[1])

        transport = ttk.Frame(parent)
        transport.pack(fill="x", pady=(8, 4))
        ttk.Button(transport, text="Speak", command=self.speak_text).pack(side="left")
        self.pause_button = ttk.Button(transport, text="Pause",
                                       command=self.app.toggle_pause)
        self.pause_button.pack(side="left", padx=4)
        ttk.Button(transport, text="Stop", command=self.app.stop).pack(side="left")
        ttk.Button(transport, text="Read selection",
                   command=self.app.speak_selection).pack(side="left", padx=(12, 0))
        ttk.Button(transport, text="Save as WAV...",
                   command=self.save_wav).pack(side="right")

        ttk.Separator(parent).pack(fill="x", pady=8)
        self._build_voice_row(parent)
        ttk.Separator(parent).pack(fill="x", pady=8)
        self._build_sliders(parent)
        self._build_presets(parent)

    def _build_voice_row(self, parent) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x")
        row.columnconfigure(1, weight=1)

        ttk.Label(row, text="Engine").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.engine_var = tk.StringVar()
        engine_box = ttk.Combobox(row, textvariable=self.engine_var, state="readonly",
                                  width=28, font=_FONT)
        engine_box["values"] = ["Piper (downloadable neural voices)",
                                "Windows built-in voices"]
        engine_box.grid(row=0, column=1, sticky="ew", pady=2)
        engine_box.bind("<<ComboboxSelected>>", self._on_engine_change)

        ttk.Label(row, text="Voice").grid(row=1, column=0, sticky="w", padx=(0, 8))
        self.voice_var = tk.StringVar()
        self.voice_box = ttk.Combobox(row, textvariable=self.voice_var,
                                      state="readonly", font=_FONT)
        self.voice_box.grid(row=1, column=1, sticky="ew", pady=2)
        self.voice_box.bind("<<ComboboxSelected>>", self._on_voice_change)

        self.speaker_row = ttk.Frame(row)
        self.speaker_row.grid(row=2, column=1, sticky="ew", pady=2)
        self.speaker_label = ttk.Label(row, text="Speaker")
        self.speaker_var = tk.IntVar(value=0)
        self.speaker_spin = ttk.Spinbox(self.speaker_row, from_=0, to=0, width=6,
                                        textvariable=self.speaker_var, font=_FONT,
                                        command=self._on_speaker_change)
        self.speaker_spin.pack(side="left")
        self.speaker_name = ttk.Label(self.speaker_row, text="", style="Hint.TLabel")
        self.speaker_name.pack(side="left", padx=8)
        self.speaker_spin.bind("<Return>", lambda event: self._on_speaker_change())

    def _build_sliders(self, parent) -> None:
        frame = ttk.Frame(parent)
        frame.pack(fill="x")
        # The scale column takes the slack, and never collapses to nothing
        # however long the labels around it get.
        frame.columnconfigure(1, weight=1, minsize=220)

        self.slider_vars: Dict[str, tk.DoubleVar] = {}
        self.slider_labels: Dict[str, ttk.Label] = {}

        row = 0
        for label, attribute, resolution, fmt, hint in _SLIDERS:
            low, high = LIMITS[attribute]
            variable = tk.DoubleVar(value=getattr(self.app.settings, attribute))
            self.slider_vars[attribute] = variable

            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w",
                                              padx=(0, 10), pady=(3, 0))
            scale = ttk.Scale(frame, from_=low, to=high, variable=variable,
                              orient="horizontal",
                              command=lambda value, a=attribute, r=resolution,
                              f=fmt: self._on_slider(a, value, r, f))
            scale.grid(row=row, column=1, sticky="ew", pady=(3, 0))
            # Double-click a slider to put it back to its default.
            scale.bind("<Double-Button-1>",
                       lambda event, a=attribute: self._reset_slider(a))

            value_label = ttk.Label(frame, width=7, anchor="e")
            value_label.grid(row=row, column=2, sticky="e", padx=(10, 0))
            self.slider_labels[attribute] = value_label
            row += 1

            if hint:
                # Hints go under their slider rather than beside it, so they
                # cannot squeeze the scale out of the row.
                ttk.Label(frame, text=hint, style="Hint.TLabel").grid(
                    row=row, column=1, columnspan=2, sticky="w", pady=(0, 2))
                row += 1

    def _build_presets(self, parent) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(10, 0))

        ttk.Label(row, text="Preset").pack(side="left", padx=(0, 8))
        self.preset_var = tk.StringVar()
        self.preset_box = ttk.Combobox(row, textvariable=self.preset_var,
                                       state="readonly", width=24, font=_FONT)
        self.preset_box.pack(side="left")
        self.preset_box.bind("<<ComboboxSelected>>", self._on_preset_selected)
        ttk.Button(row, text="Save current...", command=self.save_preset).pack(
            side="left", padx=4)
        ttk.Button(row, text="Delete", command=self.delete_preset).pack(side="left")
        self._refresh_presets()

    # -- Voices tab ----------------------------------------------------------

    def _build_voices_tab(self) -> None:
        parent = self.voices_tab

        ttk.Label(parent, text="Piper voices", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(parent, style="Hint.TLabel", wraplength=660, justify="left",
                  text=("Each voice is a separate download that then works entirely "
                        "offline. Voices with more than one speaker hold a range of "
                        "accents in a single model - install one and use the Speaker "
                        "control on the Speak tab to move between them.")
                  ).pack(anchor="w", pady=(2, 8))

        tree_row = ttk.Frame(parent)
        tree_row.pack(side="top", fill="both", expand=True)

        columns = ("voice", "language", "quality", "speakers", "size", "status")
        self.voice_tree = ttk.Treeview(tree_row, columns=columns, show="headings",
                                       height=12, selectmode="browse")
        headings = {"voice": ("Voice", 255), "language": ("Language", 115),
                    "quality": ("Quality", 70), "speakers": ("Speakers", 70),
                    "size": ("Size", 70), "status": ("Status", 90)}
        for key, (title, width) in headings.items():
            self.voice_tree.heading(key, text=title)
            self.voice_tree.column(key, width=width,
                                   anchor="w" if key in ("voice", "language") else "center")

        tree_scroll = ttk.Scrollbar(tree_row, command=self.voice_tree.yview)
        self.voice_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.voice_tree.pack(side="left", fill="both", expand=True)
        self.voice_tree.bind("<<TreeviewSelect>>", self._on_catalog_select)
        self.voice_tree.bind("<Double-Button-1>", lambda event: self.download_selected())

        self.voice_note = ttk.Label(parent, style="Hint.TLabel", wraplength=660,
                                    justify="left", text="")
        self.voice_note.pack(anchor="w", pady=(6, 4))

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", pady=(4, 0))
        self.download_button = ttk.Button(buttons, text="Download",
                                          command=self.download_selected)
        self.download_button.pack(side="left")
        ttk.Button(buttons, text="Use this voice",
                   command=self.use_selected_voice).pack(side="left", padx=4)
        ttk.Button(buttons, text="Delete", command=self.delete_selected_voice).pack(
            side="left")
        ttk.Button(buttons, text="Refresh list",
                   command=lambda: self.refresh_catalog(force=True)).pack(side="right")

        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill="x", pady=(8, 0))
        self.progress_label = ttk.Label(parent, style="Hint.TLabel", text="")
        self.progress_label.pack(anchor="w")

    # -- Settings tab --------------------------------------------------------

    def _build_settings_tab(self) -> None:
        parent = self.settings_tab

        ttk.Label(parent, text="Hotkeys", style="Heading.TLabel").pack(anchor="w")
        ttk.Label(parent, style="Hint.TLabel", wraplength=660, justify="left",
                  text=("Type combinations like ctrl+alt+s, ctrl+shift+f9 or "
                        "win+alt+space. Every hotkey needs at least one modifier, "
                        "and works in whatever application you are using.")
                  ).pack(anchor="w", pady=(2, 8))

        grid = ttk.Frame(parent)
        grid.pack(fill="x")
        grid.columnconfigure(1, weight=1)

        self.hotkey_vars: Dict[str, tk.StringVar] = {}
        rows = [("speak", "Speak selection"), ("stop", "Stop"),
                ("pause", "Pause / resume"), ("window", "Show / hide this window")]
        for index, (key, label) in enumerate(rows):
            ttk.Label(grid, text=label).grid(row=index, column=0, sticky="w", pady=3)
            variable = tk.StringVar()
            self.hotkey_vars[key] = variable
            ttk.Entry(grid, textvariable=variable, font=_FONT, width=28).grid(
                row=index, column=1, sticky="w", pady=3)

        ttk.Button(parent, text="Apply hotkeys", command=self.apply_hotkeys).pack(
            anchor="w", pady=(8, 0))
        self.hotkey_status = ttk.Label(parent, style="Hint.TLabel", wraplength=660,
                                       justify="left", text="")
        self.hotkey_status.pack(anchor="w", pady=(4, 0))

        ttk.Separator(parent).pack(fill="x", pady=12)
        ttk.Label(parent, text="Behaviour", style="Heading.TLabel").pack(anchor="w")

        self.restore_var = tk.BooleanVar()
        ttk.Checkbutton(parent, variable=self.restore_var,
                        text="Put my clipboard back after reading a selection",
                        command=self._on_behaviour_change).pack(anchor="w", pady=2)

        self.fallback_var = tk.BooleanVar()
        ttk.Checkbutton(parent, variable=self.fallback_var,
                        text="If nothing is selected, read the clipboard instead",
                        command=self._on_behaviour_change).pack(anchor="w", pady=2)

        self.start_hidden_var = tk.BooleanVar()
        ttk.Checkbutton(parent, variable=self.start_hidden_var,
                        text="Start hidden in the tray",
                        command=self._on_behaviour_change).pack(anchor="w", pady=2)

        ttk.Separator(parent).pack(fill="x", pady=12)
        ttk.Label(parent, text="Aloud runs in the tray. Right-click its icon for "
                              "quick actions or to quit.", style="Hint.TLabel").pack(anchor="w")
        ttk.Button(parent, text="Quit Aloud", command=self.app.quit).pack(
            anchor="w", pady=(8, 0))

    # -- loading settings into widgets --------------------------------------

    def _load_settings_into_widgets(self) -> None:
        self._updating = True
        try:
            settings = self.app.settings
            self.engine_var.set("Piper (downloadable neural voices)"
                                if settings.engine == "piper"
                                else "Windows built-in voices")
            for label, attribute, resolution, fmt, _hint in _SLIDERS:
                value = getattr(settings, attribute)
                self.slider_vars[attribute].set(value)
                self.slider_labels[attribute].configure(text=fmt.format(value))

            config = self.app.config
            self.hotkey_vars["speak"].set(config.hotkey_speak)
            self.hotkey_vars["stop"].set(config.hotkey_stop)
            self.hotkey_vars["pause"].set(config.hotkey_pause)
            self.hotkey_vars["window"].set(config.hotkey_window)
            self.restore_var.set(config.restore_clipboard)
            self.fallback_var.set(config.read_clipboard_if_no_selection)
            self.start_hidden_var.set(config.start_hidden)
        finally:
            self._updating = False

    # -- callbacks: voice selection -----------------------------------------

    def _on_engine_change(self, _event=None) -> None:
        if self._updating:
            return
        self.app.settings.engine = ("piper" if self.engine_var.get().startswith("Piper")
                                    else "sapi")
        self.refresh_voice_list()
        self.app.save_config()

    def refresh_voice_list(self) -> None:
        """Reload the installed-voice dropdown for the current engine."""
        settings = self.app.settings
        try:
            engine = get_engine(settings.engine)
            installed = engine.list_voices()
        except Exception as error:
            _LOGGER.warning("Could not list voices: %s", error)
            installed = []

        self._voice_infos = {info.label: info for info in installed}
        self._updating = True
        try:
            self.voice_box["values"] = [info.label for info in installed]
            current = (settings.voice if settings.engine == "piper"
                       else settings.sapi_voice)
            match = next((info for info in installed if info.id == current), None)
            if match is None and installed:
                match = installed[0]
                self._store_voice_id(match.id)
            self.voice_var.set(match.label if match else "")
        finally:
            self._updating = False

        if not installed:
            self.voice_box["values"] = []
            self.voice_var.set("")
            if settings.engine == "piper":
                self.status("No Piper voices installed yet - open the Voices tab.")
        self._update_speaker_control()

    def _store_voice_id(self, voice_id: str) -> None:
        if self.app.settings.engine == "piper":
            self.app.settings.voice = voice_id
        else:
            self.app.settings.sapi_voice = voice_id

    def _on_voice_change(self, _event=None) -> None:
        if self._updating:
            return
        info = self._voice_infos.get(self.voice_var.get())
        if not info:
            return
        self._store_voice_id(info.id)
        self.app.settings.speaker = 0
        self._update_speaker_control()
        self.app.save_config()
        self.status(f"Voice set to {info.label}")

    def _update_speaker_control(self) -> None:
        """Show the speaker picker only for models that have several."""
        info = getattr(self, "_voice_infos", {}).get(self.voice_var.get())
        if info is None or not info.is_multi_speaker:
            self.speaker_label.grid_remove()
            self.speaker_row.grid_remove()
            return

        count = len(info.speakers)
        self.speaker_label.grid(row=2, column=0, sticky="w", padx=(0, 8))
        self.speaker_row.grid(row=2, column=1, sticky="ew", pady=2)
        self.speaker_spin.configure(to=count - 1)

        self._updating = True
        try:
            index = min(max(0, int(self.app.settings.speaker)), count - 1)
            self.speaker_var.set(index)
            self.speaker_name.configure(
                text=f"{info.speakers[index]}   (0-{count - 1}, {count} voices in this model)")
        finally:
            self._updating = False

    def _on_speaker_change(self) -> None:
        if self._updating:
            return
        try:
            value = int(self.speaker_var.get())
        except (tk.TclError, ValueError):
            return
        self.app.settings.speaker = max(0, value)
        self._update_speaker_control()
        self.app.save_config()

    # -- callbacks: sliders and presets --------------------------------------

    def _on_slider(self, attribute: str, value, resolution: float, fmt: str) -> None:
        # Quantise so the label and the saved value are tidy numbers rather
        # than whatever float the pixel position produced.
        stepped = round(float(value) / resolution) * resolution
        self.slider_labels[attribute].configure(text=fmt.format(stepped))
        if self._updating:
            return
        setattr(self.app.settings, attribute, stepped)
        self.app.settings.normalise()
        self.app.save_config(defer=True)

    def _reset_slider(self, attribute: str) -> None:
        from .config import VoiceSettings

        default = getattr(VoiceSettings(), attribute)
        self.slider_vars[attribute].set(default)
        setattr(self.app.settings, attribute, default)
        for label, name, resolution, fmt, _hint in _SLIDERS:
            if name == attribute:
                self.slider_labels[attribute].configure(text=fmt.format(default))
        self.app.save_config(defer=True)

    def _refresh_presets(self) -> None:
        names = sorted(self.app.config.presets)
        self.preset_box["values"] = names
        if self.preset_var.get() not in names:
            self.preset_var.set("")

    def _on_preset_selected(self, _event=None) -> None:
        name = self.preset_var.get()
        data = self.app.config.presets.get(name)
        if not data:
            return
        self.app.apply_preset(data)
        self._load_settings_into_widgets()
        self.refresh_voice_list()
        self.status(f"Loaded preset '{name}'")

    def save_preset(self) -> None:
        from tkinter import simpledialog

        name = simpledialog.askstring("Save preset", "Name for this preset:",
                                      parent=self.root)
        if not name:
            return
        self.app.config.presets[name.strip()] = self.app.settings.to_dict()
        self.app.save_config()
        self._refresh_presets()
        self.preset_var.set(name.strip())
        self.status(f"Saved preset '{name.strip()}'")

    def delete_preset(self) -> None:
        name = self.preset_var.get()
        if not name or name not in self.app.config.presets:
            return
        del self.app.config.presets[name]
        self.app.save_config()
        self._refresh_presets()
        self.status(f"Deleted preset '{name}'")

    # -- callbacks: speaking -------------------------------------------------

    def _clear_placeholder(self, _event=None) -> None:
        if self.text.get("1.0", "end-1c").strip() == _PLACEHOLDER:
            self.text.delete("1.0", "end")
            self.text.configure(foreground=self._text_foreground)

    def current_text(self) -> str:
        value = self.text.get("1.0", "end-1c")
        return "" if value.strip() == _PLACEHOLDER else value

    def set_text(self, value: str) -> None:
        self.text.delete("1.0", "end")
        self.text.insert("1.0", value)
        self.text.configure(foreground=self._text_foreground)

    def speak_text(self) -> None:
        text = self.current_text()
        if not text.strip():
            self.status("Nothing to read - type something, or highlight text elsewhere.")
            return
        self.app.speak_text(text)

    def save_wav(self) -> None:
        text = self.current_text()
        if not text.strip():
            self.status("Nothing to save - type something first.")
            return
        path = filedialog.asksaveasfilename(
            parent=self.root, defaultextension=".wav",
            filetypes=[("WAV audio", "*.wav")], initialfile="aloud.wav")
        if not path:
            return
        self.status("Rendering...")
        self.app.render_to_file(text, Path(path))

    # -- callbacks: the voices tab ------------------------------------------

    def refresh_catalog(self, force: bool = False) -> None:
        self.status("Fetching the voice list...")

        def work() -> None:
            catalog = voice_catalog.fetch_catalog(force=force)
            self.post(lambda: self._show_catalog(catalog))

        threading.Thread(target=work, name="aloud-catalog", daemon=True).start()

    def _show_catalog(self, catalog: List[CatalogVoice]) -> None:
        self._catalog = catalog
        self.voice_tree.delete(*self.voice_tree.get_children())
        for voice in catalog:
            language = voice.country or voice.language or voice.locale
            self.voice_tree.insert(
                "", "end", iid=voice.id,
                values=(voice.id, language, voice.quality,
                        voice.num_speakers if voice.num_speakers > 1 else "1",
                        f"{voice.size_mb:.0f} MB",
                        "Installed" if voice.installed else ""))
        self.status(f"{len(catalog)} voices available")

    def _selected_catalog_voice(self) -> Optional[CatalogVoice]:
        selection = self.voice_tree.selection()
        if not selection:
            return None
        return next((v for v in self._catalog if v.id == selection[0]), None)

    def _on_catalog_select(self, _event=None) -> None:
        voice = self._selected_catalog_voice()
        if not voice:
            return
        note = voice.note or ""
        if voice.num_speakers > 1 and "Speaker" not in note:
            note = (note + " " if note else "") + (
                f"{voice.num_speakers} speakers in this model - switch between them "
                "with the Speaker control on the Speak tab.")
        self.voice_note.configure(text=note)
        self.download_button.configure(
            text="Re-download" if voice.installed else "Download")

    def download_selected(self) -> None:
        voice = self._selected_catalog_voice()
        if not voice:
            self.status("Pick a voice from the list first.")
            return
        if self._download_cancel is not None:
            self.status("A download is already running.")
            return

        cancel = threading.Event()
        self._download_cancel = cancel
        self.progress.configure(value=0, maximum=100)
        self.progress_label.configure(text=f"Downloading {voice.id}...")
        self.status(f"Downloading {voice.id} ({voice.size_mb:.0f} MB)...")

        def report(done: int, total: int) -> None:
            percent = (done / total * 100) if total else 0
            self.post(lambda: self._update_progress(percent, done, total))

        def work() -> None:
            try:
                voice_catalog.download_voice(voice.id, progress=report, cancel=cancel)
                self.post(lambda: self._download_finished(voice, None))
            except DownloadCancelled:
                self.post(lambda: self._download_finished(voice, "cancelled"))
            except Exception as error:
                message = str(error)
                self.post(lambda: self._download_finished(voice, message))

        threading.Thread(target=work, name="aloud-download", daemon=True).start()

    def _update_progress(self, percent: float, done: int, total: int) -> None:
        self.progress.configure(value=percent)
        self.progress_label.configure(text=f"{done / 1e6:.1f} MB of {total / 1e6:.1f} MB")

    def _download_finished(self, voice: CatalogVoice, error: Optional[str]) -> None:
        self._download_cancel = None
        self.progress.configure(value=0)

        if error == "cancelled":
            self.progress_label.configure(text="Download cancelled")
            self.status("Download cancelled")
            return
        if error:
            self.progress_label.configure(text="")
            self.status(f"Download failed: {error}")
            messagebox.showerror("Download failed", error, parent=self.root)
            return

        self.progress_label.configure(text=f"{voice.id} installed")
        self.voice_tree.set(voice.id, "status", "Installed")
        self.status(f"{voice.id} is ready to use.")

        # A first download is almost certainly meant to be used straight away.
        if self.app.settings.engine == "piper" and not self.app.settings.voice:
            self.app.settings.voice = voice.id
            self.app.save_config()
        self.refresh_voice_list()

    def use_selected_voice(self) -> None:
        voice = self._selected_catalog_voice()
        if not voice:
            return
        if not voice.installed:
            self.status("Download that voice first.")
            return
        self.app.settings.engine = "piper"
        self.app.settings.voice = voice.id
        self.app.settings.speaker = 0
        self.app.save_config()
        self._load_settings_into_widgets()
        self.refresh_voice_list()
        self.status(f"Now using {voice.id}")

    def delete_selected_voice(self) -> None:
        voice = self._selected_catalog_voice()
        if not voice or not voice.installed:
            return
        if not messagebox.askyesno("Delete voice",
                                   f"Remove {voice.id} from this computer?",
                                   parent=self.root):
            return
        voice_catalog.delete_voice(voice.id)
        self.voice_tree.set(voice.id, "status", "")
        if self.app.settings.voice == voice.id:
            self.app.settings.voice = ""
        self.app.save_config()
        self.refresh_voice_list()
        self.status(f"Deleted {voice.id}")

    # -- callbacks: settings tab --------------------------------------------

    def apply_hotkeys(self) -> None:
        bindings = {key: variable.get().strip().lower()
                    for key, variable in self.hotkey_vars.items()}
        failures = self.app.apply_hotkeys(bindings)
        if failures:
            lines = [f"{key}: {reason}" for key, reason in sorted(failures.items())]
            self.hotkey_status.configure(text="Could not register - " + "; ".join(lines))
            self.status("Some hotkeys could not be registered.")
        else:
            self.hotkey_status.configure(text="All hotkeys registered.")
            self.status("Hotkeys updated.")

    def _on_behaviour_change(self) -> None:
        if self._updating:
            return
        config = self.app.config
        config.restore_clipboard = self.restore_var.get()
        config.read_clipboard_if_no_selection = self.fallback_var.get()
        config.start_hidden = self.start_hidden_var.get()
        self.app.save_config()

    # -- status --------------------------------------------------------------

    def status(self, message: str) -> None:
        self.status_var.set(message)

    def set_playing_state(self, state: str) -> None:
        self.pause_button.configure(text="Resume" if state == "paused" else "Pause")
        if state == "playing":
            self.status("Speaking...")
        elif state == "paused":
            self.status("Paused")
        elif state == "idle":
            self.status("Ready")

    def run(self) -> None:
        self.root.mainloop()
