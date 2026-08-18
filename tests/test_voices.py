import io
import threading
import urllib.error

import pytest

from aloud import voices


@pytest.fixture(autouse=True)
def isolated_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path / "roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    return tmp_path


SAMPLE_CATALOG = {
    "en_GB-alba-medium": {
        "language": {"code": "en_GB", "name_english": "English",
                     "country_english": "United Kingdom"},
        "quality": "medium",
        "num_speakers": 1,
        "files": {"en/en_GB/alba/medium/en_GB-alba-medium.onnx": {"size_bytes": 63201294},
                  "en/en_GB/alba/medium/en_GB-alba-medium.onnx.json": {"size_bytes": 4899}},
    },
    "de_DE-thorsten-low": {
        "language": {"code": "de_DE", "name_english": "German",
                     "country_english": "Germany"},
        "quality": "low",
        "num_speakers": 1,
        "files": {"de/de_DE/thorsten/low/de_DE-thorsten-low.onnx": {"size_bytes": 20000000}},
    },
    "en_GB-vctk-medium": {
        "language": {"code": "en_GB", "name_english": "English",
                     "country_english": "United Kingdom"},
        "quality": "medium",
        "num_speakers": 3,
        "speaker_id_map": {"p239": 1, "p225": 0, "p226": 2},
        "files": {"en/en_GB/vctk/medium/en_GB-vctk-medium.onnx": {"size_bytes": 75000000}},
    },
}


# -- URLs -------------------------------------------------------------------

def test_download_urls_follow_the_repository_layout():
    assert voices._file_url("en_US-amy-medium", ".onnx") == (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/amy/"
        "medium/en_US-amy-medium.onnx?download=true")


def test_multi_word_voice_names_are_handled():
    url = voices._file_url("en_GB-northern_english_male-medium", ".onnx.json")
    assert "/en/en_GB/northern_english_male/medium/" in url
    assert url.endswith("en_GB-northern_english_male-medium.onnx.json?download=true")


@pytest.mark.parametrize("bad", ["nonsense", "en_US-amy", "", "-a-b"])
def test_malformed_voice_names_are_rejected(bad):
    with pytest.raises(ValueError):
        voices._file_url(bad, ".onnx")


# -- Catalog ----------------------------------------------------------------

def test_catalog_entries_are_parsed():
    parsed = {voice.id: voice for voice in voices._parse_catalog(SAMPLE_CATALOG)}
    alba = parsed["en_GB-alba-medium"]
    assert alba.country == "United Kingdom"
    assert alba.quality == "medium"
    assert alba.size_bytes == 63201294
    assert alba.size_mb == pytest.approx(63.2, abs=0.1)
    assert alba.label == "Alba"
    assert alba.locale == "en_GB"


def test_speakers_are_ordered_by_their_model_index():
    parsed = {voice.id: voice for voice in voices._parse_catalog(SAMPLE_CATALOG)}
    assert parsed["en_GB-vctk-medium"].speakers == ["p225", "p239", "p226"]


def test_size_falls_back_to_an_estimate_when_absent():
    entry = {"x": {"language": {}, "quality": "high", "files": {}}}
    voice = voices._parse_catalog(entry)[0]
    assert voice.size_mb == pytest.approx(115, abs=1)


def test_curated_voices_sort_first_then_english():
    catalog = sorted(voices._parse_catalog(SAMPLE_CATALOG), key=voices._sort_key)
    assert catalog[0].id == "en_GB-alba-medium"      # curated
    assert catalog[-1].id == "de_DE-thorsten-low"    # not English, not curated


def test_offline_fetch_falls_back_to_the_curated_list(monkeypatch):
    def refuse(*args, **kwargs):
        raise urllib.error.URLError("no network")

    monkeypatch.setattr(voices.urllib.request, "urlopen", refuse)
    catalog = voices.fetch_catalog(force=True)
    assert catalog == voices.CURATED


