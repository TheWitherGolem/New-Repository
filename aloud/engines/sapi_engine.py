"""SAPI: the voices Windows already has.

This is the fallback so that Aloud can speak the moment it starts, before any
Piper model has been downloaded. It drives System.Speech through PowerShell,
which needs nothing installed - no pywin32, no COM bindings.

Each block of text is rendered to a temporary WAV and then streamed through the
same pipeline as Piper's output, so speed, pitch, volume and pauses all behave
identically whichever engine is selected.
"""

from __future__ import annotations

import logging
import math
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Iterator, List, Optional

import numpy as np

from ..textproc import split_blocks
from .base import AudioChunk, EngineError, SpeechEngine, VoiceInfo

_LOGGER = logging.getLogger(__name__)

# Long enough that PowerShell's startup cost is amortised, short enough that
# stopping mid-passage still feels immediate.
_BLOCK_CHARS = 600
_SAMPLE_RATE = 22050
# Windows-only flag that keeps a console window from flashing up.
_NO_WINDOW = 0x08000000

_LIST_SCRIPT = """
Add-Type -AssemblyName System.Speech
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
foreach ($voice in $synth.GetInstalledVoices()) {
  if ($voice.Enabled) {
    $info = $voice.VoiceInfo
    Write-Output ("{0}`t{1}`t{2}" -f $info.Name, $info.Culture.Name, $info.Gender)
  }
}
$synth.Dispose()
"""

_SPEAK_SCRIPT = """
param([string]$TextFile, [string]$WavFile, [string]$VoiceName, [int]$Rate)
Add-Type -AssemblyName System.Speech
$text = [System.IO.File]::ReadAllText($TextFile, [System.Text.Encoding]::UTF8)
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
if ($VoiceName) { try { $synth.SelectVoice($VoiceName) } catch { } }
$synth.Rate = $Rate
$synth.Volume = 100
$format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
  %(rate)d,
  [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
  [System.Speech.AudioFormat.AudioChannel]::Mono)
$synth.SetOutputToWaveFile($WavFile, $format)
$synth.Speak($text)
$synth.Dispose()
""" % {"rate": _SAMPLE_RATE}


