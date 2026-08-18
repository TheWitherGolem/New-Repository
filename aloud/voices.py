"""Finding, downloading and removing Piper voices.

The authoritative list of voices lives in the piper-voices repository on
Hugging Face. It is fetched on demand and cached; a small curated list is
compiled in so the Voices window still has something to show when the machine
is offline or the fetch fails.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .paths import data_dir, ensure_dirs, voices_dir

_LOGGER = logging.getLogger(__name__)

_REPO = "https://huggingface.co/rhasspy/piper-voices/resolve/main"
_CATALOG_URL = f"{_REPO}/voices.json?download=true"
_FILE_URL = "{repo}/{family}/{locale}/{name}/{quality}/{locale}-{name}-{quality}{ext}?download=true"
_USER_AGENT = "Aloud/1.0 (+https://github.com/TheWitherGolem/New-Repository)"

# Re-fetch the catalog at most weekly; voices are added rarely.
_CATALOG_TTL = 7 * 24 * 3600
# Rough download sizes by quality, used when the catalog has no size for a file.
_APPROX_SIZE = {"x_low": 12_000_000, "low": 22_000_000,
                "medium": 65_000_000, "high": 115_000_000}

DownloadCancelled = type("DownloadCancelled", (Exception,), {})


@dataclass
class CatalogVoice:
    """A voice that can be downloaded, whether or not it is installed."""

    id: str
    language: str = ""
    country: str = ""
    quality: str = ""
    num_speakers: int = 1
    size_bytes: int = 0
    note: str = ""
    speakers: List[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        name = self.id.split("-")[1] if "-" in self.id else self.id
        return name.replace("_", " ").title()

    @property
    def locale(self) -> str:
        return self.id.split("-")[0]

    @property
    def size_mb(self) -> float:
        return (self.size_bytes or _APPROX_SIZE.get(self.quality, 65_000_000)) / 1e6

    @property
    def installed(self) -> bool:
        return is_installed(self.id)


# A hand-picked starting set, chosen to cover a spread of accents. This is only
# the offline fallback and the "recommended" ordering; the fetched catalog is
# always the complete and authoritative list.
CURATED: List[CatalogVoice] = [
    CatalogVoice("en_US-amy-medium", "English", "United States", "medium",
                 note="Clear American female. A good default."),
    CatalogVoice("en_US-ryan-high", "English", "United States", "high",
                 note="American male, the highest-quality US voice."),
    CatalogVoice("en_US-lessac-medium", "English", "United States", "medium",
                 note="Neutral American, very evenly paced."),
    CatalogVoice("en_US-hfc_female-medium", "English", "United States", "medium",
                 note="Warm American female."),
    CatalogVoice("en_US-joe-medium", "English", "United States", "medium",
                 note="Relaxed American male."),
    CatalogVoice("en_GB-alan-medium", "English", "United Kingdom", "medium",
                 note="Southern English male, RP-leaning."),
    CatalogVoice("en_GB-jenny_dioco-medium", "English", "United Kingdom", "medium",
                 note="Southern English female."),
    CatalogVoice("en_GB-alba-medium", "English", "United Kingdom", "medium",
                 note="Scottish female."),
    CatalogVoice("en_GB-northern_english_male-medium", "English", "United Kingdom",
                 "medium", note="Northern English male."),
    CatalogVoice("en_GB-cori-high", "English", "United Kingdom", "high",
                 note="British female, highest-quality UK voice."),
    CatalogVoice("en_GB-vctk-medium", "English", "United Kingdom", "medium",
                 num_speakers=109,
                 note="109 British speakers in one model - Scottish, Irish, "
                      "Welsh, Northern and Southern English. Switch accent with "
                      "the Speaker control."),
    CatalogVoice("en_US-libritts_r-medium", "English", "United States", "medium",
                 num_speakers=904,
                 note="904 American speakers in one model. Use the Speaker "
                      "control to browse them."),
]

_CURATED_ORDER = {voice.id: index for index, voice in enumerate(CURATED)}


# --------------------------------------------------------------------------
# Installed voices
# --------------------------------------------------------------------------

def is_installed(voice_id: str) -> bool:
    directory = voices_dir()
    return ((directory / f"{voice_id}.onnx").exists()
            and (directory / f"{voice_id}.onnx.json").exists())


def installed_ids() -> List[str]:
    directory = voices_dir()
    if not directory.exists():
        return []
    return sorted(model.stem for model in directory.glob("*.onnx")
                  if model.with_suffix(".onnx.json").exists())


def delete_voice(voice_id: str) -> None:
    """Remove an installed voice's files."""
    directory = voices_dir()
    for suffix in (".onnx", ".onnx.json"):
        path = directory / f"{voice_id}{suffix}"
        if path.exists():
            path.unlink()


# --------------------------------------------------------------------------
# Catalog
# --------------------------------------------------------------------------

def _catalog_cache() -> Path:
    return data_dir() / "catalog.json"


