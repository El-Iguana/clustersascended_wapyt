"""
The cluster model: a host, its clusters, and the servers (maps) in each.

A cluster is the foundation: it carries the ClusterId, the shared transfer
folder and the defaults every map in it starts from. A server only adds what
differs (``overrides``). ``effective()`` merges the two and is the one input
anything that runs a server reads.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional

GiB = 1024 ** 3

# Phases a server can be in. "Committed" phases hold (or are about to hold)
# their memory, so admission counts them.
PHASES = ("absent", "stopped", "starting", "ready", "stopping", "updating", "unknown")
COMMITTED = frozenset({"starting", "ready", "stopping", "updating"})


@dataclass
class HostSettings:
    """Host-wide rules. One host, one memory budget, whatever the clusters."""

    swap_allowance: int = 12 * GiB      # swap the budget may count on
    reserve: int = 2 * GiB              # never handed out (desktop, this app)
    max_running: int = 2                # belt and braces on top of the budget
    default_need: int = 16 * GiB        # floor for a server's memory need
    need_margin: float = 1.10           # learned peak × this = need
    settle_s: int = 60                  # wait after "ready" before the next start
    ready_timeout_s: int = 1200         # start → advertising for join
    rcon_timeout_s: int = 180           # ready → RCON answers
    stop_timeout_s: int = 300           # SIGTERM grace for ASA to flush its save
    save_timeout_s: int = 180           # RCON saveworld
    image: str = "docker.io/acekorneya/asa_server:2_1_latest"
    puid: int = 7777
    pgid: int = 7777
    tz: str = "America/New_York"

    @classmethod
    def from_dict(cls, data: dict) -> "HostSettings":
        known = {k: v for k, v in (data or {}).items() if k in cls.__dataclass_fields__}
        return cls(**known)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Cluster:
    id: str                      # slug, e.g. "oldmangan"
    name: str
    cluster_id: str              # ASA ClusterId, e.g. "oldmangancluster"
    transfer_dir: str            # shared cross-server transfer folder (host path)
    root_dir: str                # where new maps' installs and saves go
    max_running: Optional[int] = None   # may only be stricter than the host
    memory_cap: Optional[int] = None    # bytes; Σ needs of this cluster
    defaults: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Server:
    id: str
    cluster: str
    name: str
    map_name: str
    session_name: str
    container_name: str
    game_port: int
    rcon_port: int
    install_dir: str
    saved_dir: str
    overrides: dict = field(default_factory=dict)
    priority: int = 5                    # 1 = most important
    autostart: bool = False
    need_override: Optional[int] = None  # bytes
    desired: str = "stopped"

    def to_dict(self) -> dict:
        return asdict(self)


# Settings a cluster defines for its maps and a map may override.
DEFAULT_KEYS = (
    "mod_ids", "custom_args", "battleye", "server_password", "admin_password",
    "max_players", "extra_env",
)


def effective(server: Server, cluster: Cluster) -> dict:
    """
    What a server actually runs with: the cluster's defaults, then the
    server's overrides. Mods append to the cluster's list unless the server
    says ``mods_mode: replace``.
    """
    base = dict(cluster.defaults or {})
    over = dict(server.overrides or {})
    out = {k: base.get(k) for k in DEFAULT_KEYS}
    for key in DEFAULT_KEYS:
        if key == "mod_ids" or key == "extra_env":
            continue
        if key in over and over[key] is not None:
            out[key] = over[key]
    cluster_mods = list(base.get("mod_ids") or [])
    own_mods = list(over.get("mod_ids") or [])
    if over.get("mods_mode") == "replace":
        mods = own_mods
    else:
        mods = cluster_mods + [m for m in own_mods if m not in cluster_mods]
    out["mod_ids"] = mods
    out["extra_env"] = {**(base.get("extra_env") or {}), **(over.get("extra_env") or {})}
    out["custom_args"] = list(out.get("custom_args") or [])
    out.update(
        cluster_id=cluster.cluster_id, transfer_dir=cluster.transfer_dir,
        map_name=server.map_name, session_name=server.session_name,
        game_port=server.game_port, rcon_port=server.rcon_port,
        install_dir=server.install_dir, saved_dir=server.saved_dir,
        container_name=server.container_name,
    )
    return out
