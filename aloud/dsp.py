"""Small signal-processing helpers applied to synthesised audio."""

from __future__ import annotations

import numpy as np

_INT16_MAX = 32767
_INT16_MIN = -32768


def resample(audio: np.ndarray, factor: float) -> np.ndarray:
    """Resample by `factor` using linear interpolation.

    Played back at the original rate this shifts pitch by `factor` and scales
    duration by `1 / factor`. Aloud pairs it with a compensating change to the
    synthesiser's length scale, so what the listener hears is a pitch change at
    the speed they asked for.
    """
    if audio.size == 0 or abs(factor - 1.0) < 1e-3:
        return audio

    out_len = max(1, int(round(audio.size / factor)))
    # Map output sample positions back onto the input timeline.
    positions = np.linspace(0.0, audio.size - 1, out_len, dtype=np.float64)
    resampled = np.interp(positions, np.arange(audio.size), audio.astype(np.float64))
    return np.clip(np.rint(resampled), _INT16_MIN, _INT16_MAX).astype(np.int16)


def apply_gain(audio: np.ndarray, gain: float) -> np.ndarray:
    """Scale amplitude, clipping rather than wrapping on overflow."""
    if audio.size == 0 or abs(gain - 1.0) < 1e-3:
        return audio
    scaled = audio.astype(np.float32) * float(gain)
    return np.clip(scaled, _INT16_MIN, _INT16_MAX).astype(np.int16)


def silence(seconds: float, sample_rate: int) -> np.ndarray:
    """A block of quiet, used to pace the gaps between sentences."""
    count = max(0, int(round(seconds * sample_rate)))
    return np.zeros(count, dtype=np.int16)


# Below this amplitude (about 0.5% of full scale) audio is inaudible against
# any real-world background, so it counts as silence for trimming purposes.
_SILENCE_THRESHOLD = 164


def trim_silence(audio: np.ndarray, sample_rate: int,
                 margin_seconds: float = 0.02) -> np.ndarray:
    """Strip the near-silence a synthesiser leaves at the edges of a sentence.

    Piper pads every sentence with a little quiet, and how much varies with the
    speaking rate. Removing it is what makes the sentence-pause control mean
    what it says, instead of adding to a gap that was already there.

    A short margin is kept so consonants at the very start or end are not
    clipped.
    """
    if audio.size == 0:
        return audio

    loud = np.flatnonzero(np.abs(audio) > _SILENCE_THRESHOLD)
    if loud.size == 0:
        return audio[:0]  # nothing but silence

    margin = max(0, int(round(margin_seconds * sample_rate)))
    start = max(0, int(loud[0]) - margin)
    end = min(audio.size, int(loud[-1]) + margin + 1)
    return audio[start:end]


def fade_out(audio: np.ndarray, samples: int = 256) -> np.ndarray:
    """Taper the tail so an interrupted utterance ends without a click."""
    if audio.size == 0:
        return audio
    count = min(samples, audio.size)
    out = audio.astype(np.float32).copy()
    out[-count:] *= np.linspace(1.0, 0.0, count, dtype=np.float32)
    return out.astype(np.int16)
