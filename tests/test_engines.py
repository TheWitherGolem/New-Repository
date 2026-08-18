import json
import wave

import numpy as np
import pytest

from aloud.engines.piper_engine import PiperEngine, _pretty_label
from aloud.engines.sapi_engine import _read_wav, _sapi_rate


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path


# -- Piper -------------------------------------------------------------------

@pytest.mark.parametrize("voice_id,expected", [
    ("en_US-amy-medium", "Amy (en_US, medium)"),
    ("en_GB-northern_english_male-medium", "Northern English Male (en_GB, medium)"),
    ("en_GB-cori-high", "Cori (en_GB, high)"),
])
def test_voice_labels_are_readable(voice_id, expected):
    assert _pretty_label(voice_id) == expected


def _install_voice(voice_id, config):
    from aloud.paths import voices_dir

    directory = voices_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{voice_id}.onnx").write_bytes(b"not a real model")
    (directory / f"{voice_id}.onnx.json").write_text(json.dumps(config))


def test_installed_voices_are_described_from_their_config():
    _install_voice("en_GB-alba-medium", {
        "language": {"name_english": "English"},
        "audio": {"quality": "medium"},
        "num_speakers": 1,
    })

    voice = PiperEngine().list_voices()[0]
    assert voice.id == "en_GB-alba-medium"
    assert voice.language == "English"
    assert voice.quality == "medium"
    assert not voice.is_multi_speaker


def test_multi_speaker_models_list_their_speakers_in_index_order():
    _install_voice("en_GB-vctk-medium", {
        "language": {"name_english": "English"},
        "audio": {"quality": "medium"},
        "num_speakers": 3,
        "speaker_id_map": {"p239": 1, "p225": 0, "p226": 2},
    })

    voice = PiperEngine().list_voices()[0]
    assert voice.is_multi_speaker
    assert voice.speakers == ["p225", "p239", "p226"]


def test_a_model_without_its_config_is_not_offered():
    from aloud.paths import voices_dir

    voices_dir().mkdir(parents=True, exist_ok=True)
    (voices_dir() / "en_US-amy-medium.onnx").write_bytes(b"half a download")
    assert PiperEngine().list_voices() == []


def test_an_unreadable_config_does_not_break_the_listing():
    from aloud.paths import voices_dir

    voices_dir().mkdir(parents=True, exist_ok=True)
    (voices_dir() / "en_US-amy-medium.onnx").write_bytes(b"model")
    (voices_dir() / "en_US-amy-medium.onnx.json").write_text("{ broken")

    voice = PiperEngine().list_voices()[0]
    assert voice.id == "en_US-amy-medium"
    assert voice.quality == "medium"  # recovered from the file name


def test_loading_a_missing_voice_explains_itself():
    from aloud.engines.base import EngineError

    engine = PiperEngine()
    if not engine.is_available():
        pytest.skip("piper-tts is not installed here")
    with pytest.raises(EngineError, match="not installed"):
        engine._load("en_US-nobody-medium")


# -- SAPI --------------------------------------------------------------------

def test_sapi_rate_is_zero_at_normal_speed():
    assert _sapi_rate(1.0) == 0


def test_sapi_rate_moves_in_the_right_direction_and_stays_in_range():
    assert _sapi_rate(0.5) < 0
    assert _sapi_rate(2.0) > 0
    assert -10 <= _sapi_rate(0.01) <= 10
    assert -10 <= _sapi_rate(100) <= 10


def _write_wav(path, audio, rate=22050, channels=1, width=2):
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(width)
        handle.setframerate(rate)
        handle.writeframes(audio.tobytes())


def test_reading_a_mono_wav(tmp_path):
    path = tmp_path / "mono.wav"
    audio = np.array([0, 1000, -1000, 32767], dtype=np.int16)
    _write_wav(path, audio)

    loaded, rate = _read_wav(path)
    assert rate == 22050
    assert loaded.tolist() == audio.tolist()


def test_a_stereo_wav_is_mixed_down(tmp_path):
    path = tmp_path / "stereo.wav"
    interleaved = np.array([100, 300, 0, 1000], dtype=np.int16)  # L,R,L,R
    _write_wav(path, interleaved, channels=2)

    loaded, _rate = _read_wav(path)
    assert loaded.tolist() == [200, 500]


def test_an_eight_bit_wav_is_converted(tmp_path):
    path = tmp_path / "eight.wav"
    _write_wav(path, np.array([128, 255, 0], dtype=np.uint8), width=1)

    loaded, _rate = _read_wav(path)
    assert loaded.dtype == np.int16
    assert loaded[0] == 0            # 128 is silence in unsigned 8-bit
    assert loaded[1] > 30000
    assert loaded[2] < -30000


def test_a_missing_or_corrupt_wav_returns_nothing(tmp_path):
    assert _read_wav(tmp_path / "absent.wav") is None
    broken = tmp_path / "broken.wav"
    broken.write_bytes(b"RIFFnonsense")
    assert _read_wav(broken) is None
