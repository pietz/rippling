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
