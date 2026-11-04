"""Browser login: the user signs in by hand, we read the token from localStorage.

Starts an installed Chromium-family browser (Chrome, Edge, Brave, Chromium, Helium)
with a dedicated profile and a local DevTools port, then reads localStorage over the
DevTools protocol. It is a normal browser window: the user logs in exactly as on
the website (password, browser check, 2FA). The profile
persists, so later logins usually need no typing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import CurlError
from curl_cffi.requests import WebSocket

from .auth import Credentials, config_dir

APP_HOST = "app.rippling.com"
APP_URL = f"https://{APP_HOST}/"

READ_STORAGE_JS = """JSON.stringify({
  path: location.pathname,
  access_token: localStorage.getItem('access_token'),
  expires_at: localStorage.getItem('expires_at'),
  company_id: localStorage.getItem('company_id'),
  role_id: localStorage.getItem('role_id'),
})"""


class LoginError(Exception):
    pass


# What talking to a browser over DevTools can raise: socket/HTTP errors (OSError covers
# urllib's URLError), WebSocket errors (CurlError), bad or partial JSON, missing keys.
CDP_ERRORS = (OSError, CurlError, ValueError, KeyError, TimeoutError)


def _candidates() -> list[str]:
    if sys.platform == "darwin":
        apps = [
            "Helium",
            "Google Chrome",
            "Microsoft Edge",
            "Brave Browser",
            "Chromium",
            "Vivaldi",
        ]
        roots = [Path("/Applications"), Path.home() / "Applications"]
        return [
            str(r / f"{app}.app/Contents/MacOS/{app}") for r in roots for app in apps
        ]
    if sys.platform == "win32":
        roots = [
            os.environ.get(k)
            for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")
        ]
        rel = [
            r"Google\Chrome\Application\chrome.exe",
            r"Microsoft\Edge\Application\msedge.exe",
            r"BraveSoftware\Brave-Browser\Application\brave.exe",
            r"Chromium\Application\chrome.exe",
        ]
        return [str(Path(r) / p) for r in roots if r for p in rel]
    names = [
        "google-chrome",
        "google-chrome-stable",
        "chromium",
        "chromium-browser",
        "microsoft-edge",
        "brave-browser",
        "helium",
    ]
    return [p for n in names if (p := shutil.which(n))]


def find_browser() -> str:
    """RIPPLING_BROWSER, else the first installed Chromium-family browser."""
    if env := os.environ.get("RIPPLING_BROWSER"):
        return env
    for path in _candidates():
        if Path(path).is_file():
            return path
    raise LoginError(
        "No Chromium-based browser found (Chrome, Edge, Brave, Chromium or Helium). "
        "Set RIPPLING_BROWSER to the path of its executable."
    )


class Browser:
    """A browser process with its own profile and a DevTools port on localhost."""

    def __init__(self, executable: str, profile: Path):
        self.executable, self.profile = executable, profile
        self.proc: subprocess.Popen | None = None
        self.port = 0
        self.browser_ws = ""

    def start(self, url: str, timeout: float = 30) -> None:
        self.profile.mkdir(parents=True, exist_ok=True)
        port_file = self.profile / "DevToolsActivePort"
        port_file.unlink(missing_ok=True)
        self.proc = subprocess.Popen(
            [
                self.executable,
                f"--user-data-dir={self.profile}",
                "--remote-debugging-port=0",  # the browser picks a free port
                # Behave like a regular browser window while the debugging port is open.
                "--disable-blink-features=AutomationControlled",
                # Profile-local cookie encryption: no OS keychain prompt, and the
                # profile stays readable no matter which browser opens it.
                "--use-mock-keychain",
                "--password-store=basic",
                "--no-first-run",
                "--no-default-browser-check",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise LoginError(
                    "The browser exited right away. If it's already open with the "
                    "rippling profile, close that window and try again."
                )
            try:
                port, path = port_file.read_text().split()[:2]
                self.port, self.browser_ws = int(port), path
                return
            except (FileNotFoundError, ValueError):
                time.sleep(0.2)
        raise LoginError("The browser didn't open its DevTools port in time.")

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def _cdp(self, ws_url: str, method: str, params: dict | None = None) -> dict:
        ws = WebSocket()
        ws.connect(ws_url, timeout=10)
        try:
            ws.send_str(json.dumps({"id": 1, "method": method, "params": params or {}}))
            while True:
                msg = json.loads(ws.recv_str(timeout=10))
                if msg.get("id") == 1:
                    return msg
        finally:
            ws.close()

    def evaluate_on(self, host: str, expression: str) -> object:
        """Evaluate JS in the first open tab whose host is exactly `host`; None if none."""
        url = f"http://127.0.0.1:{self.port}/json/list"
        with urllib.request.urlopen(url, timeout=5) as r:
            targets = json.load(r)
        for t in targets:
            if t.get("type") == "page" and urlparse(t.get("url", "")).hostname == host:
                msg = self._cdp(
                    t["webSocketDebuggerUrl"],
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": True},
                )
                return msg.get("result", {}).get("result", {}).get("value")
        return None

    def close(self) -> None:
        """Close the browser and reap the process. Never raises."""
        if self.proc is None or self.proc.poll() is not None:
            return
        try:
            self._cdp(f"ws://127.0.0.1:{self.port}{self.browser_ws}", "Browser.close")
            self.proc.wait(timeout=10)
            return
        except (*CDP_ERRORS, subprocess.TimeoutExpired):
            pass
        for stop in (self.proc.terminate, self.proc.kill):
            try:
                stop()
                self.proc.wait(timeout=5)
                return
            except (OSError, subprocess.TimeoutExpired):
                continue


def _credentials(raw: object) -> Credentials | None:
    try:
        s = json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None
    if not isinstance(s, dict) or "/login" in (s.get("path") or ""):
        return None
    keys = ("access_token", "company_id", "role_id", "expires_at")
    if not all(s.get(k) for k in keys):
        return None
    return Credentials(
        access_token=s["access_token"],
        company_id=s["company_id"],
        role_id=s["role_id"],
        expires_at=float(s["expires_at"]),
    )


def wait_for_login(
    verify, timeout: float = 600, poll: float = 2, on_wait=None
) -> Credentials:
    """Open Rippling in a browser and poll localStorage until `verify` accepts a token."""
    browser = Browser(find_browser(), config_dir() / "browser-profile")
    started = time.monotonic()
    deadline = started + timeout
    announced = False
    try:
        browser.start(APP_URL)
        while time.monotonic() < deadline:
            if not browser.running:
                raise LoginError("The browser was closed before the login finished.")
            try:
                creds = _credentials(browser.evaluate_on(APP_HOST, READ_STORAGE_JS))
            except CDP_ERRORS:
                creds = None  # page mid-navigation or tab not ready; keep waiting
            if creds and verify(creds):
                return creds
            # Only ask for typing if the saved session didn't log us in right away.
            if not announced and on_wait and time.monotonic() > started + 5:
                on_wait()
                announced = True
            time.sleep(poll)
        raise LoginError("Timed out waiting for login.")
    finally:
        browser.close()
