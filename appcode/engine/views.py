"""
What the map and cluster pages show, and the checks on what they save.
Server only; the BFFs call these so the rules are tested without a browser.
"""
from __future__ import annotations

import re
import time
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
        "need": state.needs_of(s, store.host_settings())[0],
        "need_start": state.needs_of(s, store.host_settings())[1],
        "live": {"mem": lv.mem, "peak": lv.peak, "players": lv.players, "ready_at": lv.ready_at,
                 "started_at": lv.started_at or None, "managed": lv.extra.get("managed", False)},
        "container": container_info(s),
        "build": build_info(s),
        "backups": store.backups(s.id, 30),
        "runs": runs,
        "game": {"managed": c.game is not None, "cluster": _game_effective(c),
                 "own": (s.overrides or {}).get("game") or {}},
        "mod_names": store.mod_names(),
    }


def _game_effective(cluster) -> dict:
    from engine import game_settings

    return game_settings.effective(cluster)


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


# -- game settings ------------------------------------------------------------

KNOWN_MODS = {
    "942024": "Dino Depot", "928548": "Shiny! Dinos Ascended", "929420": "Super Spyglass Plus",
    "950970": "Birthweaver Tames",
}


def _game_values(values: dict) -> dict:
    """Validate {key: value} against the catalogue; returns typed values."""
    from services.ark_settings import SETTINGS, in_range, normalize

    out = {}
    for key, raw in (values or {}).items():
        if key not in SETTINGS:
            raise Invalid(f"Not a setting this app manages: {key}")
        try:
            value = normalize(key, raw)
        except ValueError as exc:
            raise Invalid(str(exc))
        if not in_range(key, value):
            s = SETTINGS[key]
            raise Invalid(f"{s['label']} must be between {s['min']:g} and {s['max']:g}.")
        out[key] = value
    return out


def game_detail(cluster_id: str) -> Optional[dict]:
    from engine import game_settings
    from services.ark_settings import differs

    c = store.cluster(cluster_id)
    if c is None:
        return None
    maps = []
    for s in store.servers(c.id):
        own = (s.overrides or {}).get("game") or {}
        maps.append({"id": s.id, "name": s.name, "phase": state.phase(s.id), "overrides": own})
    values = game_settings.effective(c)
    return {"cluster": {"id": c.id, "name": c.name}, "managed": c.game is not None,
            "values": values, "differs": differs(values), "maps": maps}


def save_game(cluster_id: str, values: dict) -> dict:
    """The cluster's game settings: every key given (the form sends all of them)."""
    from services.ark_settings import is_default

    c = store.cluster(cluster_id)
    if c is None:
        raise Invalid("Unknown cluster.")
    typed = _game_values(values)
    c.game = {k: v for k, v in typed.items() if not is_default(k, v)}
    store.save_cluster(c)
    return {"restart_needed": _running_names(c.id), "differs": len(c.game)}


def import_game(cluster_id: str) -> dict:
    """Take the settings the maps' ini files use now: shared ones to the cluster, the rest per map."""
    from engine import game_settings

    c = store.cluster(cluster_id)
    if c is None:
        raise Invalid("Unknown cluster.")
    servers = store.servers(c.id)
    shared, own = game_settings.import_from_maps(c, servers)
    c.game = shared
    store.save_cluster(c)
    for s in servers:
        o = dict(s.overrides or {})
        if own.get(s.id):
            o["game"] = own[s.id]
        else:
            o.pop("game", None)
        s.overrides = o
        store.save_server(s)
    for mod_id in c.defaults.get("mod_ids") or []:
        if mod_id in KNOWN_MODS and mod_id not in store.mod_names([mod_id]):
            store.set_mod_name(mod_id, KNOWN_MODS[mod_id])
    return {"shared": len(shared), "per_map": {s.id: len(own.get(s.id) or {}) for s in servers}}


