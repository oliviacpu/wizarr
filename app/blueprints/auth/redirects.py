from urllib.parse import unquote, urlsplit

from flask import url_for


def login_destination(value: str | None) -> str:
    """Keep administrator sign-in on a local path, independent of the public root."""
    fallback = url_for("admin.dashboard")
    if not isinstance(value, str) or not value or len(value) > 2048:
        return fallback
    decoded = unquote(value)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or "\\" in decoded
        or any(ord(char) < 32 or ord(char) == 127 for char in decoded)
    ):
        return fallback
    target = urlsplit(decoded)
    if target.scheme or target.netloc or target.path in {"/", "/login", "/logout"}:
        return fallback
    return value
