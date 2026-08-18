"""Settings model, persisted as JSON.

`VoiceSettings` holds everything that shapes how a piece of text sounds, so a
preset is just a saved copy of one. `Config` adds the app-level things:
hotkeys, which engine to use, window behaviour.
"""

from __future__ import annotations

import dataclasses
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict

from .paths import config_file, ensure_dirs

_LOGGER = logging.getLogger(__name__)

# Slider bounds, shared by the UI and by clamping on load so a hand-edited
# config file can never push the synthesiser into a nonsense state.
LIMITS: Dict[str, tuple] = {
    "speed": (0.5, 2.5),
    "pitch": (0.7, 1.4),
    "volume": (0.0, 2.0),
    "expressiveness": (0.0, 1.5),
    "cadence": (0.0, 2.0),
    "sentence_pause": (0.0, 2.0),
}


# How Piper's length_scale maps onto duration; see VoiceSettings.length_scale.
DURATION_EXPONENT = 0.8

# Engine names a config file may name. Anything else is treated as a corrupt
# value and reset, so a hand-edited or downgraded config cannot leave the app
# pointing at an engine that does not exist.
VALID_ENGINES = {"piper", "sapi"}


def clamp(name: str, value: float) -> float:
    low, high = LIMITS[name]
    return max(low, min(high, float(value)))


@dataclass
class VoiceSettings:
    """How text should sound. One of these is what a preset stores."""

    engine: str = "piper"          # "piper" or "sapi"
    voice: str = ""               # piper voice id, e.g. "en_GB-alba-medium"
    speaker: int = 0              # speaker index for multi-speaker models
    sapi_voice: str = ""          # Windows voice name, used when engine == "sapi"

    speed: float = 1.0            # 1.0 = the voice's natural pace
    pitch: float = 1.0            # 1.0 = unshifted
    volume: float = 1.0           # linear gain
    expressiveness: float = 0.667  # piper noise_scale: pitch/energy variation
    cadence: float = 0.8          # piper noise_w_scale: rhythm/timing variation
    sentence_pause: float = 0.35  # seconds of silence inserted between sentences

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "VoiceSettings":
        known = {f.name for f in dataclasses.fields(cls)}
        clean = {k: v for k, v in (data or {}).items() if k in known}
        settings = cls(**clean)
        settings.normalise()
        return settings

    def normalise(self) -> None:
        for name in LIMITS:
            setattr(self, name, clamp(name, getattr(self, name)))
        self.speaker = max(0, int(self.speaker))
        if self.engine not in VALID_ENGINES:
            self.engine = "piper"

    def copy(self) -> "VoiceSettings":
        return VoiceSettings.from_dict(self.to_dict())

    # -- derived values fed to the synthesiser -------------------------------

    @property
    def effective_speed(self) -> float:
        """The pace to synthesise at, before pitch shifting.

        Pitch is applied by resampling the finished audio, which also changes
        its duration. Synthesising `pitch` times slower cancels that out, so
        the result lands back at the speed the user actually asked for.
        """
        return self.speed / self.pitch

    @property
    def length_scale(self) -> float:
        """Piper's duration multiplier, calibrated so that speed means speed.

        Piper does not stretch time proportionally: it quantises each phoneme's
        duration to whole frames, which compresses the response at both ends of
        the range. Measured on a Piper voice across several passages, duration
        tracks `length_scale ** 0.7` rather than `length_scale ** 1`, so asking
        for double speed the naive way delivers about 1.5x.

        Raising the request to `1 / DURATION_EXPONENT` corrects for that. The
        exponent is deliberately set a little above the measured 0.7: models at
        higher sample rates quantise less, and undershooting their speed is a
        far smaller annoyance than overshooting it.
        """
        return (1.0 / self.effective_speed) ** (1.0 / DURATION_EXPONENT)


@dataclass
class Config:
    hotkey_speak: str = "ctrl+alt+s"
    hotkey_stop: str = "ctrl+alt+x"
    hotkey_pause: str = "ctrl+alt+p"
    hotkey_window: str = "ctrl+alt+space"

    voice_settings: VoiceSettings = field(default_factory=VoiceSettings)
    presets: Dict[str, Dict[str, Any]] = field(default_factory=dict)

    restore_clipboard: bool = True
    start_hidden: bool = True
    read_clipboard_if_no_selection: bool = True

    def to_dict(self) -> Dict[str, Any]:
        data = dataclasses.asdict(self)
        data["voice_settings"] = self.voice_settings.to_dict()
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Config":
        data = dict(data or {})
        voice = VoiceSettings.from_dict(data.pop("voice_settings", {}))
        known = {f.name for f in dataclasses.fields(cls)} - {"voice_settings"}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(voice_settings=voice, **clean)

    # -- persistence ---------------------------------------------------------

    @classmethod
    def load(cls) -> "Config":
        path = config_file()
        if not path.exists():
            return cls()
        try:
            with open(path, "r", encoding="utf-8") as handle:
                return cls.from_dict(json.load(handle))
        except Exception:
            # A corrupt config should never stop the app from starting; the
            # bad file is kept alongside for the curious.
            _LOGGER.exception("Could not read %s, falling back to defaults", path)
            try:
                path.replace(path.with_suffix(".json.bad"))
            except OSError:
                pass
            return cls()

    def save(self) -> None:
        ensure_dirs()
        path = config_file()
        tmp = path.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, indent=2)
        tmp.replace(path)
