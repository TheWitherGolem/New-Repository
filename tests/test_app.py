"""Integration tests for the app wiring, minus the Tk event loop."""

import sys
import threading
import time
import types

import numpy as np
import pytest

pytest.importorskip("tkinter")
import tkinter


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


class QuietEngine:
    name = "fake"

    def __init__(self):
        self.spoken = []

    def is_available(self):
        return True

    def list_voices(self):
        return []

    def synthesize(self, text, settings, cancel):
        self.spoken.append(text)
        yield (np.zeros(1024, dtype=np.int16), 22050)


@pytest.fixture
def app(monkeypatch, tmp_path):
    try:
        probe = tkinter.Tk()
        probe.destroy()
    except tkinter.TclError as error:
        pytest.skip(f"no display available: {error}")

    from aloud import config, engines

    # A stand-in sound card, and an engine that needs no model files.
    stream = types.SimpleNamespace(
        stopped=True, start=lambda: None, stop=lambda: None,
        abort=lambda: None, close=lambda: None, write=lambda data: None)
    sounddevice = types.ModuleType("sounddevice")
    sounddevice.OutputStream = lambda **kwargs: stream
    sounddevice.query_devices = lambda: [{"name": "fake"}]
    sounddevice.default = types.SimpleNamespace(device=(0, 0))
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)

    monkeypatch.setattr(config, "VALID_ENGINES", config.VALID_ENGINES | {"fake"})
    engine = QuietEngine()
    monkeypatch.setitem(engines._ENGINES, "fake", engine)
    monkeypatch.setattr("aloud.voices.fetch_catalog", lambda force=False: [])

    from aloud.app import AloudApp

    instance = AloudApp()
    instance.settings.engine = "fake"
    instance.engine = engine
    yield instance

    instance.speech.close()
    instance.hotkeys.stop()
    try:
        instance.window.root.destroy()
    except Exception:
        pass


def test_the_app_starts_with_defaults(app):
    assert app.settings.engine == "fake"
    assert app.config.hotkey_speak == "ctrl+alt+s"


def test_speaking_reaches_the_engine(app):
    app.speak_text("Read this out.")
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not app.engine.spoken:
        time.sleep(0.02)
    assert app.engine.spoken == ["Read this out."]


def test_deferred_saves_are_coalesced(app, monkeypatch):
    writes = []
    monkeypatch.setattr(app, "_write_config", lambda: writes.append(1))

    for _ in range(20):
        app.save_config(defer=True)
    assert writes == []          # nothing written while the changes keep coming

    time.sleep(1.3)
    assert writes == [1]         # one write once they stop


def test_an_immediate_save_writes_straight_away(app):
    from aloud.paths import config_file

    app.config.hotkey_stop = "ctrl+alt+q"
    app.save_config()
    assert "ctrl+alt+q" in config_file().read_text(encoding="utf-8")


def test_invalid_hotkeys_are_rejected_before_anything_is_saved(app):
    original = app.config.hotkey_speak
    failures = app.apply_hotkeys({"speak": "banana", "stop": "ctrl+alt+x",
                                  "pause": "ctrl+alt+p", "window": "ctrl+alt+space"})
    assert "speak" in failures
    assert app.config.hotkey_speak == original


def test_valid_hotkeys_are_stored(app):
    failures = app.apply_hotkeys({"speak": "ctrl+shift+r", "stop": "ctrl+shift+x",
                                  "pause": "ctrl+shift+p", "window": "ctrl+shift+space"})
    assert failures == {}
    assert app.config.hotkey_speak == "ctrl+shift+r"


def test_hotkeys_route_to_the_right_actions(app, monkeypatch):
    calls = []
    monkeypatch.setattr(app, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(app, "toggle_pause", lambda: calls.append("pause"))
    monkeypatch.setattr(app, "speak_selection", lambda: calls.append("speak"))

    app._on_hotkey("stop")
    app._on_hotkey("pause")
    app._on_hotkey("window")
    app._on_hotkey("speak")

    deadline = time.monotonic() + 2
    while time.monotonic() < deadline and "speak" not in calls:
        time.sleep(0.02)
    assert sorted(calls) == ["pause", "speak", "stop"]


def test_a_failing_hotkey_action_does_not_take_down_the_listener(app):
    # The dispatcher runs on the Win32 message loop thread; an exception
    # escaping it would silently kill every hotkey.
    app._on_hotkey("no-such-action")


def test_presets_replace_the_current_settings(app):
    app.apply_preset({"engine": "sapi", "speed": 1.8, "voice": "en_GB-alba-medium"})
    assert app.settings.speed == 1.8
    assert app.settings.voice == "en_GB-alba-medium"


def test_first_run_falls_back_to_windows_voices_when_piper_has_none(app):
    app.settings.engine = "piper"
    app._first_run_checks()
    app.window._pump()
    # On a machine with neither Piper voices nor SAPI, the status still
    # explains itself rather than the app silently doing nothing.
    assert app.window.status_var.get()


def test_shutdown_saves_and_stops_everything(app):
    from aloud.paths import config_file

    app.config.hotkey_pause = "ctrl+alt+m"
    app._shutdown()
    assert "ctrl+alt+m" in config_file().read_text(encoding="utf-8")
    assert not app.speech.is_playing