def _parse_catalog(raw: Dict) -> List[CatalogVoice]:
    voices: List[CatalogVoice] = []
    for voice_id, entry in raw.items():
        language = entry.get("language", {}) or {}
        speaker_map = entry.get("speaker_id_map") or {}
        speakers = [name for name, _ in sorted(speaker_map.items(),
                                               key=lambda item: item[1])]

        size = 0
        for path, info in (entry.get("files") or {}).items():
            if path.endswith(".onnx"):
                size = int((info or {}).get("size_bytes") or 0)
                break

        curated = next((c for c in CURATED if c.id == voice_id), None)
        voices.append(CatalogVoice(
            id=voice_id,
            language=language.get("name_english", "") or "",
            country=language.get("country_english", "") or "",
            quality=entry.get("quality", "") or "",
            num_speakers=int(entry.get("num_speakers", 1) or 1),
            size_bytes=size,
            note=curated.note if curated else "",
            speakers=speakers,
        ))
    return voices


def _sort_key(voice: CatalogVoice):
    """Curated voices first, then English, then everything else by name."""
    return (_CURATED_ORDER.get(voice.id, 999),
            0 if voice.locale.startswith("en") else 1,
            voice.locale, voice.id)


def fetch_catalog(force: bool = False, timeout: float = 20.0) -> List[CatalogVoice]:
    """The full downloadable voice list, from cache when it is fresh."""
    ensure_dirs()
    cache = _catalog_cache()

    if not force and cache.exists():
        age = time.time() - cache.stat().st_mtime
        if age < _CATALOG_TTL:
            try:
                with open(cache, "r", encoding="utf-8") as handle:
                    return sorted(_parse_catalog(json.load(handle)), key=_sort_key)
            except Exception:
                _LOGGER.warning("Cached voice catalog is unreadable", exc_info=True)

    try:
        request = urllib.request.Request(_CATALOG_URL, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = json.loads(response.read().decode("utf-8"))
        with open(cache, "w", encoding="utf-8") as handle:
            json.dump(raw, handle)
        return sorted(_parse_catalog(raw), key=_sort_key)
    except Exception as error:
        _LOGGER.warning("Could not fetch the voice catalog: %s", error)

    # Offline, or Hugging Face is unreachable. A stale cache still beats
    # nothing; failing that, fall back to the compiled-in list.
    if cache.exists():
        try:
            with open(cache, "r", encoding="utf-8") as handle:
                return sorted(_parse_catalog(json.load(handle)), key=_sort_key)
        except Exception:
            pass
    return list(CURATED)


# --------------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------------

def _file_url(voice_id: str, extension: str) -> str:
    """Build the download URL for one of a voice's two files."""
    locale, _, rest = voice_id.partition("-")
    name, _, quality = rest.partition("-")
    if not (locale and name and quality):
        raise ValueError(
            f"'{voice_id}' is not a valid voice name; expected something like "
            "'en_US-amy-medium'")
    family = locale.split("_")[0]
    return _FILE_URL.format(repo=_REPO, family=family, locale=locale,
                            name=name, quality=quality, ext=extension)


def download_voice(voice_id: str,
                   progress: Optional[Callable[[int, int], None]] = None,
                   cancel: Optional[threading.Event] = None,
                   timeout: float = 30.0) -> None:
    """Download a voice into the voices directory.

    `progress` is called with (bytes so far, total bytes) across both files
    combined. Downloads land on a `.part` file and are renamed into place only
    once complete, so an interrupted download can never leave a half-written
    model that would fail to load later.
    """
    ensure_dirs()
    directory = voices_dir()
    targets = [(directory / f"{voice_id}.onnx", _file_url(voice_id, ".onnx")),
               (directory / f"{voice_id}.onnx.json", _file_url(voice_id, ".onnx.json"))]

    done_bytes = 0
    # The model dwarfs the config, so its Content-Length is a good enough total
    # to drive a progress bar with.
    total_bytes = 0

    for path, url in targets:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled(f"Download of {voice_id} was cancelled")
        if path.exists() and path.stat().st_size > 0:
            continue

        part = path.with_suffix(path.suffix + ".part")
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = int(response.headers.get("Content-Length") or 0)
                total_bytes = max(total_bytes, done_bytes + length)
                with open(part, "wb") as handle:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled(
                                f"Download of {voice_id} was cancelled")
                        block = response.read(262144)
                        if not block:
                            break
                        handle.write(block)
                        done_bytes += len(block)
                        if progress:
                            progress(done_bytes, total_bytes)
        except urllib.error.HTTPError as error:
            part.unlink(missing_ok=True)
            if error.code == 404:
                raise RuntimeError(
                    f"There is no voice called '{voice_id}' in the Piper "
                    "collection. Refresh the list and try another.") from error
            raise RuntimeError(
                f"Downloading {voice_id} failed: {error}") from error
        except DownloadCancelled:
            part.unlink(missing_ok=True)
            raise
        except (urllib.error.URLError, OSError) as error:
            part.unlink(missing_ok=True)
            raise RuntimeError(
                f"Could not reach the voice server: {error}") from error

        part.replace(path)

    if progress and total_bytes:
        progress(total_bytes, total_bytes)
