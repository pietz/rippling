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


class TransportError(Exception):
    """No usable response (network failure, timeout, unparseable body).

    For a write this means the outcome is unknown: it may or may not have been applied.
    """


class Client:
    def __init__(self, creds: Credentials):
        self.creds = creds
        # Same client profile and headers as the Rippling web app in Chrome.
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
        try:
            r = self.session.request(method, BASE + path, timeout=30, **kwargs)
        except requests.RequestsError as e:
            raise TransportError(f"{method} {path}: {e}") from e
        if r.status_code == 401:
            raise AuthError("Rippling rejected the token. Run `rippling login`.")
        if r.status_code >= 400:
            raise ApiError(r.status_code, r.text, path)
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError as e:
            raise TransportError(f"{method} {path}: response is not JSON") from e

    def get(self, path: str, **params: Any) -> Any:
        return self.request("GET", path, params=params)

    def post(self, path: str, body: dict) -> Any:
        return self.request("POST", path, json=body)

    def twirp(self, method: str, body: dict) -> Any:
        return self.post(TWIRP + method, body)
