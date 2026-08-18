"""End-to-end tests for the speech pipeline, with the sound card faked out.

`sounddevice` is stubbed into sys.modules so the player's queueing, stop and
pause behaviour can be exercised on a machine with no audio hardware.
"""

import sys
import threading
import time
import types
import wave
from pathlib import Path

import numpy as np
import pytest


# --------------------------------------------------------------------------
# Fakes
# --------------------------------------------------------------------------

# A real output stream blocks in write() until the device has room, which is
# what paces playback. The fake does the same, sped up so tests stay quick.
TIME_SCALE = 50


class FakeStream:
    def __init__(self, samplerate, channels, dtype):
        self.samplerate = samplerate
        self.channels = channels
        self.written = []
        self.stopped = True
        self.closed = False
        self.aborts = 0
        self._lock = threading.Lock()

    def start(self):
        self.stopped = False

    def stop(self):
        self.stopped = True

    def abort(self):
        self.aborts += 1
        self.stopped = True

    def close(self):
        self.closed = True
        self.stopped = True

    def write(self, data):
        block = np.asarray(data).reshape(-1).copy()
        time.sleep(block.size / self.samplerate / TIME_SCALE)
        with self._lock:
            self.written.append(block)

    @property
    def frames(self):
        with self._lock:
            return int(sum(block.size for block in self.written))


@pytest.fixture
def fake_sounddevice(monkeypatch):
    module = types.ModuleType("sounddevice")
    created = []

    def output_stream(samplerate, channels, dtype):
        stream = FakeStream(samplerate, channels, dtype)
        created.append(stream)
        return stream

    module.OutputStream = output_stream
    module.streams = created
    monkeypatch.setitem(sys.modules, "sounddevice", module)
    return module


class FakeEngine:
    """Yields a fixed number of one-second chunks of constant-amplitude audio."""

    name = "fake"

    def __init__(self, chunks=3, rate=22050, amplitude=1000, delay=0.0):
        self.chunks = chunks
        self.rate = rate
        self.amplitude = amplitude
        self.delay = delay
        self.calls = []
        self.produced = 0

    def is_available(self):
        return True

    def list_voices(self):
        return []

    def synthesize(self, text, settings, cancel):
        self.calls.append((text, settings))
        for _ in range(self.chunks):
            if cancel.is_set():
                return
            if self.delay:
                time.sleep(self.delay)
            self.produced += 1
            yield (np.full(self.rate, self.amplitude, dtype=np.int16), self.rate)


@pytest.fixture
def register_fake_engine(monkeypatch):
    """Make "fake" a first-class engine name for the duration of a test."""
    from aloud import config, engines

    monkeypatch.setattr(config, "VALID_ENGINES", config.VALID_ENGINES | {"fake"})

    def register(engine):
        monkeypatch.setitem(engines._ENGINES, "fake", engine)
        return engine

    return register