class SapiEngine(SpeechEngine):
    name = "sapi"

    def __init__(self) -> None:
        self._voices: Optional[List[VoiceInfo]] = None
        self._script_dir: Optional[Path] = None
        self._lock = threading.Lock()

    # -- availability --------------------------------------------------------

    def is_available(self) -> bool:
        import sys

        return sys.platform == "win32"

    def unavailable_reason(self) -> str:
        return "Windows speech voices are only available on Windows"

    def _powershell(self, script: Path, *args: str, timeout: float = 60.0):
        command = ["powershell", "-NoProfile", "-NonInteractive",
                   "-ExecutionPolicy", "Bypass", "-File", str(script), *args]
        return subprocess.run(command, capture_output=True, text=True,
                              timeout=timeout, creationflags=_NO_WINDOW)

    def _scripts(self) -> Path:
        """Write the helper scripts to a temp dir once per run."""
        with self._lock:
            if self._script_dir is None:
                directory = Path(tempfile.mkdtemp(prefix="aloud-sapi-"))
                (directory / "list.ps1").write_text(_LIST_SCRIPT, encoding="utf-8")
                (directory / "speak.ps1").write_text(_SPEAK_SCRIPT, encoding="utf-8")
                self._script_dir = directory
            return self._script_dir

    # -- voices --------------------------------------------------------------

    def list_voices(self) -> List[VoiceInfo]:
        if self._voices is not None:
            return self._voices
        if not self.is_available():
            self._voices = []
            return self._voices

        voices: List[VoiceInfo] = []
        try:
            result = self._powershell(self._scripts() / "list.ps1", timeout=30.0)
            for line in result.stdout.splitlines():
                parts = line.strip().split("\t")
                if not parts or not parts[0]:
                    continue
                name = parts[0]
                culture = parts[1] if len(parts) > 1 else ""
                gender = parts[2] if len(parts) > 2 else ""
                label = f"{name} ({culture})" if culture else name
                voices.append(VoiceInfo(id=name, label=label, language=culture,
                                        quality=gender))
        except Exception:
            _LOGGER.warning("Could not list Windows voices", exc_info=True)

        self._voices = voices
        return voices

    # -- synthesis -----------------------------------------------------------

    def synthesize(self, text: str, settings, cancel: threading.Event) -> Iterator[AudioChunk]:
        if not self.is_available():
            raise EngineError(self.unavailable_reason())

        voice_name = settings.sapi_voice or ""
        if not voice_name:
            installed = self.list_voices()
            if not installed:
                raise EngineError("No Windows speech voices are installed.")
            voice_name = installed[0].id

        script = self._scripts() / "speak.ps1"
        rate = _sapi_rate(settings.effective_speed)

        with tempfile.TemporaryDirectory(prefix="aloud-speak-") as workdir:
            work = Path(workdir)
            for index, block in enumerate(split_blocks(text, _BLOCK_CHARS)):
                if cancel.is_set():
                    return

                text_file = work / f"block{index}.txt"
                wav_file = work / f"block{index}.wav"
                text_file.write_text(block, encoding="utf-8")

                if not self._render(script, text_file, wav_file, voice_name, rate, cancel):
                    return
                if cancel.is_set():
                    return

                audio = _read_wav(wav_file)
                if audio is not None and audio[0].size:
                    yield audio

    def _render(self, script: Path, text_file: Path, wav_file: Path,
                voice_name: str, rate: int, cancel: threading.Event) -> bool:
        """Run PowerShell to produce one WAV. False if cancelled or failed."""
        command = ["powershell", "-NoProfile", "-NonInteractive",
                   "-ExecutionPolicy", "Bypass", "-File", str(script),
                   "-TextFile", str(text_file), "-WavFile", str(wav_file),
                   "-VoiceName", voice_name, "-Rate", str(rate)]
        process = subprocess.Popen(command, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
        try:
            while True:
                try:
                    _, stderr = process.communicate(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    if cancel.is_set():
                        # Stopping should be immediate, so do not wait for the
                        # rest of the block to render.
                        process.kill()
                        process.wait(timeout=2.0)
                        return False
        except Exception:
            _LOGGER.exception("Windows speech synthesis failed")
            return False

        if process.returncode != 0:
            message = (stderr or b"").decode("utf-8", "replace").strip()
            raise EngineError(f"Windows speech synthesis failed: {message[:300]}")
        return True


def _sapi_rate(speed: float) -> int:
    """Map a speed multiplier onto SAPI's -10..10 rate scale.

    SAPI's scale is roughly exponential - about a threefold change at each
    end - so a log fit tracks the requested multiplier far better than a
    linear one does.
    """
    speed = max(0.1, float(speed))
    return int(round(max(-10.0, min(10.0, 10.0 * math.log(speed, 3.0)))))


def _read_wav(path: Path) -> Optional[AudioChunk]:
    """Load a WAV as mono int16, whatever the writer chose to produce."""
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            width = handle.getsampwidth()
            rate = handle.getframerate()
            frames = handle.readframes(handle.getnframes())
    except (OSError, wave.Error):
        _LOGGER.warning("Could not read rendered audio %s", path, exc_info=True)
        return None

    if width == 2:
        audio = np.frombuffer(frames, dtype=np.int16)
    elif width == 1:
        # 8-bit WAV is unsigned, centred on 128.
        audio = (np.frombuffer(frames, dtype=np.uint8).astype(np.int16) - 128) * 256
    else:
        _LOGGER.warning("Unsupported sample width %s in %s", width, path)
        return None

    if channels > 1:
        usable = (audio.size // channels) * channels
        audio = audio[:usable].reshape(-1, channels).mean(axis=1).astype(np.int16)

    return audio.copy(), rate
