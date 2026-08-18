"""The interface every speech engine implements."""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Iterator, List, Tuple

import numpy as np


class EngineError(RuntimeError):
    """Raised when an engine cannot speak: no voice, missing model, etc."""


@dataclass
class VoiceInfo:
    """One selectable voice, as shown in the voice list."""

    id: str
    label: str
    language: str = ""
    quality: str = ""
    speakers: List[str] = field(default_factory=list)

    @property
    def is_multi_speaker(self) -> bool:
        return len(self.speakers) > 1


# A chunk of audio and the rate it should be played at. Mono int16 throughout:
# it is what Piper produces, what WAV files hold, and what the output stream
# wants, so nothing has to convert.
AudioChunk = Tuple[np.ndarray, int]


class SpeechEngine(ABC):
    name = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """Whether this engine can run here (dependencies present, etc.)."""

    @abstractmethod
    def list_voices(self) -> List[VoiceInfo]:
        """Voices ready to use right now, without downloading anything."""

    @abstractmethod
    def synthesize(self, text: str, settings, cancel: threading.Event) -> Iterator[AudioChunk]:
        """Yield audio for `text`, a sentence or so at a time.

        Yielding progressively is what lets playback start before a long
        passage has finished rendering. Implementations should check `cancel`
        between chunks and return promptly when it is set.
        """

    def unavailable_reason(self) -> str:
        """Human-readable explanation for why `is_available` is False."""
        return ""
