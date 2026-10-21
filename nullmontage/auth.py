"""Optional password gate: HTTP Basic auth on every route when NULLMONTAGE_PASSWORD is set.

Basic auth rather than a login page because the browser caches the credentials for the origin and
re-sends them on every fetch, EventSource, <img> and <video> request, so the UI needs no changes.
Only the password is checked; any username is accepted. Serve it over HTTPS, since Basic auth sends
the password with every request.
"""
from __future__ import annotations

import base64
import binascii
import secrets

# Hosting platforms poll a health path without credentials; it reveals nothing about jobs.
OPEN_PATHS = {"/healthz"}


class PasswordGate:
    """Pure ASGI middleware, so streaming responses (SSE progress, video ranges) pass through untouched."""

    def __init__(self, app, password: str, realm: str = "nullmontage"):
        self.app = app
        self.password = password.encode()
        self.challenge = f'Basic realm="{realm}", charset="UTF-8"'.encode()

    def _authorised(self, scope) -> bool:
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                scheme, _, token = value.partition(b" ")
                if scheme.lower() != b"basic":
                    return False
                try:
                    decoded = base64.b64decode(token.strip(), validate=True)
                except (binascii.Error, ValueError):
                    return False
                _, sep, given = decoded.partition(b":")
                return bool(sep) and secrets.compare_digest(given, self.password)
        return False

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["path"] in OPEN_PATHS or self._authorised(scope):
            await self.app(scope, receive, send)
            return
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"www-authenticate", self.challenge), (b"content-type", b"text/plain; charset=utf-8")]})
        await send({"type": "http.response.body", "body": b"password required\n"})
