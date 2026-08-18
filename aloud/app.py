"""Wiring: hotkeys, tray, window and the speech pipeline.

Threads in play:
  main      - Tkinter's event loop, and the only thread allowed near a widget
  hotkeys   - a Win32 message loop, kept free of slow work
  synthesis - one per utterance, owned by SpeechService
  audio     - one long-lived output thread
  workers   - short-lived threads for selection capture, downloads, rendering
"""

from __future__ import annotations

import logging
import logging.handlers
import threading
from pathlib import Path
from typing import Dict, Optional

from . import APP_NAME, __version__
from .config import Config, VoiceSettings
from .hotkeys import HotkeyManager
from .paths import ensure_dirs, log_file
from .selection import capture_selection
from .speech import SpeechService
from .tray import Tray
from .ui import MainWindow
from .winapi import IS_WINDOWS, WindowsOnlyError

_LOGGER = logging.getLogger(__name__)

# Settings are written a second after the last change, so dragging a slider
# does not mean a hundred writes to disk.
_SAVE_DELAY = 1.0


def setup_logging(verbose: bool = False) -> None:
    ensure_dirs()
    handlers = [logging.handlers.RotatingFileHandler(
        log_file(), maxBytes=512_000, backupCount=2, encoding="utf-8")]
    if verbose:
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        handlers=handlers)


