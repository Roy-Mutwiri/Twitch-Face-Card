"""Isolated Edge test instance driven over the Chrome DevTools Protocol.

Launches msedge.exe with a throwaway --user-data-dir (never the user's
profile), muted audio, and remote debugging bound to 127.0.0.1. Used only by
validation scripts; the app itself never uses CDP.
"""

from __future__ import annotations

import itertools
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"


class Cdp:
    def __init__(self, ws_url: str) -> None:
        self.ws = connect(ws_url, max_size=64 * 2 ** 20, open_timeout=10)
        self._ids = itertools.count(1)
        self.events: list[dict] = []

    def call(self, method: str, params: dict | None = None, timeout: float = 30.0, session: str | None = None):
        mid = next(self._ids)
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.ws.send(json.dumps(msg))
        deadline = time.time() + timeout
        while time.time() < deadline:
            raw = self.ws.recv(timeout=max(0.1, deadline - time.time()))
            m = json.loads(raw)
            if m.get("id") == mid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            self.events.append(m)
        raise TimeoutError(method)

    def close(self) -> None:
        try:
            self.ws.close()
        except Exception:
            pass


class TestBrowser:
    def __init__(self, profile_dir: str | Path, port: int = 9333, extension_dir: str | Path | None = None,
                 extra_args: list[str] | None = None, window: tuple[int, int, int, int] | None = (0, 0, 1600, 1000)) -> None:
        self.profile_dir = Path(profile_dir)
        self.port = port
        self.extension_dir = Path(extension_dir) if extension_dir else None
        self.extra_args = extra_args or []
        self.window = window
        self.proc: subprocess.Popen | None = None
        self.browser: Cdp | None = None

    def start(self, url: str = "about:blank") -> None:
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        args = [EDGE, f"--user-data-dir={self.profile_dir}", f"--remote-debugging-port={self.port}",
                "--remote-debugging-address=127.0.0.1", "--remote-allow-origins=*",
                "--no-first-run", "--no-default-browser-check", "--mute-audio",
                "--disable-sync", "--hide-crash-restore-bubble", "--new-window"]
        if self.window:
            x, y, w, h = self.window
            args += [f"--window-position={x},{y}", f"--window-size={w},{h}"]
        if self.extension_dir:
            args += ["--enable-unsafe-extension-debugging"]
        args += self.extra_args + [url]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ver = None
        for _ in range(100):
            try:
                ver = json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/version", timeout=1))
                break
            except Exception:
                time.sleep(0.2)
        if ver is None:
            raise RuntimeError("Edge test instance did not expose CDP")
        self.version = ver
        self.browser = Cdp(ver["webSocketDebuggerUrl"])

    def load_extension(self) -> str:
        assert self.browser and self.extension_dir
        r = self.browser.call("Extensions.loadUnpacked", {"path": str(self.extension_dir)})
        return r["id"]

    def targets(self) -> list[dict]:
        return json.load(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/json/list", timeout=3))

    def page(self, url_contains: str = "") -> "Page":
        for t in self.targets():
            if t["type"] == "page" and url_contains in t.get("url", ""):
                return Page(t)
        raise LookupError(url_contains)

    def stop(self) -> None:
        try:
            if self.browser:
                self.browser.call("Browser.close", timeout=5)
        except Exception:
            pass
        if self.browser:
            self.browser.close()
        if self.proc:
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()


class Page:
    def __init__(self, target: dict) -> None:
        self.target = target
        self.cdp = Cdp(target["webSocketDebuggerUrl"])

    def eval(self, expr: str, await_promise: bool = True, timeout: float = 30.0):
        r = self.cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                               "awaitPromise": await_promise}, timeout=timeout)
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:800])
        return r["result"].get("value")

    def navigate(self, url: str) -> None:
        self.cdp.call("Page.navigate", {"url": url})

    def close(self) -> None:
        self.cdp.close()


def hwnd_for_pid_tree(title_contains: str) -> int | None:
    from faceframe.capture import windows as win
    for wi in win.list_edge_windows():
        if title_contains in wi.title:
            return wi.hwnd
    return None


if __name__ == "__main__":
    import sys
    tb = TestBrowser(Path(os.environ.get("TEMP", ".")) / "faceframe-edge-test")
    tb.start(sys.argv[1] if len(sys.argv) > 1 else "https://www.twitch.tv/")
    print(tb.version)
