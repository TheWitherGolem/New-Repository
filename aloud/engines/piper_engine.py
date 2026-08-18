"""Piper: local neural voices.

Models are ONNX files sitting in the voices directory, each with a JSON
sidecar. Loading one costs a second or so and a few tens of megabytes, so the
most recently used ones are kept around rather than reloaded per utterance.
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Iterator, List, Optional

import numpy as np

from ..paths import voices_dir
from .base import AudioChunk, EngineError, SpeechEngine, VoiceInfo

_LOGGER = logging.getLogger(__name__)

# How many loaded models to hold in memory at once.
_CACHE_SIZE = 2


class PiperEngine(SpeechEngine):
    name = "piper"

    def __init__(self) -> None:
        self._cache: "OrderedDict[str, object]" = OrderedDict()
        self._lock = threading.Lock()
        self._import_error: Optional[str] = None

    # -- availability --------------------------------------------------------

    def _piper(self):
        """Import piper lazily; it pulls in onnxruntime and is slow to load."""
        try:
            import piper  # noqa: F401  (imported for its side effect of loading)

            return piper
        except Exception as error:  # pragma: no cover - depends on install
            self._import_error = str(error)
            raise EngineError(
                "The piper-tts package is not installed. Run install.ps1, or "
                "'pip install piper-tts', then restart Aloud."
            ) from error

    def is_available(self) -> bool:
        try:
            self._piper()
            return True
        except EngineError:
            return False

    def unavailable_reason(self) -> str:
        return self._import_error or "piper-tts is not installed"

    # -- voices --------------------------------------------------------------

    def list_voices(self) -> List[VoiceInfo]:
        voices: List[VoiceInfo] = []
        directory = voices_dir()
        if not directory.exists():
            return voices

        for model in sorted(directory.glob("*.onnx")):
            config_path = model.with_suffix(".onnx.json")
            if not config_path.exists():
                # A half-finished download; ignore it rather than crash later.
                continue
            voices.append(self._describe(model, config_path))
        return voices

    def _describe(self, model: Path, config_path: Path) -> VoiceInfo:
        voice_id = model.stem
        language, quality, speakers = "", "", []
        try:
            with open(config_path, "r", encoding="utf-8") as handle:
                config = json.load(handle)
            language = (config.get("language", {}) or {}).get("name_english", "")
            quality = (config.get("audio", {}) or {}).get("quality", "")
            speaker_map = config.get("speaker_id_map") or {}
            if speaker_map:
                # Order by id so the index the user picks matches the model's.
                speakers = [name for name, _ in sorted(speaker_map.items(),
                                                       key=lambda item: item[1])]
            elif int(config.get("num_speakers", 1) or 1) > 1:
                speakers = [str(i) for i in range(int(config["num_speakers"]))]
        except Exception:
            _LOGGER.warning("Could not read voice config %s", config_path, exc_info=True)

        if not quality:
            parts = voice_id.split("-")
            quality = parts[-1] if len(parts) >= 3 else ""

        return VoiceInfo(id=voice_id, label=_pretty_label(voice_id), language=language,
                         quality=quality, speakers=speakers)

    def has_voices(self) -> bool:
        return bool(self.list_voices())

    # -- synthesis -----------------------------------------------------------

    def _load(self, voice_id: str):
        """Load a voice, reusing the cached instance when we already have it."""
        with self._lock:
            if voice_id in self._cache:
                self._cache.move_to_end(voice_id)
                return self._cache[voice_id]

            model = voices_dir() / f"{voice_id}.onnx"
            config_path = model.with_suffix(".onnx.json")
            if not model.exists() or not config_path.exists():
                raise EngineError(
                    f"Voice '{voice_id}' is not installed. Pick another voice or "
                    "download it from the Voices window.")

            piper = self._piper()
            _LOGGER.info("Loading Piper voice %s", voice_id)
            voice = piper.PiperVoice.load(str(model), config_path=str(config_path))

            self._cache[voice_id] = voice
            while len(self._cache) > _CACHE_SIZE:
                self._cache.popitem(last=False)
            return voice

    def synthesize(self, text: str, settings, cancel: threading.Event) -> Iterator[AudioChunk]:
        piper = self._piper()
        voice = self._load(settings.voice)

        speaker_id = None
        if getattr(voice.config, "num_speakers", 1) > 1:
            # Out-of-range ids make onnxruntime throw, so fold into range.
            speaker_id = int(settings.speaker) % int(voice.config.num_speakers)

        synthesis = piper.SynthesisConfig(
            speaker_id=speaker_id,
            length_scale=settings.length_scale,
            noise_scale=settings.expressiveness,
            noise_w_scale=settings.cadence,
            normalize_audio=True,
        )

        # Piper yields one chunk per sentence, which is exactly the granularity
        # at which a cancel should take effect.
        for chunk in voice.synthesize(text, syn_config=synthesis):
            if cancel.is_set():
                return
            audio = np.asarray(chunk.audio_int16_array, dtype=np.int16).reshape(-1)
            yield audio, int(chunk.sample_rate)


def _pretty_label(voice_id: str) -> str:
    """"en_GB-northern_english_male-medium" -> "Northern English Male (en_GB, medium)"."""
    parts = voice_id.split("-")
    if len(parts) < 2:
        return voice_id
    locale, name = parts[0], parts[1]
    quality = parts[2] if len(parts) > 2 else ""
    pretty = name.replace("_", " ").title()
    suffix = f" ({locale}, {quality})" if quality else f" ({locale})"
    return pretty + suffix
