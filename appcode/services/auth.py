"""
Sign-in: one admin. pytincture's ``AUTH_USER_AUTHENTICATOR`` checks the
password from ``CA_ADMIN_PASSWORD``; ``policy_hook`` runs before every BFF
call and only asks for a signed-in user.
"""
from __future__ import annotations

import hmac
from typing import Any, Mapping, Optional


def claims_for(username: str) -> dict:
    return {"user_id": username, "username": username, "is_admin": True, "roles": ["admin", "user"]}


def authenticate(email: str, password: str, request: Any = None) -> Optional[dict]:
    from services.config import ADMIN_PASSWORD, ADMIN_USER

    user = (email or "").strip().casefold()
    if not ADMIN_PASSWORD or user != ADMIN_USER.casefold():
        return None
    if not hmac.compare_digest(password.encode(), ADMIN_PASSWORD.encode()):
        return None
    return claims_for(ADMIN_USER)


def policy_hook(user: Mapping[str, Any], policy: Mapping[str, Any], class_name: str = "",
                function_name: str = "", **_context: Any) -> bool:
    return bool(user and user.get("user_id"))


def actor(user: Optional[Mapping[str, Any]]) -> str:
    return str((user or {}).get("username") or "admin")
