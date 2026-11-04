"""Browser login: the user signs in by hand, we lift the token from localStorage.

Uses agent-browser with a dedicated, persistent profile. Rippling sits behind
Cloudflare, so the window must be headed. Because the profile persists, a later
`rippling login` usually finds the session still active and needs no typing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from .auth import Credentials, config_dir

APP_URL = "https://app.rippling.com/"
SESSION = "rippling"
HELIUM = Path("/Applications/Helium.app/Contents/MacOS/Helium")

READ_STORAGE_JS = """JSON.stringify({
  path: location.pathname,
  access_token: localStorage.getItem('access_token'),
  refresh_token: localStorage.getItem('refresh_token'),
  expires_at: localStorage.getItem('expires_at'),
  company_id: localStorage.getItem('company_id'),
  role_id: localStorage.getItem('role_id'),
  user_agent: navigator.userAgent,
})"""


class LoginError(Exception):
    pass


def browser_executable() -> str | None:
    """RIPPLING_BROWSER, else Helium if installed, else agent-browser's own Chrome."""
    if env := os.environ.get("RIPPLING_BROWSER"):
        return env
    return str(HELIUM) if HELIUM.exists() else None


def _browser(*args: str, timeout: float = 60) -> str:
    # The same flags on every call, or agent-browser treats it as a new browser.
    cmd = ["agent-browser", "--session", SESSION]
    if exe := browser_executable():
        cmd += ["--executable-path", exe]
    cmd += ["--profile", str(config_dir() / "browser-profile"), "--headed", *args]
    r = subprocess.run(
        cmd, check=False, capture_output=True, text=True, timeout=timeout
    )
    if r.returncode != 0:
        raise LoginError(
            f"agent-browser {args[0]} failed: {r.stderr.strip() or r.stdout.strip()}"
        )
    return r.stdout.strip()


def _read_storage() -> dict:
    out = _browser("eval", READ_STORAGE_JS)
    # agent-browser prints the JS return value as JSON; ours is itself a JSON string.
    value = json.loads(out.splitlines()[-1])
    return json.loads(value) if isinstance(value, str) else value


def wait_for_login(
    verify, timeout: float = 600, poll: float = 2, on_wait=None
) -> Credentials:
    """Open the app and poll localStorage until a token that `verify` accepts appears."""
    if not shutil.which("agent-browser"):
        raise LoginError("agent-browser is not installed (npm i -g agent-browser).")
    _browser("open", APP_URL)
    deadline = time.monotonic() + timeout
    announced = False
    try:
        while time.monotonic() < deadline:
            s = _read_storage()
            if (
                s.get("access_token")
                and s.get("role_id")
                and "/login" not in s.get("path", "")
            ):
                creds = Credentials(
                    access_token=s["access_token"],
                    refresh_token=s.get("refresh_token") or "",
                    company_id=s["company_id"],
                    role_id=s["role_id"],
                    expires_at=float(s.get("expires_at") or 0),
                    user_agent=s.get("user_agent") or "",
                )
                if verify(creds):
                    return creds
            if not announced and on_wait:
                on_wait()
                announced = True
            time.sleep(poll)
        raise LoginError("Timed out waiting for login.")
    finally:
        try:
            _browser("close", timeout=30)
        except (LoginError, subprocess.TimeoutExpired):
            pass