def wait_for(predicate, timeout=5.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# --------------------------------------------------------------------------
# Player
# --------------------------------------------------------------------------

def test_player_writes_submitted_audio(fake_sounddevice):
    from aloud.audio import Player

    player = Player()
    try:
        generation = player.begin()
        player.submit(generation, np.full(4410, 500, dtype=np.int16), 22050)
        player.end_of_utterance(generation)

        assert wait_for(lambda: fake_sounddevice.streams
                        and fake_sounddevice.streams[0].frames == 4410)
        assert wait_for(lambda: not player.is_playing)
    finally:
        player.close()


def test_stop_discards_queued_audio(fake_sounddevice):
    from aloud.audio import Player

    player = Player()
    try:
        generation = player.begin()
        for _ in range(20):
            player.submit(generation, np.full(22050, 500, dtype=np.int16), 22050)

        assert wait_for(lambda: fake_sounddevice.streams
                        and fake_sounddevice.streams[0].frames > 0)
        player.stop()

        stream = fake_sounddevice.streams[0]
        assert stream.aborts >= 1
        assert player.pending() == 0

        # Nothing more should be written after the stop settles.
        time.sleep(0.15)
        settled = stream.frames
        time.sleep(0.15)
        assert stream.frames == settled
        assert stream.frames < 20 * 22050
    finally:
        player.close()


def test_audio_from_a_stopped_utterance_is_never_played(fake_sounddevice):
    from aloud.audio import Player

    player = Player()
    try:
        stale = player.begin()
        player.stop()
        player.submit(stale, np.full(22050, 500, dtype=np.int16), 22050)

        time.sleep(0.2)
        assert not fake_sounddevice.streams or fake_sounddevice.streams[0].frames == 0
    finally:
        player.close()


def test_pause_holds_playback_and_resume_continues_it(fake_sounddevice):
    from aloud.audio import Player

    player = Player()
    try:
        generation = player.begin()
        # Twenty seconds of audio, so there is plenty left to resume into.
        player.submit(generation, np.full(20 * 22050, 500, dtype=np.int16), 22050)

        assert wait_for(lambda: fake_sounddevice.streams
                        and fake_sounddevice.streams[0].frames > 0)
        player.pause()
        assert player.is_paused

        time.sleep(0.15)
        held = fake_sounddevice.streams[0].frames
        time.sleep(0.15)
        assert fake_sounddevice.streams[0].frames == held

        player.resume()
        assert not player.is_paused
        assert wait_for(lambda: fake_sounddevice.streams[0].frames > held)
    finally:
        player.close()


def test_a_new_utterance_replaces_the_previous_one(fake_sounddevice):
    from aloud.audio import Player

    player = Player()
    try:
        first = player.begin()
        for _ in range(10):
            player.submit(first, np.full(22050, 100, dtype=np.int16), 22050)
        second = player.begin()

        assert not player.is_current(first)
        assert player.is_current(second)
        assert player.pending() == 0
    finally:
        player.close()


# --------------------------------------------------------------------------
# SpeechService
# --------------------------------------------------------------------------

def test_speech_applies_gain_pitch_and_sentence_pauses(fake_sounddevice,
                                                       register_fake_engine):
    from aloud.config import VoiceSettings
    from aloud.speech import SpeechService

    engine = register_fake_engine(FakeEngine(chunks=2, amplitude=1000))
    settings = VoiceSettings(engine="fake", volume=1.5, pitch=1.25,
                             sentence_pause=0.5)

    service = SpeechService()
    try:
        assert service.speak("Hello there. Goodbye.", settings)
        stream_ready = wait_for(lambda: bool(fake_sounddevice.streams))
        assert stream_ready
        # Two chunks resampled to 1/1.25 of a second, each followed by half a
        # second of silence.
        expected = 2 * (round(22050 / 1.25) + 11025)
        assert wait_for(lambda: fake_sounddevice.streams[0].frames == expected,
                        timeout=10)

        played = np.concatenate(fake_sounddevice.streams[0].written)
        assert played.max() == 1500  # 1000 * 1.5 gain
        # Two half-second pauses, plus the handful of zeros the anti-click
        # fade puts at the end of each chunk.
        assert (played == 0).sum() == pytest.approx(22050, abs=8)
    finally:
        service.close()


def test_speaking_again_cancels_the_previous_synthesis(fake_sounddevice,
                                                       register_fake_engine):
    from aloud.config import VoiceSettings
    from aloud.speech import SpeechService

    engine = register_fake_engine(FakeEngine(chunks=50, delay=0.02))
    settings = VoiceSettings(engine="fake", sentence_pause=0)

    service = SpeechService()
    try:
        service.speak("First passage.", settings)
        assert wait_for(lambda: bool(fake_sounddevice.streams))
        service.speak("Second passage.", settings)

        assert wait_for(lambda: len(engine.calls) == 2)
        # The first pass was abandoned well before its fiftieth chunk.
        time.sleep(0.3)
        assert engine.produced < 50
    finally:
        service.close()


def test_stop_cancels_synthesis(fake_sounddevice, register_fake_engine):
    from aloud.config import VoiceSettings
    from aloud.speech import SpeechService

    engine = register_fake_engine(FakeEngine(chunks=50, delay=0.02))
    service = SpeechService()
    try:
        service.speak("A long passage.", VoiceSettings(engine="fake"))
        assert wait_for(lambda: bool(fake_sounddevice.streams))
        service.stop()

        time.sleep(0.2)
        settled = engine.produced
        time.sleep(0.3)
        assert engine.produced == settled  # synthesis really stopped
        assert settled < 50
    finally:
        service.close()


def test_empty_text_is_not_spoken(fake_sounddevice, register_fake_engine):
    from aloud.config import VoiceSettings
    from aloud.speech import SpeechService

    register_fake_engine(FakeEngine())
    service = SpeechService()
    try:
        assert service.speak("   \n  ", VoiceSettings(engine="fake")) is False
    finally:
        service.close()


def test_engine_errors_are_reported_not_raised(fake_sounddevice, register_fake_engine):
    from aloud.config import VoiceSettings
    from aloud.engines import EngineError
    from aloud.speech import SpeechService

    class BrokenEngine(FakeEngine):
        def synthesize(self, text, settings, cancel):
            raise EngineError("no voice installed")
            yield  # pragma: no cover - makes this a generator

    register_fake_engine(BrokenEngine())
    errors = []
    service = SpeechService(on_error=errors.append)
    try:
        service.speak("Hello.", VoiceSettings(engine="fake"))
        assert wait_for(lambda: errors == ["no voice installed"])
    finally:
        service.close()


def test_render_to_wav_writes_a_playable_file(tmp_path, fake_sounddevice,
                                              register_fake_engine):
    from aloud.config import VoiceSettings
    from aloud.speech import SpeechService

    register_fake_engine(FakeEngine(chunks=2))
    settings = VoiceSettings(engine="fake", sentence_pause=0.25, pitch=1.0)

    service = SpeechService()
    try:
        target = tmp_path / "out.wav"
        service.render_to_wav("Hello there. Goodbye.", settings, target)

        with wave.open(str(target), "rb") as handle:
            assert handle.getnchannels() == 1
            assert handle.getsampwidth() == 2
            assert handle.getframerate() == 22050
            assert handle.getnframes() == 2 * (22050 + 5512)
    finally:
        service.close()
