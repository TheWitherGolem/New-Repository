"""Audio output.

A single long-lived thread owns the output stream and drains a queue of PCM
blocks. Utterances are tagged with a generation number: stopping bumps the
generation, so any audio still queued or half-written from the previous
utterance is dropped without having to coordinate with the synthesis thread.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Callable, Optional

import numpy as np

from .dsp import fade_out

_LOGGER = logging.getLogger(__name__)

# Write in short blocks so a stop takes effect within a few tens of
# milliseconds rather than at the end of a sentence.
_BLOCK_SECONDS = 0.05


class AudioError(RuntimeError):
    """Raised when audio output is unavailable."""


class _Item:
    __slots__ = ("generation", "audio", "sample_rate", "final")

    def __init__(self, generation: int, audio, sample_rate: int, final: bool = False):
        self.generation = generation
        self.audio = audio
        self.sample_rate = sample_rate
        self.final = final


class Player:
    """Plays mono int16 audio, with stop and pause."""

    def __init__(self, on_state_change: Optional[Callable[[str], None]] = None) -> None:
        self._queue: "queue.Queue[Optional[_Item]]" = queue.Queue()
        self._generation = 0
        self._lock = threading.Lock()
        self._paused = threading.Event()
        self._resumed = threading.Event()
        self._resumed.set()
        self._shutdown = threading.Event()
        self._stream = None
        self._stream_rate = 0
        self._active = False
        self._on_state_change = on_state_change
        self._thread = threading.Thread(target=self._run, name="aloud-audio", daemon=True)
        self._thread.start()

    # -- state ---------------------------------------------------------------

    @property
    def is_playing(self) -> bool:
        return self._active

    @property
    def is_paused(self) -> bool:
        return self._paused.is_set()

    def _set_active(self, active: bool) -> None:
        if active == self._active:
            return
        self._active = active
        self._notify("playing" if active else "idle")

    def _notify(self, state: str) -> None:
        if self._on_state_change:
            try:
                self._on_state_change(state)
            except Exception:
                _LOGGER.exception("Player state callback failed")

    # -- submitting audio ----------------------------------------------------

    def begin(self) -> int:
        """Start a new utterance, cancelling whatever was playing."""
        with self._lock:
            self._generation += 1
            generation = self._generation
        self._drain()
        self.resume()
        return generation

    def submit(self, generation: int, audio: np.ndarray, sample_rate: int) -> None:
        if audio is None or audio.size == 0:
            return
        self._queue.put(_Item(generation, audio, sample_rate))

    def end_of_utterance(self, generation: int) -> None:
        """Mark the end of an utterance so the idle state is reported."""
        self._queue.put(_Item(generation, None, 0, final=True))

    def is_current(self, generation: int) -> bool:
        return generation == self._generation

    def pending(self) -> int:
        """Blocks waiting to be played, so producers can throttle themselves."""
        return self._queue.qsize()

    # -- transport -----------------------------------------------------------

    def stop(self) -> None:
        """Silence output immediately and discard anything pending."""
        with self._lock:
            self._generation += 1
        self._drain()
        self.resume()
        stream = self._stream
        if stream is not None:
            try:
                # abort() discards what the device has buffered; stop() would
                # politely play it out, which is not what "stop" means here.
                stream.abort()
            except Exception:
                _LOGGER.debug("Could not abort the output stream", exc_info=True)
        self._set_active(False)

    def pause(self) -> None:
        if self._paused.is_set():
            return
        self._paused.set()
        self._resumed.clear()
        stream = self._stream
        if stream is not None:
            try:
                stream.stop()  # let the buffered tail play out, then hold
            except Exception:
                _LOGGER.debug("Could not stop the output stream", exc_info=True)
        self._notify("paused")

    def resume(self) -> None:
        if not self._paused.is_set():
            return
        self._paused.clear()
        self._resumed.set()
        self._notify("playing" if self._active else "idle")

    def toggle_pause(self) -> None:
        if self._paused.is_set():
            self.resume()
        elif self._active:
            self.pause()

    def close(self) -> None:
        self._shutdown.set()
        self.stop()
        self._queue.put(None)
        self._thread.join(timeout=2.0)
        self._close_stream()

    def _drain(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                return

    # -- the audio thread ----------------------------------------------------

    def _ensure_stream(self, sample_rate: int):
        import sounddevice as sd

        if self._stream is not None and self._stream_rate == sample_rate:
            if self._stream.stopped:
                self._stream.start()
            return self._stream

        self._close_stream()
        self._stream = sd.OutputStream(samplerate=sample_rate, channels=1,
                                       dtype="int16")
        self._stream_rate = sample_rate
        self._stream.start()
        return self._stream

    def _close_stream(self) -> None:
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                _LOGGER.debug("Could not close the output stream", exc_info=True)
        self._stream = None
        self._stream_rate = 0

    def _run(self) -> None:
        while not self._shutdown.is_set():
            item = self._queue.get()
            if item is None:
                break
            if not self.is_current(item.generation):
                continue  # left over from an utterance that has been stopped

            if item.final:
                if self.is_current(item.generation):
                    self._set_active(False)
                continue

            try:
                self._play(item)
            except Exception as error:
                _LOGGER.exception("Playback failed")
                self._set_active(False)
                self._notify(f"error:{error}")

    def _play(self, item: _Item) -> None:
        stream = self._ensure_stream(item.sample_rate)
        self._set_active(True)

        block = max(256, int(item.sample_rate * _BLOCK_SECONDS))
        audio = item.audio
        position = 0

        while position < audio.size:
            if not self.is_current(item.generation) or self._shutdown.is_set():
                return

            if self._paused.is_set():
                # Wait to be resumed, checking often enough that a stop while
                # paused is still noticed promptly.
                while self._paused.is_set():
                    if not self.is_current(item.generation) or self._shutdown.is_set():
                        return
                    self._resumed.wait(0.1)
                if stream.stopped:
                    stream.start()

            chunk = audio[position:position + block]
            position += block

            if not self.is_current(item.generation):
                return
            if position >= audio.size:
                # Soften the very end of a chunk boundary against clicks.
                chunk = fade_out(chunk, samples=min(64, chunk.size))
            stream.write(chunk.reshape(-1, 1))


def check_output_available() -> Optional[str]:
    """Return None if audio output works, else a description of the problem."""
    try:
        import sounddevice as sd
    except Exception as error:
        return (f"The sounddevice package is not available ({error}). "
                "Run install.ps1 to set the app up.")
    try:
        if sd.default.device[1] is None and not sd.query_devices():
            return "No audio output device was found."
    except Exception as error:
        return f"No usable audio output device: {error}"
    return None
