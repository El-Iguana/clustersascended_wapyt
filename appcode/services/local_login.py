"""
Local sign-in: opening the app on this machine signs you straight in as the
admin, with no password typed. Only while both hold:

* the app runs in loopback mode (its canonical origin is 127.0.0.1), and
* the browser connects from this machine (a loopback peer address).

``CA_LOCAL_LOGIN=false`` turns it off. ``/clusters_ascended/login`` redirects
to ``/ca/local-login`` (signs in, then on to the app);
``/clusters_ascended/login?manual=1`` is the normal form, which signing out
goes to. Ported from calagopus_wapyt. Server only.
"""
from __future__ import annotations

import ipaddress
import time

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

APPLICATION = "clusters_ascended"
LOCAL_PATH = "/ca/local-login"

router = APIRouter()
_state = {"enabled": False}
# pytincture's _set_authenticated_user from the app create_app built (bind()).
# Not ``from pytincture.backend.app import …``: create_app runs its own copy
# of that module with this app's settings.
_helper: dict = {}

# A broken sign-in would loop login → local-login → app → login; past this
# many a minute, show the form.
MAX_PER_MINUTE = 5
_recent: list = []


def _too_many(now: float | None = None) -> bool:
    now = time.monotonic() if now is None else now
    _recent[:] = [t for t in _recent if now - t < 60]
    if len(_recent) >= MAX_PER_MINUTE:
        return True
    _recent.append(now)
    return False


def bind(application) -> str:
    """Find the session helper behind pytincture's own ``/{application}/auth/user``."""
    _helper.clear()
    for route in getattr(application, "routes", []):
        if getattr(route, "path", "") == "/{application}/auth/user":
            fn = getattr(route.endpoint, "__globals__", {}).get("_set_authenticated_user")
            if callable(fn):
                _helper["set_user"] = fn
                return ""
    _state["enabled"] = False
    return "  Local sign-in is OFF: this pytincture has no /auth/user session helper."


def load(loopback_deployment: bool) -> str:
    from services.config import LOCAL_LOGIN

    _state["enabled"] = False
    if not LOCAL_LOGIN:
        return ""
    if not loopback_deployment:
        return "  Local sign-in is OFF: this is not a loopback deployment."
    _state["enabled"] = True
    return "  Local sign-in is ON (browsers on this machine only; CA_LOCAL_LOGIN=false turns it off)."


def enabled() -> bool:
    return bool(_state["enabled"])


def is_local_peer(host: str | None) -> bool:
    try:
        return ipaddress.ip_address(host or "").is_loopback
    except ValueError:
        return False


def _navigation(scope) -> bool:
    """A page load, not a fetch() (sign-out's request follows its redirect to /login)."""
    for key, value in scope.get("headers") or []:
        if key == b"sec-fetch-mode":
            return value == b"navigate"
    return True


@router.get(LOCAL_PATH)
async def local_login(request: Request):
    peer = request.client.host if request.client is not None else ""
    if not enabled() or not is_local_peer(peer) or not _navigation(request.scope):
        return RedirectResponse(f"/{APPLICATION}/login?manual=1", status_code=303)
    if _too_many():
        return HTMLResponse(
            "<!doctype html><meta charset=utf-8><body style='font:15px system-ui;background:#0f1115;"
            "color:#e5e7eb;padding:40px'><h2>Local sign-in keeps failing</h2><p>Something ends the "
            f"session right after it starts. <a style='color:#93c5fd' href='/{APPLICATION}/login?manual=1'>"
            "Use the form</a>.</p></body>", status_code=502)

    from services.auth import claims_for
    from services.config import ADMIN_USER

    claims = {**claims_for(ADMIN_USER), "email": ADMIN_USER.casefold()}
    user_info = {**claims, "picture": f"/{APPLICATION}/appcode/profile.png", "auth_type": "user"}
    _helper["set_user"](request, user_info, application=APPLICATION, auth_type="user",
                        auth_provider="user", auth_provider_label="Email")
    return RedirectResponse(f"/{APPLICATION}/", status_code=303)


class LocalLoginRedirect:
    """``GET /clusters_ascended/login`` from this machine → ``/ca/local-login``, unless ``?manual=1``."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if (
            enabled()
            and scope["type"] == "http"
            and scope.get("method") == "GET"
            and scope.get("path") == f"/{APPLICATION}/login"
            and b"manual=1" not in (scope.get("query_string") or b"")
            and is_local_peer((scope.get("client") or ("",))[0])
            and _navigation(scope)
        ):
            await send({"type": "http.response.start", "status": 303,
                        "headers": [(b"location", LOCAL_PATH.encode()), (b"cache-control", b"no-store")]})
            await send({"type": "http.response.body", "body": b""})
            return
        await self.app(scope, receive, send)
