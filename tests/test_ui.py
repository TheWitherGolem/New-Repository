"""Smoke tests for the control window.

These build the real window against a stand-in for the app object, so a typo in
a widget option or a broken grid shows up here rather than on first launch.
Skipped where there is no display to build a window on.
"""

import sys
import types

import pytest

tkinter = pytest.importorskip("tkinter")


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))


class StubApp:
    """Records what the window asks the app to do."""

    def __init__(self):
        from aloud.config import Config

        self.config = Config()
        self.saves = 0
        self.spoken = []
        self.stopped = 0
        self.paused = 0
        self.selections = 0
        self.hotkeys_applied = None
        self.quit_calls = 0

    @property
    def settings(self):
        return self.config.voice_settings

    def save_config(self, defer=False):
        self.saves += 1

    def speak_text(self, text):
        self.spoken.append(text)

    def speak_selection(self):
        self.selections += 1

    def stop(self):
        self.stopped += 1

    def toggle_pause(self):
        self.paused += 1

    def render_to_file(self, text, path):
        pass

    def apply_hotkeys(self, bindings):
        self.hotkeys_applied = bindings
        return {}

    def apply_preset(self, data):
        from aloud.config import VoiceSettings

        self.config.voice_settings = VoiceSettings.from_dict(data)

    def quit(self):
        self.quit_calls += 1


@pytest.fixture
def window(monkeypatch):
    try:
        probe = tkinter.Tk()
        probe.destroy()
    except tkinter.TclError as error:
        pytest.skip(f"no display available: {error}")

    from aloud.ui import MainWindow

    # The catalog fetch would otherwise hit the network on construction.
    monkeypatch.setattr("aloud.voices.fetch_catalog", lambda force=False: [])

    app = StubApp()
    main = MainWindow(app)
    main.app = app
    yield main
    main.root.destroy()


def test_the_window_builds(window):
    assert window.root.title() == "Aloud"
    # It starts hidden: Aloud lives in the tray.
    assert window.root.state() == "withdrawn"


def test_show_and_hide(window):
    window.show()
    window.root.update()
    assert window.root.state() != "withdrawn"
    window.hide()
    window.root.update()
    assert window.root.state() == "withdrawn"


def test_moving_a_slider_updates_the_settings_and_its_label(window):
    window._on_slider("speed", 1.4321, 0.05, "{:.2f}x")
    assert window.app.settings.speed == pytest.approx(1.45)
    assert window.slider_labels["speed"].cget("text") == "1.45x"


def test_slider_values_are_clamped_to_their_limits(window):
    window._on_slider("pitch", 99, 0.01, "{:.2f}x")
    assert window.app.settings.pitch == 1.4


def test_double_clicking_a_slider_restores_its_default(window):
    window._on_slider("cadence", 1.75, 0.01, "{:.2f}")
    assert window.app.settings.cadence == pytest.approx(1.75)
    window._reset_slider("cadence")
    assert window.app.settings.cadence == 0.8


def test_the_placeholder_is_not_treated_as_text(window):
    assert window.current_text() == ""
    window.speak_text()
    assert window.app.spoken == []
    assert "Nothing to read" in window.status_var.get()


def test_typed_text_is_spoken(window):
    window.set_text("Hello there.")
    window.speak_text()
    assert window.app.spoken == ["Hello there."]


def test_switching_engine_updates_the_settings(window):
    window.engine_var.set("Windows built-in voices")
    window._on_engine_change()
    assert window.app.settings.engine == "sapi"


def test_presets_round_trip(window):
    window.app.settings.speed = 1.75
    window.app.config.presets["Fast"] = window.app.settings.to_dict()
    window._refresh_presets()
    assert "Fast" in window.preset_box["values"]

    window.app.settings.speed = 1.0
    window.preset_var.set("Fast")
    window._on_preset_selected()
    assert window.app.settings.speed == 1.75


