"""Thin ctypes wrappers over the Win32 calls Aloud needs.

Kept in one place so the rest of the app never touches ctypes, and so this
module can be imported (though not used) on other platforms for testing.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from typing import List, Optional

_LOGGER = logging.getLogger(__name__)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
else:  # pragma: no cover - import-time shim for non-Windows development
    user32 = kernel32 = None


class WindowsOnlyError(RuntimeError):
    """Raised when a Win32-only helper is called on another platform."""


def _require_windows() -> None:
    if not IS_WINDOWS:
        raise WindowsOnlyError("this feature requires Windows")


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
INPUT_KEYBOARD = 1

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

VK_CONTROL, VK_SHIFT, VK_MENU = 0x11, 0x10, 0x12
VK_LSHIFT, VK_RSHIFT = 0xA0, 0xA1
VK_LCONTROL, VK_RCONTROL = 0xA2, 0xA3
VK_LMENU, VK_RMENU = 0xA4, 0xA5
VK_LWIN, VK_RWIN = 0x5B, 0x5C

SW_RESTORE = 9

# Named keys usable in a hotkey string. Single characters and digits are
# resolved by ord(), so only the non-printing ones need listing.
NAMED_KEYS = {
    "space": 0x20, "enter": 0x0D, "return": 0x0D, "tab": 0x09, "esc": 0x1B,
    "escape": 0x1B, "backspace": 0x08, "insert": 0x2D, "delete": 0x2E,
    "home": 0x24, "end": 0x23, "pageup": 0x21, "pagedown": 0x22,
    "left": 0x25, "up": 0x26, "right": 0x27, "down": 0x28,
    "plus": 0xBB, "minus": 0xBD, "comma": 0xBC, "period": 0xBE,
    "slash": 0xBF, "backslash": 0xDC, "semicolon": 0xBA, "quote": 0xDE,
    "backtick": 0xC0, "lbracket": 0xDB, "rbracket": 0xDD,
}
NAMED_KEYS.update({f"f{n}": 0x6F + n for n in range(1, 25)})

MODIFIER_NAMES = {
    "ctrl": MOD_CONTROL, "control": MOD_CONTROL,
    "alt": MOD_ALT,
    "shift": MOD_SHIFT,
    "win": MOD_WIN, "super": MOD_WIN, "cmd": MOD_WIN,
}


# --------------------------------------------------------------------------
# Structures
# --------------------------------------------------------------------------

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


if IS_WINDOWS:
    user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
    user32.SendInput.restype = wintypes.UINT
    user32.GetMessageW.argtypes = (
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
    user32.GetMessageW.restype = ctypes.c_int
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = ctypes.c_short
    user32.GetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL


# --------------------------------------------------------------------------
# Hotkey strings
# --------------------------------------------------------------------------

def parse_hotkey(spec: str) -> tuple:
    """Turn "ctrl+alt+s" into the (modifiers, virtual-key) pair Win32 wants."""
    parts = [p.strip().lower() for p in str(spec).split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")

    modifiers = 0
    key: Optional[int] = None
    for part in parts:
        if part in MODIFIER_NAMES:
            modifiers |= MODIFIER_NAMES[part]
        elif part in NAMED_KEYS:
            key = NAMED_KEYS[part]
        elif len(part) == 1 and (part.isalnum()):
            key = ord(part.upper())
        else:
            raise ValueError(f"unrecognised key in hotkey: {part!r}")

    if key is None:
        raise ValueError(f"hotkey {spec!r} has no non-modifier key")
    if not modifiers:
        # Without a modifier we would swallow a bare key from every other app.
        raise ValueError(f"hotkey {spec!r} needs at least one modifier")
    return modifiers, key


def format_hotkey(spec: str) -> str:
    """Pretty form for display: "ctrl+alt+s" -> "Ctrl + Alt + S"."""
    parts = [p.strip() for p in str(spec).split("+") if p.strip()]
    return " + ".join(p.upper() if len(p) == 1 else p.capitalize() for p in parts)


# --------------------------------------------------------------------------
# Synthetic keystrokes
# --------------------------------------------------------------------------

def _key_event(vk: int, up: bool) -> INPUT:
    event = INPUT()
    event.type = INPUT_KEYBOARD
    event.ki = KEYBDINPUT(wVk=vk, wScan=0, dwFlags=KEYEVENTF_KEYUP if up else 0,
                          time=0, dwExtraInfo=0)
    return event


def _send(events: List[INPUT]) -> None:
    if not events:
        return
    array = (INPUT * len(events))(*events)
    sent = user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
    if sent != len(events):
        raise ctypes.WinError(ctypes.get_last_error())


def _held_modifiers() -> List[int]:
    """Which modifier keys the user is physically holding right now."""
    candidates = (VK_LMENU, VK_RMENU, VK_LSHIFT, VK_RSHIFT,
                  VK_LCONTROL, VK_RCONTROL, VK_LWIN, VK_RWIN)
    # The high bit of GetAsyncKeyState means "currently down".
    return [vk for vk in candidates if user32.GetAsyncKeyState(vk) & 0x8000]


def send_copy() -> None:
    """Send Ctrl+C to the focused window.

    The hotkey that triggered this is itself a chord, so the user still has
    Ctrl and Alt held down. Sending Ctrl+C on top of that would deliver
    Ctrl+Alt+C, which most apps ignore. So every held modifier is released
    first, the copy is sent cleanly, and the modifiers the user is still
    holding are pressed again afterwards to leave the keyboard as we found it.
    """
    _require_windows()
    held = _held_modifiers()

    release = [_key_event(vk, up=True) for vk in held]
    copy = [_key_event(VK_CONTROL, up=False), _key_event(ord("C"), up=False),
            _key_event(ord("C"), up=True), _key_event(VK_CONTROL, up=True)]

    _send(release)
    if held:
        time.sleep(0.02)  # let the target app see the modifiers go up
    _send(copy)
    if held:
        time.sleep(0.02)
        # Only re-press what is still physically down.
        still_held = [vk for vk in held if user32.GetAsyncKeyState(vk) & 0x8000]
        _send([_key_event(vk, up=False) for vk in still_held])


# --------------------------------------------------------------------------
# Clipboard
# --------------------------------------------------------------------------

def _open_clipboard(attempts: int = 10, delay: float = 0.02) -> bool:
    """Open the clipboard, retrying: another app may hold it for a moment."""
    for _ in range(attempts):
        if user32.OpenClipboard(None):
            return True
        time.sleep(delay)
    return False


def clipboard_sequence() -> int:
    """A counter Windows bumps on every clipboard change."""
    _require_windows()
    return int(user32.GetClipboardSequenceNumber())


def get_clipboard_text() -> Optional[str]:
    """Read Unicode text off the clipboard, or None if it holds none."""
    _require_windows()
    if not _open_clipboard():
        _LOGGER.warning("Could not open the clipboard for reading")
        return None
    try:
        if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
            return None
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            return ctypes.c_wchar_p(pointer).value
        finally:
            kernel32.GlobalUnlock(handle)
    finally:
        user32.CloseClipboard()


def set_clipboard_text(text: str) -> bool:
    """Put Unicode text on the clipboard."""
    _require_windows()
    if not _open_clipboard():
        _LOGGER.warning("Could not open the clipboard for writing")
        return False
    try:
        user32.EmptyClipboard()
        size = (len(text) + 1) * ctypes.sizeof(ctypes.c_wchar)
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not handle:
            return False
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            kernel32.GlobalFree(handle)
            return False
        try:
            ctypes.memmove(pointer, ctypes.create_unicode_buffer(text), size)
        finally:
            kernel32.GlobalUnlock(handle)
        # On success the system takes ownership of the block, so it must not
        # be freed here.
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            kernel32.GlobalFree(handle)
            return False
        return True
    finally:
        user32.CloseClipboard()


# --------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------

def force_foreground(hwnd: int) -> None:
    """Bring a window to the front and give it focus.

    Windows refuses SetForegroundWindow from a process that does not own the
    current foreground window. The usual workaround is to attach our input
    queue to the foreground thread's for the duration of the call, which makes
    the two count as the same input context.
    """
    _require_windows()
    if not hwnd:
        return

    user32.ShowWindow(hwnd, SW_RESTORE)

    foreground = user32.GetForegroundWindow()
    if foreground == hwnd:
        return

    target_thread = kernel32.GetCurrentThreadId()
    foreground_thread = user32.GetWindowThreadProcessId(foreground, None)

    attached = False
    if foreground_thread and foreground_thread != target_thread:
        attached = bool(user32.AttachThreadInput(foreground_thread, target_thread, True))
    try:
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(foreground_thread, target_thread, False)
