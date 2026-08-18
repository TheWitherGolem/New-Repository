import numpy as np
import pytest

from aloud.dsp import apply_gain, fade_out, resample, silence, trim_silence


def _tone(frequency=440, seconds=0.2, rate=22050):
    t = np.arange(int(rate * seconds)) / rate
    return (np.sin(2 * np.pi * frequency * t) * 8000).astype(np.int16)


def test_resample_shortens_for_a_higher_pitch():
    audio = _tone()
    assert resample(audio, 1.25).size == round(audio.size / 1.25)
    assert resample(audio, 0.8).size == round(audio.size / 0.8)


def test_resample_shifts_frequency_by_the_factor():
    rate = 22050

    def dominant(signal):
        spectrum = np.abs(np.fft.rfft(signal.astype(np.float64)))
        return np.fft.rfftfreq(signal.size, 1 / rate)[np.argmax(spectrum)]

    audio = _tone(frequency=440, seconds=1.0, rate=rate)
    assert dominant(audio) == pytest.approx(440, rel=0.02)
    # Played back at the same rate, the resampled tone is 1.5x higher.
    assert dominant(resample(audio, 1.5)) == pytest.approx(660, rel=0.02)
    assert dominant(resample(audio, 0.75)) == pytest.approx(330, rel=0.02)


def test_resample_is_a_no_op_for_unit_factor():
    audio = _tone()
    assert resample(audio, 1.0) is audio


def test_resample_keeps_dtype_and_handles_empty():
    assert resample(_tone(), 1.3).dtype == np.int16
    assert resample(np.array([], dtype=np.int16), 1.3).size == 0


def test_gain_clips_instead_of_wrapping():
    loud = np.array([30000, -30000], dtype=np.int16)
    boosted = apply_gain(loud, 4.0)
    assert boosted.dtype == np.int16
    assert boosted.tolist() == [32767, -32768]


def test_gain_scales_quiet_audio():
    assert apply_gain(np.array([1000], dtype=np.int16), 0.5).tolist() == [500]


def test_silence_length_matches_the_requested_duration():
    assert silence(0.35, 22050).size == 7717
    assert silence(0.0, 22050).size == 0
    assert not silence(0.1, 22050).any()


def test_fade_out_ends_at_zero():
    audio = np.full(1000, 5000, dtype=np.int16)
    faded = fade_out(audio, samples=100)
    assert faded[-1] == 0
    assert faded[0] == 5000


def test_trim_removes_leading_and_trailing_quiet():
    rate = 22050
    quiet = np.zeros(rate // 2, dtype=np.int16)          # half a second each end
    speech = np.full(rate, 8000, dtype=np.int16)
    trimmed = trim_silence(np.concatenate([quiet, speech, quiet]), rate,
                           margin_seconds=0.0)
    assert trimmed.size == rate
    assert (trimmed == 8000).all()


def test_trim_keeps_a_margin_so_consonants_survive():
    rate = 22050
    padded = np.concatenate([np.zeros(rate, dtype=np.int16),
                             np.full(rate, 8000, dtype=np.int16),
                             np.zeros(rate, dtype=np.int16)])
    trimmed = trim_silence(padded, rate, margin_seconds=0.02)
    assert trimmed.size == rate + 2 * int(0.02 * rate)


def test_trim_leaves_speech_that_runs_to_the_edges_alone():
    audio = np.full(1000, 8000, dtype=np.int16)
    assert trim_silence(audio, 22050).size == 1000


def test_trim_of_pure_silence_yields_nothing():
    assert trim_silence(np.zeros(22050, dtype=np.int16), 22050).size == 0


def test_trim_ignores_inaudible_noise():
    rate = 22050
    # A whisper-quiet noise floor should not count as speech.
    floor = np.full(rate, 50, dtype=np.int16)
    speech = np.full(rate, 8000, dtype=np.int16)
    trimmed = trim_silence(np.concatenate([floor, speech]), rate, margin_seconds=0.0)
    assert trimmed.size == rate


def test_trim_handles_empty_input():
    assert trim_silence(np.array([], dtype=np.int16), 22050).size == 0
