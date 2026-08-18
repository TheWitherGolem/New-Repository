import pytest

from aloud.winapi import (MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN,
                          format_hotkey, parse_hotkey)


def test_letter_with_modifiers():
    assert parse_hotkey("ctrl+alt+s") == (MOD_CONTROL | MOD_ALT, ord("S"))


def test_case_and_spacing_are_forgiving():
    assert parse_hotkey("  CTRL + Alt + S ") == parse_hotkey("ctrl+alt+s")


def test_function_keys():
    assert parse_hotkey("ctrl+shift+f9") == (MOD_CONTROL | MOD_SHIFT, 0x78)
    assert parse_hotkey("alt+f12") == (MOD_ALT, 0x7B)


def test_named_keys():
    assert parse_hotkey("ctrl+alt+space") == (MOD_CONTROL | MOD_ALT, 0x20)
    assert parse_hotkey("win+alt+up") == (MOD_WIN | MOD_ALT, 0x26)


def test_digits():
    assert parse_hotkey("ctrl+alt+1") == (MOD_CONTROL | MOD_ALT, ord("1"))


def test_modifier_aliases():
    assert parse_hotkey("control+super+k") == (MOD_CONTROL | MOD_WIN, ord("K"))


@pytest.mark.parametrize("spec", ["", "   ", "ctrl", "alt+shift", "s", "f5"])
def test_incomplete_hotkeys_are_rejected(spec):
    # A bare key would be swallowed from every other application, so at least
    # one modifier plus one real key is required.
    with pytest.raises(ValueError):
        parse_hotkey(spec)


def test_unknown_key_names_are_rejected():
    with pytest.raises(ValueError):
        parse_hotkey("ctrl+alt+banana")


def test_display_formatting():
    assert format_hotkey("ctrl+alt+s") == "Ctrl + Alt + S"
    assert format_hotkey("ctrl+shift+f9") == "Ctrl + Shift + F9"
