#!/usr/bin/env python3
"""
ASGI entrypoint for Clusters Ascended.

    python service.py            # http://127.0.0.1:8098/clusters_ascended/

Wiring follows the sibling wapyt apps (IguanaXterm, calagopus_wapyt): hooks by
dotted path, declared session claims, loopback-only plain HTTP, the
login-page rewrite, same-origin static files. ``engine.boot.start_engine()``
starts the poller and job workers once, after the app is built.
"""
from __future__ import annotations

import ipaddress
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent
APPCODE = HERE / "appcode"

# BFF modules import `services.*` and `engine.*`, and pytincture loads them
# by path, so the modules folder must be importable here too.
sys.path.insert(0, str(APPCODE))


def load_dotenv_file() -> None:
    """Load ``.env`` from the project root; real environment variables win."""
    env_path = HERE / ".env"
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_dotenv_file()


def register_mime_types() -> None:
    import mimetypes

    for suffix, kind in (
        (".js", "text/javascript"), (".mjs", "text/javascript"), (".css", "text/css"),
        (".json", "application/json"), (".wasm", "application/wasm"),
        (".svg", "image/svg+xml"), (".png", "image/png"), (".webp", "image/webp"),
    ):
        mimetypes.add_type(kind, suffix)


register_mime_types()

from pytincture import PytinctureConfig, create_app  # noqa: E402

from services.config import session_secret  # noqa: E402
from services.local_login import LocalLoginRedirect, bind as bind_local_login  # noqa: E402
from services.local_login import load as load_local_login  # noqa: E402
from services.local_login import router as local_login_router  # noqa: E402
from services.login_page import LoginPageMiddleware, check_at_startup  # noqa: E402

APPLICATION = "clusters_ascended"
# Own cookie names, so another pytincture app on this host cannot sign this one out.
COOKIE_NAMESPACE = "clustersascended"
PORT = int(os.getenv("PORT", "8098"))
BIND = os.getenv("CA_BIND", "127.0.0.1")


def canonical_origin() -> str:
    # A literal IP, never "localhost": pytincture's loopback check parses it.
    return os.getenv("CA_CANONICAL_ORIGIN", f"http://127.0.0.1:{PORT}").rstrip("/")


def _strip_port(value: str) -> str:
    head, _, tail = value.rpartition(":")
    return head if head and tail.isdigit() else value


def allowed_hosts() -> tuple[str, ...]:
    configured = os.getenv("CA_ALLOWED_HOSTS", "").strip()
    hosts = ([_strip_port(p.strip()) for p in configured.split(",") if p.strip()]
             if configured else ["127.0.0.1"])
    canonical = urlsplit(canonical_origin()).hostname
    if canonical and canonical not in hosts:
        hosts.append(canonical)
    return tuple(dict.fromkeys(hosts))


def is_loopback_deployment(origin: str) -> bool:
    parsed = urlsplit(origin)
    if parsed.scheme == "https":
        return False
    try:
        return ipaddress.ip_address(parsed.hostname or "").is_loopback
    except ValueError:
        return False


def build_app():
    hooks = {
        "AUTH_USER_AUTHENTICATOR": "services.auth.authenticate",
        "BFF_POLICY_HOOK_PATH": "services.auth.policy_hook",
        "AUTH_SESSION_CLAIM_KEYS": "user_id,is_admin,username",
    }
    origin = canonical_origin()
    loopback = is_loopback_deployment(origin)
    application = create_app(
        PytinctureConfig(
            modules_path=str(APPCODE),
            default_application=APPLICATION,
            enable_user_login=True,
            # Only to allow plain HTTP on loopback; the authenticator still
            # asks for the password on the form.
            enable_dev_email_login=loopback,
            cookie_namespace=COOKIE_NAMESPACE,
            session_secret=session_secret(),
            allowed_hosts=allowed_hosts(),
            canonical_origin=origin,
            trusted_proxy_headers=not loopback,
            environment=hooks,
        )
    )
    check_at_startup()
    local = load_local_login(loopback)
    local = (bind_local_login(application) or local) if local else local
    if local:
        print(local, flush=True)
    application.add_middleware(LoginPageMiddleware)
    application.add_middleware(LocalLoginRedirect)
    application.include_router(local_login_router)

    from fastapi.staticfiles import StaticFiles

    application.mount("/static", StaticFiles(directory=str(APPCODE / "static")), name="static")

    if os.getenv("CA_ENGINE", "on") != "off":
        from engine.boot import start_engine

        start_engine()
    return application


app = build_app()


if __name__ == "__main__":
    import uvicorn

    print(f"\n  Clusters Ascended\n  URL : {canonical_origin()}/{APPLICATION}/\n", flush=True)
    uvicorn.run(app, host=BIND, port=PORT, log_level="info")
