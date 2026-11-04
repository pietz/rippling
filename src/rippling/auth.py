"""Credential storage: one JSON file in the user's config dir (mode 0600)."""

from __future__ import annotations

import json
import os
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
    refresh_token: str = ""
    user_agent: str = ""

    @property
    def expired(self) -> bool:
        return self.expires_at <= time.time()


def config_dir() -> Path:
    env = os.environ.get("RIPPLING_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".config" / "rippling"


def credentials_path() -> Path:
    return config_dir() / "credentials.json"


def save(creds: Credentials) -> Path:
    path = credentials_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create with 0600 from the start so the token is never world-readable.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(asdict(creds), f, indent=2)
    os.chmod(path, 0o600)
    return path


def load() -> Credentials:
    path = credentials_path()
    if not path.exists():
        raise AuthError("Not logged in. Run `rippling login`.")
    creds = Credentials(**json.loads(path.read_text()))
    if creds.expired:
        raise AuthError("Rippling token expired. Run `rippling login`.")
    return creds
