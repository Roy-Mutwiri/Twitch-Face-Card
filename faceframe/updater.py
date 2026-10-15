"""In-app updates from GitHub Releases.

At start-up the installed app asks the GitHub API for the latest release. If its version
is newer, the UI offers "Update now" / "Skip" (skip = ask again next start). Updating
downloads the release's FaceFrame-Setup.exe, verifies it against SHA256SUMS.txt, and
hands over to a tiny detached helper that waits for FaceFrame to exit, runs the installer
silently into the same folder (settings in %APPDATA% are kept) and relaunches FaceFrame.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import tempfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("faceframe.update")

REPO = "Roy-Mutwiri/Twitch-Face-Card"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET = "FaceFrame-Setup.exe"
SUMS = "SHA256SUMS.txt"
UA = {"User-Agent": "FaceFrame-Updater", "Accept": "application/vnd.github+json"}


@dataclass
class UpdateInfo:
    version: str
    notes: str
    url: str
    size: int
    sums_url: str | None
    page: str


def parse_version(v: str) -> tuple[int, ...]:
    """'v1.2.0' / '1.2' / '1.2.0-beta' -> (1, 2, 0). Unparseable -> ()."""
    m = re.match(r"^\s*v?(\d+(?:\.\d+)*)", v or "")
    if not m:
        return ()
    parts = [int(x) for x in m.group(1).split(".")]
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return bool(a) and bool(b) and a > b


def parse_release(data: dict) -> UpdateInfo | None:
    if data.get("draft") or data.get("prerelease"):
        return None
    assets = {a.get("name"): a for a in data.get("assets", [])}
    exe = assets.get(ASSET)
    if exe is None:
        return None
    sums = assets.get(SUMS)
    return UpdateInfo(version=str(data.get("tag_name", "")).lstrip("v"), notes=data.get("body") or "",
                      url=exe["browser_download_url"], size=int(exe.get("size") or 0),
                      sums_url=sums["browser_download_url"] if sums else None,
                      page=data.get("html_url") or f"https://github.com/{REPO}/releases/latest")


def check(current: str, timeout: float = 8.0) -> UpdateInfo | None:
    """Newer release or None. Never raises (offline, rate limit, ... -> None)."""
    try:
        url = os.environ.get("FACEFRAME_UPDATE_URL") or API_LATEST     # override: local update test only
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            info = parse_release(json.loads(r.read().decode("utf-8")))
    except Exception as e:  # noqa: BLE001 - an update check must never break the app
        log.info("update check skipped: %s", e)
        return None
    if info is None or not is_newer(info.version, current):
        log.info("no update (current %s, latest %s)", current, info.version if info else "?")
        return None
    log.info("update available: %s -> %s", current, info.version)
    return info


def expected_sha256(sums_text: str, name: str = ASSET) -> str | None:
    for line in sums_text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[-1].lstrip("*") == name and re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            return parts[0].lower()
    return None


def download(info: UpdateInfo, progress=None, cancelled=lambda: False) -> Path:
    """Download + verify the installer. ``progress(done, total)``. Raises on failure."""
    dest = Path(tempfile.gettempdir()) / f"FaceFrame-Setup-{info.version}.exe"
    part = dest.with_suffix(".part")
    want = None
    if info.sums_url:
        with urllib.request.urlopen(urllib.request.Request(info.sums_url, headers=UA), timeout=30) as r:
            want = expected_sha256(r.read().decode("utf-8", "replace"))
    h = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(urllib.request.Request(info.url, headers=UA), timeout=60) as r, open(part, "wb") as f:
        total = int(r.headers.get("Content-Length") or info.size or 0)
        while True:
            if cancelled():
                raise RuntimeError("cancelled")
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if total and done != total:
        raise RuntimeError(f"download incomplete ({done} of {total} bytes)")
    if want and h.hexdigest() != want:
        raise RuntimeError("downloaded installer failed the checksum test")
    os.replace(part, dest)
    return dest


def install_dir() -> Path | None:
    """Folder of the installed app (has installed.flag), else None (dev checkout)."""
    root = Path(__file__).resolve().parents[1]
    return root if (root / "installed.flag").exists() else None


def launch_installer(setup: Path, app_dir: Path, wait_pids: list[int]) -> None:
    """Detached helper: wait for FaceFrame's processes to exit, install silently into
    ``app_dir``, then relaunch FaceFrame (the installer's /RELAUNCH=1 entry)."""
    pids = ",".join(str(int(p)) for p in wait_pids if p)

    def q(x) -> str:                       # PowerShell single-quoted literal (O'Brien-safe)
        return "'" + str(x).replace("'", "''") + "'"
    args = ",".join(q(a) for a in ("/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", "/RELAUNCH=1",
                                   f'/DIR="{app_dir}"', f'/LOG="{app_dir / "update.log"}"'))
    ps = (f"$ErrorActionPreference='SilentlyContinue'; "
          f"foreach ($i in @({pids or '0'})) {{ Wait-Process -Id $i -Timeout 60 }}; "
          f"Start-Sleep -Milliseconds 500; "
          f"Start-Process -FilePath {q(setup)} -ArgumentList {args}")
    # NO_WINDOW + NEW_PROCESS_GROUP (measured: with DETACHED_PROCESS PowerShell gets no console and dies
    # silently). BREAKAWAY_FROM_JOB keeps it alive if FaceFrame runs inside a kill-on-close job; some jobs
    # forbid breakaway, then start it without.
    cmd = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-Command", ps]
    base = 0x08000000 | 0x00000200
    # without PyInstaller bootloader variables (set by older FaceFrame.exe launchers): inherited by the
    # relaunched FaceFrame.exe they make it fail with "Failed to load Python DLL ... _MEIxxxx"
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(("_MEI", "_PYI"))}
    try:
        subprocess.Popen(cmd, creationflags=base | 0x01000000, close_fds=True, env=env)
    except OSError:
        subprocess.Popen(cmd, creationflags=base, close_fds=True, env=env)
    log.info("update helper started: %s -> %s (waiting for %s)", setup, app_dir, pids)


def own_pids() -> list[int]:
    """This process and, when started through the venv's pythonw launcher, its parent."""
    pids = [os.getpid()]
    try:
        import psutil
        par = psutil.Process().parent()
        if par is not None and par.name().lower().startswith("python"):
            pids.append(par.pid)
    except Exception:
        pass
    return pids
