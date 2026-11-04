import stat
import time

import pytest

from rippling import auth


def _creds(expires_at):
    return auth.Credentials(
        access_token="t", company_id="c", role_id="r", expires_at=expires_at
    )


def test_save_load_roundtrip_with_private_permissions():
    path = auth.save(_creds(time.time() + 3600))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert auth.load().access_token == "t"


def test_missing_and_expired_credentials_raise():
    with pytest.raises(auth.AuthError, match="login"):
        auth.load()
    auth.save(_creds(time.time() - 1))
    with pytest.raises(auth.AuthError, match="expired"):
        auth.load()
