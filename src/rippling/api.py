"""Thin client for Rippling's internal web API (the one app.rippling.com calls)."""

from __future__ import annotations

from typing import Any

from curl_cffi import requests

from .auth import AuthError, Credentials

BASE = "https://app.rippling.com/api"
TWIRP = "/time_products/twirp/time_products.shared.v1.TimesheetService/"


class ApiError(Exception):
    def __init__(self, status: int, body: str, path: str):
        self.status, self.body, self.path = status, body, path
        super().__init__(f"HTTP {status} from {path}: {body[:500]}")


class Client:
    def __init__(self, creds: Credentials):
        self.creds = creds
        # Requests mirror the web app: Chrome's TLS fingerprint plus its context headers.
        self.session = requests.Session(impersonate="chrome")
        self.session.headers.update(
            {
                "Authorization": f"Bearer {creds.access_token}",
                "Company": creds.company_id,
                "Role": creds.role_id,
                "requestedAccessLevel": "EE",
                "Accept": "application/json",
            }
        )

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        r = self.session.request(method, BASE + path, timeout=30, **kwargs)
        if r.status_code == 401:
            raise AuthError("Rippling rejected the token. Run `rippling login`.")
        if r.status_code >= 400:
            raise ApiError(r.status_code, r.text, path)
        return r.json() if r.content else None

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict) -> Any:
        return self.request("POST", path, json=body)

    def twirp(self, method: str, body: dict) -> Any:
        return self.post(TWIRP + method, body)
