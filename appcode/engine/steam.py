"""
ASA builds: the one installed (its SteamCMD app manifest) and the latest
public one (api.steamcmd.net, cached 5 minutes).
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

APP_ID = "2430930"
LATEST_URL = f"https://api.steamcmd.net/v1/info/{APP_ID}"
CACHE_S = 300

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "build": None, "error": ""}


def installed_build(install_dir: str) -> Optional[str]:
    path = Path(install_dir) / "steamapps" / f"appmanifest_{APP_ID}.acf"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r'"buildid"\s+"(\d+)"', text)
    return m.group(1) if m else None


def parse_latest(data: dict) -> Optional[str]:
    try:
        return str(data["data"][APP_ID]["depots"]["branches"]["public"]["buildid"])
    except (KeyError, TypeError):
        return None


def latest_build(force: bool = False) -> tuple[Optional[str], str]:
    """``(build id, error)``; the error is "" when the lookup worked."""
    with _lock:
        if not force and time.time() - _cache["at"] < CACHE_S and (_cache["build"] or _cache["error"]):
            return _cache["build"], _cache["error"]
    build, error = None, ""
    try:
        req = urllib.request.Request(LATEST_URL, headers={"User-Agent": "clusters-ascended"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            build = parse_latest(json.load(resp))
        if not build:
            error = "Steam's answer had no public build id."
    except Exception as exc:  # noqa: BLE001 - network, JSON
        error = f"Could not ask Steam: {exc}"
    with _lock:
        _cache.update(at=time.time(), build=build, error=error)
    return build, error
