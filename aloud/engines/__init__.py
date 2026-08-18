"""Speech engines.

Each engine turns text plus a `VoiceSettings` into a stream of PCM chunks.
Aloud ships with two: Piper (local neural voices, the good one) and SAPI (the
voices already on the machine, so the app can talk on first run while Piper's
models are still downloading).
"""

from .base import EngineError, SpeechEngine, VoiceInfo
from .piper_engine import PiperEngine
from .sapi_engine import SapiEngine

__all__ = ["EngineError", "SpeechEngine", "VoiceInfo", "PiperEngine", "SapiEngine",
           "get_engine", "available_engines"]

_ENGINES = {}


def get_engine(name: str) -> SpeechEngine:
    """Return the shared instance of an engine, creating it on first use."""
    if name not in _ENGINES:
        if name == "piper":
            _ENGINES[name] = PiperEngine()
        elif name == "sapi":
            _ENGINES[name] = SapiEngine()
        else:
            raise EngineError(f"unknown engine: {name}")
    return _ENGINES[name]


def available_engines() -> list:
    """Names of the engines that can actually run on this machine."""
    return [name for name in ("piper", "sapi") if get_engine(name).is_available()]
