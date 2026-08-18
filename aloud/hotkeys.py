"""System-wide hotkeys via RegisterHotKey.

RegisterHotKey delivers WM_HOTKEY to the thread that registered it, so the
registrations and the message loop both live on one dedicated thread. That
also means rebinding is done by restarting the thread rather than by poking at
it from outside.

RegisterHotKey needs no special privileges, which is why it is used here in
preference to a low-level keyboard hook: no elevation prompt, and no
key-logging behaviour that antivirus software takes an interest in.
"""

from __future__ import annotations

import ctypes
import logging
import threading
from ctypes import wintypes
from typing import Callable, Dict, List, Optional

from .winapi import (IS_WINDOWS, WM_HOTKEY, WM_QUIT, MOD_NOREPEAT, kernel32,
                     parse_hotkey, user32)

_LOGGER = logging.getLogger(__name__)


class HotkeyManager:
    """Registers a set of named hotkeys and reports which ones failed."""

    def __init__(self, dispatch: Callable[[str], None]) -> None:
        self._dispatch = dispatch
        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._ready = threading.Event()
        self._ids: Dict[int, str] = {}
        self.failures: Dict[str, str] = {}

    # -- lifecycle -----------------------------------------------------------

    def start(self, bindings: Dict[str, str], timeout: float = 5.0) -> Dict[str, str]:
        """Register `bindings` (name -> "ctrl+alt+s").

        Returns a mapping of the names that could not be registered to the
        reason why, most often because another application owns that chord.
        """
        self.stop()
        self.failures = {}
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, args=(dict(bindings),),
            name="aloud-hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(timeout)
        return dict(self.failures)

    def stop(self) -> None:
        if self._thread and self._thread.is_alive() and self._thread_id:
            # Nudge the message loop so GetMessageW returns and unwinds.
            user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread.join(timeout=2.0)
        self._thread = None
        self._thread_id = None

    def restart(self, bindings: Dict[str, str]) -> Dict[str, str]:
        return self.start(bindings)

    # -- the hotkey thread ---------------------------------------------------

    def _run(self, bindings: Dict[str, str]) -> None:
        if not IS_WINDOWS:
            self._ready.set()
            return

        self._thread_id = kernel32.GetCurrentThreadId()
        registered: List[int] = []

        try:
            for index, (name, spec) in enumerate(sorted(bindings.items()), start=1):
                try:
                    modifiers, key = parse_hotkey(spec)
                except ValueError as error:
                    self.failures[name] = str(error)
                    continue

                # MOD_NOREPEAT stops a held-down chord from firing repeatedly.
                if user32.RegisterHotKey(None, index, modifiers | MOD_NOREPEAT, key):
                    self._ids[index] = name
                    registered.append(index)
                else:
                    self.failures[name] = (
                        f"{spec} is already in use by another application")
                    _LOGGER.warning("RegisterHotKey failed for %s (%s)", name, spec)
        finally:
            self._ready.set()

        message = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result in (0, -1):  # WM_QUIT, or an error we cannot recover from
                    break
                if message.message == WM_HOTKEY:
                    name = self._ids.get(message.wParam)
                    if name:
                        try:
                            self._dispatch(name)
                        except Exception:
                            # A failing handler must not kill the hotkeys.
                            _LOGGER.exception("Hotkey handler for %s failed", name)
        finally:
            for index in registered:
                user32.UnregisterHotKey(None, index)
            self._ids.clear()
