"""
Server-side settings, read once from the environment (and ``.env``). Server only.
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

_O_BINARY = getattr(os, "O_BINARY", 0)

DATA_DIR = Path(os.getenv("CA_DATA_DIR", str(Path(__file__).resolve().parents[2] / "data")))
SESSION_KEY_PATH = DATA_DIR / "session.key"

# The one admin password for the sign-in form. Empty: only local sign-in works.
ADMIN_PASSWORD = os.getenv("CA_ADMIN_PASSWORD", "")
ADMIN_USER = os.getenv("CA_ADMIN_USER", "admin")

# Opening the app on this machine signs you in without the form (loopback only).
LOCAL_LOGIN = os.getenv("CA_LOCAL_LOGIN", "true").lower() in ("1", "true", "yes", "on")


def session_secret() -> str:
    """Cookie-signing secret, persisted in data/session.key (``CA_SESSION_SECRET`` overrides)."""
    from_env = os.environ.get("CA_SESSION_SECRET", "").strip()
    if from_env:
        return from_env
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if SESSION_KEY_PATH.exists():
        existing = SESSION_KEY_PATH.read_text(encoding="ascii").strip()
        if existing:
            return existing
    secret = secrets.token_urlsafe(48)
    fd = os.open(SESSION_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BINARY, 0o600)
    try:
        os.write(fd, secret.encode())
    finally:
        os.close(fd)
    return secret
