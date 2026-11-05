import json
import stat
import sys
import time

import pytest

from rippling import auth


def _creds(expires_at):
    return auth.Credentials(
        access_token="t", company_id="c", role_id="r", expires_at=expires_at
    )


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_save_is_private_even_over_a_loose_existing_file():
    path = auth.credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text("{}")
    path.chmod(0o644)
    auth.save(_creds(time.time() + 3600))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert auth.load().access_token == "t"
    assert [p.name for p in path.parent.iterdir()] == ["credentials.json"]


@pytest.mark.parametrize("content", ["{trunc", "{}", json.dumps({"access_token": 1})])
def test_unreadable_credentials_ask_for_login(content):
    path = auth.credentials_path()
    path.parent.mkdir(parents=True)
    path.write_text(content)
    with pytest.raises(auth.AuthError, match="rippling login"):
        auth.load()


def test_missing_and_expired_credentials_raise():
    with pytest.raises(auth.AuthError, match="login"):
        auth.load()
    auth.save(_creds(time.time() - 1))
    with pytest.raises(auth.AuthError, match="expired"):
        auth.load()


def test_clear_removes_token_and_profile():
    assert auth.clear() == []
    auth.save(_creds(time.time() + 3600))
    (auth.profile_dir() / "Default").mkdir(parents=True)
    removed = auth.clear()
    assert removed == [auth.credentials_path(), auth.profile_dir()]
    assert not auth.credentials_path().exists() and not auth.profile_dir().exists()
    with pytest.raises(auth.AuthError):
        auth.load()
