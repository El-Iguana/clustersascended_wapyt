"""
Import an existing podman-compose ASA cluster (POK image) into the model.

Pure: compose text in, an ``ImportPlan`` out. Applying it only writes the
database; containers and files are not touched. Every service must share one
CLUSTER_ID and transfer folder, or the import is refused: a cluster is one
ClusterId.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from engine.models import Cluster, Server

INSTALL_TARGET = "/home/pok/arkserver"
SAVED_TARGET = "/home/pok/arkserver/ShooterGame/Saved"
CLUSTERS_TARGET = "/home/pok/arkserver/ShooterGame/Saved/clusters"

# Env the model owns; everything else is carried over as extra_env.
_MODELLED = {
    "PUID", "PGID", "TZ", "INSTANCE_NAME", "MAP_NAME", "SESSION_NAME",
    "SERVER_ADMIN_PASSWORD", "SERVER_PASSWORD", "ASA_PORT", "RCON_PORT",
    "MAX_PLAYERS", "CLUSTER_ID", "MOD_IDS", "CUSTOM_SERVER_ARGS",
}

# Known map display names; anything else shows its MAP_NAME.
_MAP_NAMES = {
    "TheIsland_WP": "The Island", "ScorchedEarth_WP": "Scorched Earth",
    "TheCenter_WP": "The Center", "Aberration_WP": "Aberration",
    "Extinction_WP": "Extinction", "Ragnarok_WP": "Ragnarok", "Valguero_WP": "Valguero",
    "Astraeos_WP": "Astraeos", "LostColony_WP": "Lost Colony", "Genesis_WP": "Genesis 1",
    "Genesis2_WP": "Genesis 2", "Fjordur_WP": "Fjordur",
}


@dataclass
class ImportPlan:
    cluster: Cluster | None
    servers: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    running: list = field(default_factory=list)   # filled by the caller

    def to_dict(self, *, hide_secrets: bool = True) -> dict:
        def clean(d: dict) -> dict:
            if not hide_secrets:
                return d
            out = dict(d)
            for k in ("admin_password", "server_password"):
                if out.get(k):
                    out[k] = "••••••"
            return out

        cluster = None
        if self.cluster:
            cluster = self.cluster.to_dict()
            cluster["defaults"] = clean(cluster["defaults"])
        servers = []
        for s in self.servers:
            d = s.to_dict()
            d["overrides"] = clean(d["overrides"])
            servers.append(d)
        return {"cluster": cluster, "servers": servers, "errors": list(self.errors),
                "warnings": list(self.warnings)}


def _env(service: dict) -> dict:
    env = service.get("environment") or {}
    if isinstance(env, dict):
        return {str(k): "" if v is None else str(v) for k, v in env.items()}
    out = {}
    for item in env:
        key, _, value = str(item).partition("=")
        out[key.strip()] = value
    return out


def _mounts(service: dict, base: Path) -> dict:
    """container target -> absolute host path"""
    out = {}
    for vol in service.get("volumes") or []:
        if isinstance(vol, dict):
            src, dst = vol.get("source", ""), vol.get("target", "")
        else:
            src, _, rest = str(vol).partition(":")
            dst = rest.split(":")[0]
        path = Path(src)
        out[dst.rstrip("/")] = str(path if path.is_absolute() else (base / path).resolve())
    return out


def _mods(value: str) -> list:
    return [m.strip() for m in (value or "").split(",") if m.strip()]


def slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s or "server"


def plan_import(compose_text: str, compose_dir: str, *, cluster_name: str = "") -> ImportPlan:
    base = Path(compose_dir)
    try:
        doc = yaml.safe_load(compose_text) or {}
    except yaml.YAMLError as exc:
        return ImportPlan(None, errors=[f"Not valid YAML: {exc}"])
    services = doc.get("services") or {}
    entries = []
    for key, svc in services.items():
        if not isinstance(svc, dict):
            continue
        env = _env(svc)
        if "MAP_NAME" not in env or "ASA_PORT" not in env:
            continue  # not an ASA (POK) service
        entries.append((key, svc, env, _mounts(svc, base)))
    if not entries:
        return ImportPlan(None, errors=["No ASA (POK) services found in this compose file."])

    plan = ImportPlan(None)
    cluster_ids = {e[2].get("CLUSTER_ID", "") for e in entries}
    if len(cluster_ids) != 1 or not next(iter(cluster_ids)):
        plan.errors.append(f"Services must share one CLUSTER_ID; found {sorted(cluster_ids)}.")
    transfer = {e[3].get(CLUSTERS_TARGET, "") for e in entries}
    if len(transfer) != 1 or not next(iter(transfer)):
        plan.errors.append("Services must share one cluster transfer folder "
                           f"(the volume on {CLUSTERS_TARGET}).")
    for key, _svc, _env_, mounts in entries:
        for target in (INSTALL_TARGET, SAVED_TARGET):
            if target not in mounts:
                plan.errors.append(f"{key}: no volume on {target}.")
    if plan.errors:
        return plan

    cluster_id = next(iter(cluster_ids))
    first_env = entries[0][2]

    def shared(name: str) -> str | None:
        values = {e[2].get(name) for e in entries}
        return values.pop() if len(values) == 1 else None

    # The cluster's mods: the list the most servers run (first service wins a tie).
    lists = [_mods(e[2].get("MOD_IDS", "")) for e in entries]
    cluster_mods = max(lists, key=lambda l: (lists.count(l), -lists.index(l)))
    args = (shared("CUSTOM_SERVER_ARGS") or first_env.get("CUSTOM_SERVER_ARGS", "")).split()
    extra_keys = sorted({k for e in entries for k in e[2]} - _MODELLED)
    defaults = {
        "mod_ids": cluster_mods,
        "custom_args": [a for a in args if a.lower() != "-nobattleye"],
        "battleye": not any(a.lower() == "-nobattleye" for a in args),
        "admin_password": shared("SERVER_ADMIN_PASSWORD") or first_env.get("SERVER_ADMIN_PASSWORD", ""),
        "server_password": shared("SERVER_PASSWORD") or "",
        "max_players": int(shared("MAX_PLAYERS") or first_env.get("MAX_PLAYERS", "70") or 70),
        "extra_env": {k: v for k in extra_keys if (v := shared(k)) is not None},
    }
    plan.cluster = Cluster(
        id=slug(cluster_name or cluster_id.removesuffix("cluster") or cluster_id),
        name=cluster_name or cluster_id,
        cluster_id=cluster_id,
        transfer_dir=next(iter(transfer)),
        root_dir=str(base.resolve()),
        defaults=defaults,
    )

    for priority, (key, svc, env, mounts) in enumerate(entries, start=1):
        over: dict = {}
        mods = _mods(env.get("MOD_IDS", ""))
        if mods != cluster_mods:
            if mods[: len(cluster_mods)] == cluster_mods:
                over["mod_ids"] = mods[len(cluster_mods):]
                over["mods_mode"] = "append"
            else:
                # Order matters to ASA: keep this server's exact list.
                over["mod_ids"] = mods
                over["mods_mode"] = "replace"
        for env_key, field_name in (("SERVER_ADMIN_PASSWORD", "admin_password"),
                                    ("SERVER_PASSWORD", "server_password")):
            if env.get(env_key, "") != defaults[field_name]:
                over[field_name] = env.get(env_key, "")
        if int(env.get("MAX_PLAYERS", defaults["max_players"]) or 0) != defaults["max_players"]:
            over["max_players"] = int(env["MAX_PLAYERS"])
        own_args = env.get("CUSTOM_SERVER_ARGS", "").split()
        if own_args != args:
            over["custom_args"] = [a for a in own_args if a.lower() != "-nobattleye"]
            over["battleye"] = not any(a.lower() == "-nobattleye" for a in own_args)
        extra = {k: env[k] for k in extra_keys if k in env and defaults["extra_env"].get(k) != env[k]}
        if extra:
            over["extra_env"] = extra
        map_name = env["MAP_NAME"]
        name = _MAP_NAMES.get(map_name, map_name.removesuffix("_WP"))
        plan.servers.append(Server(
            id=slug(key.removeprefix("asa-")),
            cluster=plan.cluster.id,
            name=name,
            map_name=map_name,
            session_name=env.get("SESSION_NAME", name),
            container_name=str(svc.get("container_name") or key),
            game_port=int(env["ASA_PORT"]),
            rcon_port=int(env.get("RCON_PORT") or 0),
            install_dir=mounts[INSTALL_TARGET],
            saved_dir=mounts[SAVED_TARGET],
            overrides=over,
            priority=priority,
        ))
        if (svc.get("network_mode") or "") != "host":
            plan.warnings.append(f"{key}: not on host networking; the app runs maps with --network host.")
        if not env.get("RCON_PORT"):
            plan.warnings.append(f"{key}: no RCON_PORT; readiness and graceful stops need RCON.")

    ports = [s.game_port for s in plan.servers] + [s.rcon_port for s in plan.servers]
    dupes = {p for p in ports if ports.count(p) > 1}
    if dupes:
        plan.errors.append(f"Ports used twice: {sorted(dupes)}.")
    return plan
