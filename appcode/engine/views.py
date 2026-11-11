"""
What the map and cluster pages show, and the checks on what they save.
Server only; the BFFs call these so the rules are tested without a browser.
"""
from __future__ import annotations

import re
from typing import Optional

from engine import lifecycle, podman, state, steam, store, watchdog
from engine.models import COMMITTED, GiB, effective

MASK = "••••••"
_PASSWORD = re.compile(r"^[A-Za-z0-9_\-]*$")   # what ASA's URL-style args survive
_MAP_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_MOD_ID = re.compile(r"^\d{3,10}$")


class Invalid(ValueError):
    pass


def _mask(d: dict) -> dict:
    out = dict(d or {})
    for key in ("admin_password", "server_password"):
        if out.get(key):
            out[key] = MASK
    return out


def container_info(server) -> dict:
    """Is it the app's container, does it match the settings, is a rollback possible?"""
    if podman.degraded:
        return {"known": False}
    info = podman.inspect(server.container_name)
    cluster = store.cluster(server.cluster)
    return {
        "known": True,
        "exists": info is not None,
        "managed": lifecycle.is_managed(info),
        "drift": lifecycle.spec_drift(server, cluster, store.host_settings(), info) if info else False,
        "old_exists": podman.inspect(server.container_name + lifecycle.OLD_SUFFIX) is not None,
    }


def build_info(server) -> dict:
    latest, error, at = steam.cached_latest()
    installed = steam.installed_build(server.install_dir)
    return {"installed": installed, "latest": latest, "checked_at": at or None, "error": error,
            "update_available": bool(latest and installed and latest != installed)}


def server_detail(server_id: str) -> Optional[dict]:
    s = store.server(server_id)
    if s is None:
        return None
    c = store.cluster(s.cluster)
    eff = effective(s, c)
    lv = state.live(s.id)
    runs = store.recent_runs(s.id, 0)[:15]
    return {
        "server": {**s.to_dict(), "overrides": _mask(s.overrides)},
        "cluster": {"id": c.id, "name": c.name, "cluster_id": c.cluster_id,
                    "defaults": _mask(c.defaults)},
        "effective": _mask(eff),
        "phase": state.phase(s.id),
        "need": state.need_of(s, store.host_settings()),
        "live": {"mem": lv.mem, "peak": lv.peak, "players": lv.players, "ready_at": lv.ready_at,
                 "started_at": lv.started_at or None, "managed": lv.extra.get("managed", False)},
        "container": container_info(s),
        "build": build_info(s),
        "backups": store.backups(s.id, 30),
        "runs": runs,
    }


def cluster_detail(cluster_id: str) -> Optional[dict]:
    c = store.cluster(cluster_id)
    if c is None:
        return None
    return {"cluster": {**c.to_dict(), "defaults": _mask(c.defaults)},
            "servers": [{"id": s.id, "name": s.name, "priority": s.priority,
                         "overrides": _mask(s.overrides)} for s in store.servers(c.id)]}


def watchdog_status() -> dict:
    return watchdog.status()


def quick_flags(server, cluster, settings) -> dict:
    """Dashboard flags from the poller's last container listing: no podman call."""
    from engine import poller

    c = poller.container(server.container_name)
    labels = (c or {}).get("labels") or {}
    managed = labels.get("io.clustersascended.managed") == "1"
    drift = False
    if managed:
        want = lifecycle.desired_spec(server, cluster, effective(server, cluster), settings)
        drift = labels.get("io.clustersascended.spec") != want.labels["io.clustersascended.spec"]
    latest, _error, _at = steam.cached_latest()
    installed = steam.installed_build(server.install_dir)
    return {"managed": managed, "exists": c is not None, "drift": drift, "held": server.held,
            "autostart": server.autostart, "desired": server.desired,
            "update_available": bool(latest and installed and latest != installed),
            "installed_build": installed, "latest_build": latest}


# -- saving -------------------------------------------------------------------

def _int(value, name: str, lo: int, hi: int) -> int:
    try:
        n = int(str(value).strip())
    except (TypeError, ValueError):
        raise Invalid(f"{name} must be a whole number.")
    if not lo <= n <= hi:
        raise Invalid(f"{name} must be between {lo} and {hi}.")
    return n


def _password(value, name: str, current: str) -> str:
    """The mask keeps the current one; "" clears it."""
    text = str(value if value is not None else MASK)
    if text == MASK:
        return current
    if not _PASSWORD.match(text):
        raise Invalid(f"{name}: letters, digits, _ and - only (ASA passes it on its command line).")
    return text


def _mods(value) -> list:
    if isinstance(value, str):
        value = re.split(r"[\s,]+", value)
    mods = []
    for m in value or []:
        m = str(m).strip()
        found = re.search(r"(\d{3,10})(?!.*\d)", m)   # a bare id or a CurseForge link ending in one
        if not m:
            continue
        if not found or not _MOD_ID.match(found.group(1)):
            raise Invalid(f"Not a mod id: {m}")
        if found.group(1) not in mods:
            mods.append(found.group(1))
    return mods


def _args(value) -> list:
    args = str(value or "").split() if not isinstance(value, list) else [str(a) for a in value]
    for a in args:
        if not a.startswith("-"):
            raise Invalid(f"Launch flags start with '-': {a}")
    return [a for a in args if a.lower() != "-nobattleye"]


