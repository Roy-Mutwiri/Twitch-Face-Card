"""Updater: version logic, release parsing, checksum lines, offline behaviour (no network)."""

import io
import json

from faceframe import updater


def test_version_compare():
    assert updater.is_newer("v1.2.0", "1.1.0")
    assert updater.is_newer("1.10.0", "1.9.9")
    assert updater.is_newer("2", "1.9")
    assert not updater.is_newer("1.1.0", "1.1.0")
    assert not updater.is_newer("v1.0.9", "1.1.0")
    assert not updater.is_newer("garbage", "1.1.0")


def _release(**kw):
    d = {"tag_name": "v9.9.0", "body": "notes", "html_url": "https://x/rel", "draft": False, "prerelease": False,
         "assets": [{"name": "FaceFrame-Setup.exe", "browser_download_url": "https://x/setup.exe", "size": 123},
                    {"name": "SHA256SUMS.txt", "browser_download_url": "https://x/sums"}]}
    d.update(kw)
    return d


def test_parse_release():
    i = updater.parse_release(_release())
    assert i.version == "9.9.0" and i.url.endswith("setup.exe") and i.sums_url.endswith("sums") and i.size == 123
    assert updater.parse_release(_release(prerelease=True)) is None
    assert updater.parse_release(_release(assets=[])) is None


def test_expected_sha256():
    h = "ab" * 32
    txt = f"{h} FaceFrame-Setup.exe\n{'cd' * 32} FaceFrame-Setup-9.9.0.exe\n"
    assert updater.expected_sha256(txt) == h
    assert updater.expected_sha256(f"{h} *FaceFrame-Setup.exe") == h
    assert updater.expected_sha256("nothing here") is None


def test_check_offers_only_newer_and_never_raises(monkeypatch):
    def fake(req, timeout=0):
        return io.BytesIO(json.dumps(_release()).encode())
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake)
    assert updater.check("1.2.0").version == "9.9.0"
    assert updater.check("9.9.0") is None

    def offline(req, timeout=0):
        raise OSError("no network")
    monkeypatch.setattr(updater.urllib.request, "urlopen", offline)
    assert updater.check("1.2.0") is None


def test_download_rejects_bad_checksum(monkeypatch, tmp_path):
    import pytest
    payload = b"x" * 5000
    sums = f"{'00' * 32} FaceFrame-Setup.exe".encode()

    class Resp(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake(req, timeout=0):
        return Resp(sums if req.full_url.endswith("sums") else payload)
    monkeypatch.setattr(updater.urllib.request, "urlopen", fake)
    monkeypatch.setattr(updater.tempfile, "gettempdir", lambda: str(tmp_path))
    info = updater.parse_release(_release())
    with pytest.raises(RuntimeError, match="checksum"):
        updater.download(info)
    import hashlib
    sums = f"{hashlib.sha256(payload).hexdigest()} FaceFrame-Setup.exe".encode()
    p = updater.download(info)
    assert p.read_bytes() == payload