def test_deleting_a_preset(window):
    window.app.config.presets["Gone"] = window.app.settings.to_dict()
    window._refresh_presets()
    window.preset_var.set("Gone")
    window.delete_preset()
    assert "Gone" not in window.app.config.presets


def test_the_catalog_populates_the_table(window):
    from aloud.voices import CURATED

    window._show_catalog(list(CURATED))
    rows = window.voice_tree.get_children()
    assert len(rows) == len(CURATED)
    assert window.voice_tree.set(CURATED[0].id, "voice") == CURATED[0].id
    assert "voices available" in window.status_var.get()


def test_selecting_a_multi_speaker_voice_explains_the_speaker_control(window):
    from aloud.voices import CURATED

    multi = next(v for v in CURATED if v.num_speakers > 1)
    window._show_catalog(list(CURATED))
    window.voice_tree.selection_set(multi.id)
    window._on_catalog_select()
    assert "Speaker" in window.voice_note.cget("text")


def test_the_speaker_control_appears_only_for_multi_speaker_models(window):
    from aloud.engines.base import VoiceInfo

    single = VoiceInfo(id="en_US-amy-medium", label="Amy", speakers=[])
    window._voice_infos = {"Amy": single}
    window.voice_var.set("Amy")
    window._update_speaker_control()
    assert not window.speaker_row.winfo_manager()

    many = VoiceInfo(id="en_GB-vctk-medium", label="Vctk",
                     speakers=["p225", "p226", "p227"])
    window._voice_infos = {"Vctk": many}
    window.voice_var.set("Vctk")
    window._update_speaker_control()
    assert window.speaker_row.winfo_manager() == "grid"
    assert "p225" in window.speaker_name.cget("text")


def test_changing_the_speaker_is_stored(window):
    from aloud.engines.base import VoiceInfo

    window._voice_infos = {"Vctk": VoiceInfo(id="en_GB-vctk-medium", label="Vctk",
                                             speakers=[f"p{n}" for n in range(10)])}
    window.voice_var.set("Vctk")
    window._update_speaker_control()
    window.speaker_var.set(4)
    window._on_speaker_change()
    assert window.app.settings.speaker == 4
    assert "p4" in window.speaker_name.cget("text")


def test_applying_hotkeys_hands_them_to_the_app(window):
    window.hotkey_vars["speak"].set("Ctrl+Alt+R")
    window.apply_hotkeys()
    assert window.app.hotkeys_applied["speak"] == "ctrl+alt+r"
    assert "registered" in window.hotkey_status.cget("text")


def test_hotkey_failures_are_shown(window, monkeypatch):
    monkeypatch.setattr(window.app, "apply_hotkeys",
                        lambda bindings: {"speak": "already in use"})
    window.apply_hotkeys()
    assert "already in use" in window.hotkey_status.cget("text")


def test_behaviour_checkboxes_write_through(window):
    window.restore_var.set(False)
    window.fallback_var.set(False)
    window._on_behaviour_change()
    assert window.app.config.restore_clipboard is False
    assert window.app.config.read_clipboard_if_no_selection is False


def test_transport_buttons_reach_the_app(window):
    window.app.stop()
    window.app.toggle_pause()
    window.app.speak_selection()
    assert (window.app.stopped, window.app.paused, window.app.selections) == (1, 1, 1)


def test_playing_state_relabels_the_pause_button(window):
    window.set_playing_state("paused")
    assert window.pause_button.cget("text") == "Resume"
    window.set_playing_state("playing")
    assert window.pause_button.cget("text") == "Pause"
    assert window.status_var.get() == "Speaking..."


def test_posted_callbacks_run_on_the_ui_thread(window):
    ran = []
    window.post(lambda: ran.append(True))
    window._pump()
    assert ran == [True]


def test_a_failing_posted_callback_does_not_stop_the_pump(window):
    ran = []

    def explode():
        raise RuntimeError("boom")

    window.post(explode)
    window.post(lambda: ran.append(True))
    window._pump()
    assert ran == [True]
