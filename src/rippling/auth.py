"""Credential storage: one JSON file in the user's config dir (mode 0600)."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path


class AuthError(Exception):
    """No usable credentials. The fix is always `rippling login`."""


@dataclass
class Credentials:
    access_token: str
    company_id: str
    role_id: str
    expires_at: float

    @property
    def expired(self) -> bool:
        return self.expires_at <= time.time()


def config_dir() -> Path:
    env = os.environ.get("RIPPLING_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".config" / "rippling"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def profile_dir() -> Path:
    """The browser profile `rippling login` uses; it keeps the web session."""
    return config_dir() / "browser-profile"


def clear() -> list[Path]:
    """Delete the stored token and browser profile. Returns what was removed."""
    removed = []
    if (path := credentials_path()).exists():
        path.unlink()
        removed.append(path)
    if (profile := profile_dir()).exists():
        shutil.rmtree(profile)
        removed.append(profile)
    return removed


def save(creds: Credentials) -> Path:
    path = credentials_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    # A new private (0600) file, swapped in at once: the token is never readable by
    # others, even if an old file had looser permissions, and a crash can't leave
    # a half-written file behind.
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".credentials-")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(asdict(creds), f, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return path


def load() -> Credentials:
    path = credentials_path()
    if not path.exists():
        raise AuthError("Not logged in. Run `rippling login`.")
    try:
        data = json.loads(path.read_text())
        creds = Credentials(**{k: data[k] for k in Credentials.__dataclass_fields__})
    except (ValueError, KeyError, TypeError):
        raise AuthError(f"Can't read {path}. Run `rippling login`.") from None
    if creds.expired:
        raise AuthError("Rippling token expired. Run `rippling login`.")
    return creds
