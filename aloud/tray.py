"""The tray icon.

Aloud spends most of its life here: no taskbar entry, no window, just an icon
and the hotkeys. The icon runs its own message loop on a background thread,
which is fine on Windows, and every menu action is bounced back onto the Tk
thread rather than touching widgets directly.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

_LOGGER = logging.getLogger(__name__)

_ICON_SIZE = 64


def _build_image():
    """Draw a small speaker-with-soundwaves icon."""
    from PIL import Image, ImageDraw

    image = Image.new("RGBA", (_ICON_SIZE, _ICON_SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    body = (46, 125, 208, 255)
    # Speaker: a small box with a cone flaring out to the right.
    draw.rectangle((10, 25, 22, 39), fill=body)
    draw.polygon([(22, 32), (34, 18), (34, 46)], fill=body)
    # Two arcs for the sound coming out of it.
    draw.arc((28, 16, 48, 48), start=-55, end=55, fill=body, width=4)
    draw.arc((32, 8, 60, 56), start=-55, end=55, fill=body, width=4)
    return image


class Tray:
    def __init__(self, app) -> None:
        self.app = app
        self._icon = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> bool:
        """Create and show the icon. False if pystray is unavailable."""
        try:
            import pystray
        except Exception as error:
            _LOGGER.warning("Tray icon unavailable: %s", error)
            return False

        menu = pystray.Menu(
            pystray.MenuItem("Show Aloud", self._on_show, default=True),
            pystray.MenuItem("Read selection", self._on_speak),
            pystray.MenuItem("Pause / resume", self._on_pause),
            pystray.MenuItem("Stop", self._on_stop),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self._on_quit),
        )

        try:
            self._icon = pystray.Icon("aloud", _build_image(), "Aloud", menu)
        except Exception:
            _LOGGER.warning("Could not create the tray icon", exc_info=True)
            return False

        self._thread = threading.Thread(target=self._run, name="aloud-tray", daemon=True)
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            self._icon.run()
        except Exception:
            _LOGGER.exception("The tray icon stopped unexpectedly")

    def notify(self, message: str, title: str = "Aloud") -> None:
        """Show a balloon notification, where the platform supports one."""
        if self._icon is None:
            return
        try:
            self._icon.notify(message, title)
        except Exception:
            _LOGGER.debug("Tray notification failed", exc_info=True)

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                _LOGGER.debug("Could not stop the tray icon", exc_info=True)
            self._icon = None

    # -- menu actions (called on the tray thread) ---------------------------

    def _on_show(self, *_args) -> None:
        self.app.show_window()

    def _on_speak(self, *_args) -> None:
        self.app.speak_selection()

    def _on_pause(self, *_args) -> None:
        self.app.toggle_pause()

    def _on_stop(self, *_args) -> None:
        self.app.stop()

    def _on_quit(self, *_args) -> None:
        self.app.quit()