class AloudApp:
    def __init__(self) -> None:
        ensure_dirs()
        self.config = Config.load()
        self._save_timer: Optional[threading.Timer] = None
        self._save_lock = threading.Lock()

        self.speech = SpeechService(on_state=self._on_speech_state,
                                    on_error=self._on_speech_error)
        self.window = MainWindow(self)
        self.tray = Tray(self)
        self.hotkeys = HotkeyManager(self._on_hotkey)

    @property
    def settings(self) -> VoiceSettings:
        return self.config.voice_settings

    # -- startup -------------------------------------------------------------

    def run(self) -> None:
        _LOGGER.info("Starting %s %s", APP_NAME, __version__)

        failures = self.hotkeys.start(self._binding_map())
        if failures:
            summary = "; ".join(f"{name} ({reason})" for name, reason in failures.items())
            _LOGGER.warning("Hotkeys not registered: %s", summary)
            self.window.post(lambda: self.window.status(
                "Some hotkeys are in use by other apps - change them on the "
                "Settings tab."))

        if not self.tray.start():
            self.window.post(lambda: self.window.status(
                "The tray icon could not be created; closing this window will "
                "hide it, and the hotkeys keep working."))

        self.window.post(self._first_run_checks)
        self.window.post(lambda: self.window.refresh_catalog(force=False))

        if self.config.start_hidden and self.settings.voice:
            self.window.hide()
        else:
            self.window.post(self.window.show)

        try:
            self.window.run()
        finally:
            self._shutdown()

    def _first_run_checks(self) -> None:
        """Make sure the app can actually speak, and say so if it cannot."""
        from .audio import check_output_available
        from .engines import get_engine

        problem = check_output_available()
        if problem:
            self.window.status(problem)
            return

        if self.settings.engine == "piper":
            piper = get_engine("piper")
            if not piper.is_available():
                self._fall_back_to_sapi(
                    "Piper is not installed yet, so Aloud is using a built-in "
                    "Windows voice. Run install.ps1 for the neural voices.")
                return
            if not piper.list_voices():
                self._fall_back_to_sapi(
                    "No Piper voice is installed yet, so Aloud is using a "
                    "built-in Windows voice. Open the Voices tab to download one.")
                return

        self.window.status(f"Ready - press {self.config.hotkey_speak} to read a selection.")

    def _fall_back_to_sapi(self, message: str) -> None:
        """Switch to Windows voices so the app is usable right now.

        The change is deliberately not saved: the user's chosen engine stays
        chosen, and Aloud goes back to it once a Piper voice exists.
        """
        from .engines import get_engine

        if get_engine("sapi").is_available():
            self.settings.engine = "sapi"
            self.window.post(self.window._load_settings_into_widgets)
            self.window.post(self.window.refresh_voice_list)
        self.window.status(message)

    # -- hotkeys -------------------------------------------------------------

    def _binding_map(self) -> Dict[str, str]:
        return {"speak": self.config.hotkey_speak,
                "stop": self.config.hotkey_stop,
                "pause": self.config.hotkey_pause,
                "window": self.config.hotkey_window}

    def _on_hotkey(self, name: str) -> None:
        """Runs on the hotkey thread, so it must return immediately."""
        if name == "speak":
            threading.Thread(target=self.speak_selection,
                             name="aloud-selection", daemon=True).start()
        elif name == "stop":
            self.stop()
        elif name == "pause":
            self.toggle_pause()
        elif name == "window":
            self.window.post(self.window.toggle)

    def apply_hotkeys(self, bindings: Dict[str, str]) -> Dict[str, str]:
        """Validate, save and re-register the hotkeys. Returns any failures."""
        from .winapi import parse_hotkey

        failures: Dict[str, str] = {}
        for name, spec in bindings.items():
            try:
                parse_hotkey(spec)
            except ValueError as error:
                failures[name] = str(error)
        if failures:
            return failures

        self.config.hotkey_speak = bindings["speak"]
        self.config.hotkey_stop = bindings["stop"]
        self.config.hotkey_pause = bindings["pause"]
        self.config.hotkey_window = bindings["window"]
        self.save_config()

        return self.hotkeys.restart(self._binding_map())

    # -- speaking ------------------------------------------------------------

    def speak_selection(self) -> None:
        """Read whatever is highlighted in the foreground application."""
        if not IS_WINDOWS:
            self.window.post(lambda: self.window.status(
                "Reading the selection needs Windows."))
            return

        try:
            text = capture_selection(
                restore_clipboard=self.config.restore_clipboard,
                fall_back_to_clipboard=self.config.read_clipboard_if_no_selection)
        except WindowsOnlyError:
            return
        except Exception:
            _LOGGER.exception("Could not read the selection")
            self.window.post(lambda: self.window.status(
                "Could not read the selection from that application."))
            return

        if not text or not text.strip():
            self.window.post(lambda: self.window.status(
                "Nothing was selected."))
            return

        preview = " ".join(text.split())[:60]
        self.window.post(lambda: self.window.status(f"Reading: {preview}..."))
        self.window.post(lambda: self.window.set_text(text))
        self.speak_text(text)

    def speak_text(self, text: str) -> None:
        if not self.speech.speak(text, self.settings):
            self.window.post(lambda: self.window.status("Nothing to read."))

    def stop(self) -> None:
        self.speech.stop()

    def toggle_pause(self) -> None:
        self.speech.toggle_pause()

    def render_to_file(self, text: str, path: Path) -> None:
        """Save an utterance to a WAV file on a worker thread."""
        settings = self.settings.copy()

        def work() -> None:
            try:
                self.speech.render_to_wav(text, settings, path)
                self.window.post(lambda: self.window.status(f"Saved to {path}"))
            except Exception as error:
                message = str(error)
                self.window.post(lambda: self.window.status(f"Could not save: {message}"))

        threading.Thread(target=work, name="aloud-render", daemon=True).start()

    def _on_speech_state(self, state: str) -> None:
        self.window.post(lambda: self.window.set_playing_state(state))

    def _on_speech_error(self, message: str) -> None:
        self.window.post(lambda: self.window.status(message))

    # -- window / tray -------------------------------------------------------

    def show_window(self) -> None:
        self.window.post(self.window.show)

    def apply_preset(self, data: Dict) -> None:
        self.config.voice_settings = VoiceSettings.from_dict(data)
        self.save_config()

    # -- persistence ---------------------------------------------------------

    def save_config(self, defer: bool = False) -> None:
        """Write settings out, coalescing bursts of changes when deferred."""
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None

            if not defer:
                self._write_config()
                return

            self._save_timer = threading.Timer(_SAVE_DELAY, self._write_config)
            self._save_timer.daemon = True
            self._save_timer.start()

    def _write_config(self) -> None:
        try:
            self.config.save()
        except Exception:
            _LOGGER.exception("Could not save settings")

    # -- shutdown ------------------------------------------------------------

    def quit(self) -> None:
        self.window.post(self.window.root.quit)

    def _shutdown(self) -> None:
        _LOGGER.info("Shutting down")
        with self._save_lock:
            if self._save_timer is not None:
                self._save_timer.cancel()
                self._save_timer = None
        self._write_config()

        self.hotkeys.stop()
        self.tray.stop()
        self.speech.close()
        try:
            self.window.root.destroy()
        except Exception:
            pass