def test_a_fetched_catalog_is_cached_and_reused(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        calls.append(request)
        import json
        return _FakeResponse(json.dumps(SAMPLE_CATALOG).encode("utf-8"))

    monkeypatch.setattr(voices.urllib.request, "urlopen", fake_urlopen)
    first = voices.fetch_catalog(force=True)
    second = voices.fetch_catalog()  # served from the cache

    assert len(calls) == 1
    assert [v.id for v in first] == [v.id for v in second]


# -- Installed voices --------------------------------------------------------

def _install(voice_id):
    directory = voices.voices_dir()
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{voice_id}.onnx").write_bytes(b"model")
    (directory / f"{voice_id}.onnx.json").write_text("{}")


def test_installed_detection_needs_both_files():
    assert not voices.is_installed("en_US-amy-medium")
    voices.voices_dir().mkdir(parents=True, exist_ok=True)
    (voices.voices_dir() / "en_US-amy-medium.onnx").write_bytes(b"model")
    # A model with no config is a half-finished download, not an installed voice.
    assert not voices.is_installed("en_US-amy-medium")

    (voices.voices_dir() / "en_US-amy-medium.onnx.json").write_text("{}")
    assert voices.is_installed("en_US-amy-medium")
    assert voices.installed_ids() == ["en_US-amy-medium"]


def test_deleting_removes_both_files():
    _install("en_GB-alba-medium")
    voices.delete_voice("en_GB-alba-medium")
    assert not voices.is_installed("en_GB-alba-medium")
    assert voices.installed_ids() == []


def test_deleting_a_voice_that_is_not_there_is_harmless():
    voices.delete_voice("en_US-nobody-medium")


# -- Downloading -------------------------------------------------------------

class _FakeResponse(io.BytesIO):
    def __init__(self, payload):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
        return False


def test_download_writes_both_files_and_reports_progress(monkeypatch):
    payloads = {".onnx": b"m" * 5000, ".onnx.json": b"{}"}

    def fake_urlopen(request, timeout=None):
        extension = ".onnx.json" if request.full_url.split("?")[0].endswith(".json") else ".onnx"
        return _FakeResponse(payloads[extension])

    monkeypatch.setattr(voices.urllib.request, "urlopen", fake_urlopen)

    seen = []
    voices.download_voice("en_US-amy-medium", progress=lambda done, total: seen.append(done))

    assert voices.is_installed("en_US-amy-medium")
    assert (voices.voices_dir() / "en_US-amy-medium.onnx").read_bytes() == payloads[".onnx"]
    assert seen and seen[-1] >= 5000


def test_a_failed_download_leaves_no_partial_file(monkeypatch):
    def fake_urlopen(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr(voices.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(RuntimeError, match="no voice called"):
        voices.download_voice("en_US-imaginary-medium")

    assert not voices.is_installed("en_US-imaginary-medium")
    assert list(voices.voices_dir().glob("*.part")) == []


def test_a_cancelled_download_cleans_up(monkeypatch):
    cancel = threading.Event()

    class SlowResponse(_FakeResponse):
        def read(self, size=-1):
            cancel.set()  # cancel arrives partway through the transfer
            return super().read(size)

    monkeypatch.setattr(voices.urllib.request, "urlopen",
                        lambda request, timeout=None: SlowResponse(b"m" * 500000))

    with pytest.raises(voices.DownloadCancelled):
        voices.download_voice("en_US-amy-medium", cancel=cancel)

    assert not voices.is_installed("en_US-amy-medium")
    assert list(voices.voices_dir().glob("*.part")) == []


def test_an_already_installed_voice_is_not_downloaded_again(monkeypatch):
    _install("en_US-amy-medium")

    def explode(*args, **kwargs):
        raise AssertionError("should not have hit the network")

    monkeypatch.setattr(voices.urllib.request, "urlopen", explode)
    voices.download_voice("en_US-amy-medium")
