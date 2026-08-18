import json

import pytest

from aloud.config import Config, VoiceSettings, clamp


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    """Point the app's config and data directories at a temporary profile."""
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path


def test_defaults_are_sensible():
    settings = VoiceSettings()
    assert settings.speed == 1.0
    assert settings.pitch == 1.0
    assert settings.engine == "piper"


def test_pitch_is_compensated_in_the_synthesis_speed():
    # Speaking 1.25x higher means synthesising 1.25x slower, so that the
    # resampling which raises the pitch brings the duration back to 1.0x.
    settings = VoiceSettings(speed=1.0, pitch=1.25)
    assert settings.effective_speed == pytest.approx(0.8)
    assert settings.length_scale > 1.0


def test_speed_and_pitch_cancel_out():
    # Same speed and pitch means no net change to how long it takes to say.
    settings = VoiceSettings(speed=2.0, pitch=2.0)
    assert settings.effective_speed == pytest.approx(1.0)
    assert settings.length_scale == pytest.approx(1.0)


def test_length_scale_is_calibrated_past_the_naive_inverse():
    from aloud.config import DURATION_EXPONENT

    # Piper compresses its own speed response, so asking for 2x has to ask for
    # a shorter length_scale than a straight 1/speed would.
    settings = VoiceSettings(speed=2.0)
    assert settings.length_scale < 0.5
    assert settings.length_scale == pytest.approx(0.5 ** (1 / DURATION_EXPONENT))


def test_length_scale_is_monotonic_in_speed():
    scales = [VoiceSettings(speed=speed).length_scale
              for speed in (0.5, 0.75, 1.0, 1.5, 2.0, 2.5)]
    assert scales == sorted(scales, reverse=True)
    assert VoiceSettings(speed=1.0).length_scale == pytest.approx(1.0)


def test_out_of_range_values_are_clamped():
    settings = VoiceSettings(speed=99, pitch=0.01, volume=-5, sentence_pause=100)
    settings.normalise()
    assert settings.speed == 2.5
    assert settings.pitch == 0.7
    assert settings.volume == 0.0
    assert settings.sentence_pause == 2.0


def test_unknown_engine_falls_back_to_piper():
    settings = VoiceSettings(engine="wishful-thinking")
    settings.normalise()
    assert settings.engine == "piper"


def test_negative_speaker_index_is_clamped():
    settings = VoiceSettings(speaker=-3)
    settings.normalise()
    assert settings.speaker == 0


def test_clamp_helper():
    assert clamp("speed", 10) == 2.5
    assert clamp("speed", 1.2) == 1.2


def test_unknown_keys_in_a_config_file_are_ignored():
    settings = VoiceSettings.from_dict({"speed": 1.5, "from_a_later_version": True})
    assert settings.speed == 1.5


def test_settings_round_trip_through_disk():
    config = Config()
    config.voice_settings.voice = "en_GB-alba-medium"
    config.voice_settings.speed = 1.35
    config.voice_settings.speaker = 7
    config.hotkey_speak = "ctrl+shift+r"
    config.presets["Storyteller"] = config.voice_settings.to_dict()
    config.save()

    loaded = Config.load()
    assert loaded.voice_settings.voice == "en_GB-alba-medium"
    assert loaded.voice_settings.speed == 1.35
    assert loaded.voice_settings.speaker == 7
    assert loaded.hotkey_speak == "ctrl+shift+r"
    assert "Storyteller" in loaded.presets


def test_missing_config_gives_defaults():
    assert Config.load().hotkey_speak == "ctrl+alt+s"


def test_a_corrupt_config_is_set_aside_rather_than_crashing():
    from aloud.paths import config_file, ensure_dirs

    ensure_dirs()
    config_file().write_text("{ this is not json", encoding="utf-8")

    config = Config.load()
    assert config.hotkey_speak == "ctrl+alt+s"
    assert config_file().with_suffix(".json.bad").exists()


def test_saving_is_atomic_and_leaves_no_temp_file():
    from aloud.paths import config_file

    Config().save()
    assert json.loads(config_file().read_text(encoding="utf-8"))
    assert not config_file().with_suffix(".json.tmp").exists()


def test_a_copy_is_independent():
    settings = VoiceSettings(speed=1.5)
    duplicate = settings.copy()
    duplicate.speed = 0.75
    assert settings.speed == 1.5
