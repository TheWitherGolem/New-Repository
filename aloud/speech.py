"""The speaking pipeline.

Synthesis runs on its own thread and feeds the player as each sentence is
rendered, so a long passage starts talking almost immediately instead of after
the whole thing has been generated. Pitch and volume are applied here rather
than inside an engine, which keeps every engine's output consistent.
"""

from __future__ import annotations

import logging
import threading
import time
import wave
from pathlib import Path
from typing import Callable, Optional

from . import dsp
from .audio import Player
from .config import VoiceSettings
from .engines import EngineError, get_engine
from .textproc import clean_text

_LOGGER = logging.getLogger(__name__)

# Stop synthesising further ahead than this many queued blocks; a long article
# would otherwise render entirely into memory before a word was spoken.
_MAX_QUEUED_BLOCKS = 32


class SpeechService:
    """Speaks text, one utterance at a time."""

    def __init__(self,
                 on_state: Optional[Callable[[str], None]] = None,
                 on_error: Optional[Callable[[str], None]] = None) -> None:
        self._on_state = on_state
        self._on_error = on_error
        self._player = Player(on_state_change=self._handle_player_state)
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # -- state ---------------------------------------------------------------

    @property
    def is_playing(self) -> bool:
        return self._player.is_playing

    @property
    def is_paused(self) -> bool:
        return self._player.is_paused

    def _handle_player_state(self, state: str) -> None:
        if state.startswith("error:") and self._on_error:
            self._on_error(state[len("error:"):])
        elif self._on_state:
            self._on_state(state)

    def _report_error(self, message: str) -> None:
        _LOGGER.error("%s", message)
        if self._on_error:
            self._on_error(message)

    # -- transport -----------------------------------------------------------

    def speak(self, text: str, settings: VoiceSettings) -> bool:
        """Speak `text`, replacing anything currently being spoken."""
        text = clean_text(text)
        if not text:
            return False

        with self._lock:
            self._cancel.set()          # tell the previous synth thread to stop
            cancel = threading.Event()
            self._cancel = cancel
            generation = self._player.begin()

            self._thread = threading.Thread(
                target=self._synthesize,
                args=(generation, text, settings.copy(), cancel),
                name="aloud-synth", daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        with self._lock:
            self._cancel.set()
        self._player.stop()

    def toggle_pause(self) -> None:
        self._player.toggle_pause()

    def close(self) -> None:
        self.stop()
        self._player.close()

    # -- synthesis -----------------------------------------------------------

    def _synthesize(self, generation: int, text: str, settings: VoiceSettings,
                    cancel: threading.Event) -> None:
        stream = None
        try:
            engine = get_engine(settings.engine)
            produced = False

            # Held in a name so it can be closed deterministically below:
            # an engine's generator may own a subprocess or a temp directory.
            stream = engine.synthesize(text, settings, cancel)

            for audio, sample_rate in stream:
                if cancel.is_set() or not self._player.is_current(generation):
                    return

                # Trim first: the padding a synthesiser leaves around a
                # sentence would otherwise add to the gap the user asked for.
                audio = dsp.trim_silence(audio, sample_rate)
                # Pitch shifting resamples, which is why the engine was asked
                # to synthesise at a compensating speed.
                audio = dsp.resample(audio, settings.pitch)
                audio = dsp.apply_gain(audio, settings.volume)
                if audio.size == 0:
                    continue  # a chunk that was silence all the way through
                self._player.submit(generation, audio, sample_rate)
                produced = True

                if settings.sentence_pause > 0:
                    self._player.submit(
                        generation,
                        dsp.silence(settings.sentence_pause, sample_rate),
                        sample_rate)

                self._throttle(generation, cancel)

            if produced and not cancel.is_set():
                self._player.end_of_utterance(generation)
            elif not produced and not cancel.is_set():
                self._report_error("The synthesiser produced no audio for that text.")

        except EngineError as error:
            self._player.stop()
            self._report_error(str(error))
        except Exception as error:
            self._player.stop()
            _LOGGER.exception("Synthesis failed")
            self._report_error(f"Synthesis failed: {error}")
        finally:
            if stream is not None and hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    _LOGGER.debug("Could not close the synthesis stream", exc_info=True)

    def _throttle(self, generation: int, cancel: threading.Event) -> None:
        """Wait for the player to catch up before rendering further ahead."""
        while (self._player.pending() > _MAX_QUEUED_BLOCKS
               and not cancel.is_set()
               and self._player.is_current(generation)):
            time.sleep(0.05)

    # -- export --------------------------------------------------------------

    def render_to_wav(self, text: str, settings: VoiceSettings, path: Path,
                      cancel: Optional[threading.Event] = None) -> Path:
        """Render text to a WAV file using the current settings.

        Runs on the calling thread and is independent of playback, so saving a
        file neither interrupts nor is interrupted by whatever is being spoken.
        """
        text = clean_text(text)
        if not text:
            raise ValueError("There is no text to save.")

        cancel = cancel or threading.Event()
        engine = get_engine(settings.engine)
        path = Path(path)

        writer: Optional[wave.Wave_write] = None
        try:
            for audio, sample_rate in engine.synthesize(text, settings, cancel):
                audio = dsp.trim_silence(audio, sample_rate)
                audio = dsp.resample(audio, settings.pitch)
                audio = dsp.apply_gain(audio, settings.volume)
                if audio.size == 0:
                    continue

                if writer is None:
                    writer = wave.open(str(path), "wb")
                    writer.setnchannels(1)
                    writer.setsampwidth(2)
                    writer.setframerate(sample_rate)

                writer.writeframes(audio.tobytes())
                if settings.sentence_pause > 0:
                    writer.writeframes(
                        dsp.silence(settings.sentence_pause, sample_rate).tobytes())
        finally:
            if writer is not None:
                writer.close()

        if writer is None:
            raise EngineError("The synthesiser produced no audio for that text.")
        return path
