import json

import pytest

from rippling import login


def _raw(**overrides):
    data = {
        "path": "/dashboard",
        "access_token": "t",
        "company_id": "c",
        "role_id": "r",
        "expires_at": "1796390186.386",
    }
    return json.dumps(data | overrides)


def test_credentials_from_storage():
    creds = login._credentials(_raw())
    assert creds.access_token == "t" and creds.expires_at == pytest.approx(
        1796390186.386
    )


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "not json",
        _raw(path="/login"),
        _raw(access_token=None),
        _raw(expires_at=None),
    ],
)
def test_incomplete_storage_is_not_a_login(raw):
    assert login._credentials(raw) is None


def test_browser_env_override(monkeypatch):
    monkeypatch.setenv("RIPPLING_BROWSER", "/opt/my/chrome")
    assert login.find_browser() == "/opt/my/chrome"


def test_no_browser_found(monkeypatch):
    monkeypatch.delenv("RIPPLING_BROWSER", raising=False)
    monkeypatch.setattr(login, "_candidates", lambda: ["/nonexistent/chrome"])
    with pytest.raises(login.LoginError, match="RIPPLING_BROWSER"):
        login.find_browser()


class StuckProc:
    """A process that ignores the polite close and needs terminate()."""

    def __init__(self):
        self.calls, self.alive = [], True

    def poll(self):
        return None if self.alive else 0

    def wait(self, timeout=None):
        if self.calls and self.calls[-1] == "terminate":
            self.alive = False
            return 0
        raise login.subprocess.TimeoutExpired("browser", timeout)

    def terminate(self):
        self.calls.append("terminate")

    def kill(self):
        self.calls.append("kill")


def test_close_escalates_and_never_raises(monkeypatch, tmp_path):
    b = login.Browser("/bin/browser", tmp_path)
    b.proc = StuckProc()
    monkeypatch.setattr(b, "_cdp", lambda *a, **k: {})  # Browser.close "succeeds"...
    b.close()  # ...but the process hangs; must terminate without raising
    assert b.proc.calls == ["terminate"] and not b.running


def test_bad_browser_path_is_a_login_error(tmp_path):
    b = login.Browser(str(tmp_path / "no-such-browser"), tmp_path / "profile")
    with pytest.raises(login.LoginError, match="Couldn't start the browser"):
        b.start("https://example.com")


def test_closed_window_ends_the_wait(monkeypatch):
    class NoTabs(login.Browser):
        def start(self, url, timeout=30):
            self.proc = StuckProc()  # still running, like macOS after the last window

        def pages(self):
            return []

        def close(self):
            pass

    monkeypatch.setattr(login, "Browser", NoTabs)
    monkeypatch.setattr(login, "find_browser", lambda: "/bin/browser")
    with pytest.raises(login.LoginError, match="closed"):
        login.wait_for_login(lambda c: True, timeout=5, poll=0)
