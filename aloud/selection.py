"""Reading whatever text the user currently has highlighted.

Windows has no API for "give me the selection in the focused window", so the
universal trick is used instead: synthesise a Ctrl+C, watch the clipboard for a
change, then put the user's own clipboard back. It works in any app that
supports copying, which is what "ANY text" needs.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

from . import winapi

_LOGGER = logging.getLogger(__name__)

# How long to wait for the target app to service the copy. Slow apps (big PDFs,
# remote desktops) can take a moment; polling means fast apps still feel snappy.
COPY_TIMEOUT = 0.7
POLL_INTERVAL = 0.02
# Give apps that read the clipboard lazily a moment before we restore it.
RESTORE_DELAY = 0.25


def capture_selection(restore_clipboard: bool = True,
                      fall_back_to_clipboard: bool = True) -> Optional[str]:
    """Return the highlighted text, or None if nothing could be read.

    If the copy produces nothing - because nothing was selected, or the app
    does not support Ctrl+C - the current clipboard contents are returned
    instead when `fall_back_to_clipboard` is set, so the hotkey still does
    something useful.
    """
    previous = winapi.get_clipboard_text()
    sequence_before = winapi.clipboard_sequence()

    try:
        winapi.send_copy()
    except OSError:
        # SendInput is refused when the focused window belongs to a process
        # running at a higher integrity level than ours.
        _LOGGER.warning("Could not send Ctrl+C to the focused window")
        return previous if fall_back_to_clipboard else None

    selection: Optional[str] = None
    deadline = time.monotonic() + COPY_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        if winapi.clipboard_sequence() != sequence_before:
            selection = winapi.get_clipboard_text()
            break

    if selection is not None and selection.strip():
        if restore_clipboard and previous is not None:
            # Restoring immediately can beat the source app to its own
            # clipboard read, so hand the value back a beat later.
            time.sleep(RESTORE_DELAY)
            winapi.set_clipboard_text(previous)
        return selection

    # Nothing was selected: the clipboard never changed, or it changed to
    # something empty. Either way there is nothing of ours to undo.
    if fall_back_to_clipboard and previous and previous.strip():
        return previous
    return None