def save_cluster(cluster_id: str, values: dict) -> dict:
    c = store.cluster(cluster_id)
    if c is None:
        raise Invalid("Unknown cluster.")
    d = dict(c.defaults or {})
    if "name" in values:
        name = str(values["name"]).strip()
        if not name:
            raise Invalid("The cluster needs a name.")
        c.name = name
    if "mod_ids" in values:
        d["mod_ids"] = _mods(values["mod_ids"])
    if "custom_args" in values:
        d["custom_args"] = _args(values["custom_args"])
    if "battleye" in values:
        d["battleye"] = bool(values["battleye"])
    if "max_players" in values:
        d["max_players"] = _int(values["max_players"], "Max players", 1, 200)
    if "admin_password" in values:
        d["admin_password"] = _password(values["admin_password"], "Admin password", d.get("admin_password", ""))
        if not d["admin_password"]:
            raise Invalid("The admin password cannot be empty: RCON (saves, stops, players) needs it.")
    if "server_password" in values:
        d["server_password"] = _password(values["server_password"], "Join password",
                                         d.get("server_password", ""))
    if "max_running" in values:
        raw = str(values["max_running"] or "").strip()
        c.max_running = _int(raw, "Maps at once", 1, 50) if raw else None
    if "memory_cap" in values:
        raw = str(values["memory_cap"] or "").strip()
        try:
            c.memory_cap = int(float(raw) * GiB) if raw else None
        except ValueError:
            raise Invalid("The memory cap is a number of GiB.")
    c.defaults = d
    store.save_cluster(c)
    return {"restart_needed": _running_names(c.id)}


def save_server(server_id: str, values: dict) -> dict:
    s = store.server(server_id)
    if s is None:
        raise Invalid("Unknown server.")
    o = dict(s.overrides or {})
    if "name" in values:
        name = str(values["name"]).strip()
        if not name:
            raise Invalid("The map needs a name.")
        s.name = name
    if "session_name" in values:
        session = str(values["session_name"]).strip()
        if not session or len(session) > 64:
            raise Invalid("The session name is 1 to 64 characters.")
        s.session_name = session
    if "map_name" in values:
        m = str(values["map_name"]).strip()
        if not _MAP_NAME.match(m):
            raise Invalid("MAP_NAME is letters, digits and _ (e.g. TheIsland_WP).")
        s.map_name = m
    if "priority" in values:
        s.priority = _int(values["priority"], "Priority", 1, 99)
    if "autostart" in values:
        s.autostart = bool(values["autostart"])
    if "need_override" in values:
        raw = str(values["need_override"] or "").strip()
        try:
            s.need_override = int(float(raw) * GiB) if raw else None
        except ValueError:
            raise Invalid("The memory need is a number of GiB (empty = learned).")
    for key, field_name, lo, hi in (("game_port", "Game port", 1024, 65535),
                                    ("rcon_port", "RCON port", 1024, 65535)):
        if key in values:
            setattr(s, key, _int(values[key], field_name, lo, hi))
    ports = {}
    for other in store.servers():
        if other.id != s.id:
            ports[other.game_port] = other.name
            ports[other.rcon_port] = other.name
    for port in (s.game_port, s.rcon_port):
        if port in ports:
            raise Invalid(f"Port {port} is used by {ports[port]}.")
    if s.game_port == s.rcon_port:
        raise Invalid("The game and RCON ports must differ.")
    # Overrides: "" or None means "use the cluster's".
    if "max_players" in values:
        raw = str(values["max_players"] or "").strip()
        if raw:
            o["max_players"] = _int(raw, "Max players", 1, 200)
        else:
            o.pop("max_players", None)
    if "battleye" in values:
        if values["battleye"] in (None, "", "inherit"):
            o.pop("battleye", None)
        else:
            o["battleye"] = values["battleye"] in (True, "on", "true", "1")
    if "custom_args" in values:
        # None: use the cluster's flags. A string (even empty) replaces them.
        if values["custom_args"] is None:
            o.pop("custom_args", None)
        else:
            o["custom_args"] = _args(values["custom_args"])
    for key, label in (("admin_password", "Admin password"), ("server_password", "Join password")):
        if key in values:
            if values[key] in (None, "") or values[key] == "inherit":
                o.pop(key, None)
            else:
                o[key] = _password(values[key], label, o.get(key, ""))
    if "mods_mode" in values:
        mode = values["mods_mode"]
        if mode not in ("inherit", "append", "replace"):
            raise Invalid("Mods: inherit, append or replace.")
        if mode == "inherit":
            o.pop("mod_ids", None)
            o.pop("mods_mode", None)
        else:
            o["mods_mode"] = mode
            o["mod_ids"] = _mods(values.get("mod_ids") or [])
    s.overrides = o
    store.save_server(s)
    return {"restart_needed": [s.name] if state.phase(s.id) in COMMITTED else []}


def clear_hold(server_id: str) -> None:
    s = store.server(server_id)
    if s is None:
        raise Invalid("Unknown server.")
    s.held = ""
    store.save_server(s)


def _running_names(cluster_id: str) -> list:
    return [s.name for s in store.servers(cluster_id) if state.phase(s.id) in COMMITTED]