def save_map_game(server_id: str, overrides: dict) -> dict:
    """A map's own game settings (only the keys that differ from its cluster's)."""
    s = store.server(server_id)
    if s is None:
        raise Invalid("Unknown map.")
    typed = _game_values(overrides)
    o = dict(s.overrides or {})
    if typed:
        o["game"] = typed
    else:
        o.pop("game", None)
    s.overrides = o
    store.save_server(s)
    return {"restart_needed": [s.name] if state.phase(s.id) in COMMITTED else []}


# -- mods ---------------------------------------------------------------------

def mods_detail(cluster_id: str) -> Optional[dict]:
    c = store.cluster(cluster_id)
    if c is None:
        return None
    ids = list(c.defaults.get("mod_ids") or [])
    maps = []
    for s in store.servers(c.id):
        o = s.overrides or {}
        maps.append({"id": s.id, "name": s.name, "mode": o.get("mods_mode") or "inherit",
                     "own": list(o.get("mod_ids") or []),
                     "loads": list(effective(s, c).get("mod_ids") or [])})
        ids += [m for m in o.get("mod_ids") or [] if m not in ids]
    return {"cluster": {"id": c.id, "name": c.name}, "mods": list(c.defaults.get("mod_ids") or []),
            "maps": maps, "names": store.mod_names(ids)}


def name_mod(mod_id: str, name: str) -> None:
    if not _MOD_ID.match(str(mod_id)):
        raise Invalid("Not a mod id.")
    name = str(name or "").strip()
    if len(name) > 80:
        raise Invalid("Keep the name under 80 characters.")
    store.set_mod_name(str(mod_id), name)


# -- backups and schedules ----------------------------------------------------

def backups_detail(cluster_id: str) -> Optional[dict]:
    import shutil

    c = store.cluster(cluster_id)
    if c is None:
        return None
    maps = []
    total = 0
    for s in store.servers(c.id):
        rows = store.backups(s.id, 200)
        total += sum(b["size"] for b in rows)
        maps.append({"id": s.id, "name": s.name, "phase": state.phase(s.id), "backups": rows})
    try:
        usage = shutil.disk_usage(c.root_dir)
        disk = {"free": usage.free, "total": usage.total}
    except OSError:
        disk = None
    return {"cluster": {"id": c.id, "name": c.name}, "schedules": store.schedules(c.id), "maps": maps,
            "total": total, "disk": disk}


def save_schedule(cluster_id: str, kind: str, values: dict) -> None:
    from engine import scheduler

    if store.cluster(cluster_id) is None:
        raise Invalid("Unknown cluster.")
    if kind not in scheduler.DEFAULTS:
        raise Invalid("Unknown schedule.")
    at = str(values.get("at") or scheduler.DEFAULTS[kind]["at"]).strip()
    try:
        scheduler.parse_at(at)
    except ValueError as exc:
        raise Invalid(str(exc))
    days = sorted({int(d) for d in values.get("days") or scheduler.ALL_DAYS if 0 <= int(d) <= 6})
    if not days:
        raise Invalid("Pick at least one day.")
    params = dict(scheduler.DEFAULTS[kind]["params"])
    if kind == "backup":
        params["keep"] = _int(values.get("keep", 7), "Backups to keep", 1, 60)
    else:
        warn = values.get("warn") or [15, 5, 1]
        if isinstance(warn, str):
            warn = [w for w in re.split(r"[\s,]+", warn) if w]
        try:
            warn = sorted({int(w) for w in warn if 0 < int(w) <= 60}, reverse=True)
        except ValueError:
            raise Invalid("Warnings are minutes, like 15, 5, 1.")
        params["warn"] = warn or [5]
        message = str(values.get("message") or "Daily restart").strip()[:60]
        if not re.match(r"^[\w .,!'-]*$", message):
            raise Invalid("Keep the restart message to letters, digits and simple punctuation.")
        params["message"] = message
    store.save_schedule(cluster_id, kind, at, days, params, bool(values.get("enabled")),
                        scheduler.next_run(at, days, time.time()))


def pin_backup(backup_id: int, pinned: bool) -> None:
    if store.backup(backup_id) is None:
        raise Invalid("No such backup.")
    store.set_backup(backup_id, pinned=1 if pinned else 0)
