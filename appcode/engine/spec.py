"""
The container a map runs in, built from its effective settings. Pure.

Matches what podman-compose made for the POK image (host network, the three
bind mounts, the same env), with the app's own choices on top:

* ``--restart no``: only the app restarts a map, through admission control.
* labels ``io.clustersascended.*`` mark ownership, and ``spec`` holds a hash
  of everything else, so a changed setting shows until the map is recreated.
* ``--env-file``: passwords stay out of ``ps`` and ``inspect`` output.
* ``--stop-timeout``: ASA needs minutes to flush its save.
* ``--oom-score-adj`` by priority: if the kernel ever has to kill, it takes
  the least important map. Rootless podman can only raise it (from 200).
* logs go to a file the app can tail.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from engine.models import HostSettings

LABEL = "io.clustersascended"
INSTALL_TARGET = "/home/pok/arkserver"
SAVED_TARGET = "/home/pok/arkserver/ShooterGame/Saved"
CLUSTERS_TARGET = "/home/pok/arkserver/ShooterGame/Saved/clusters"


@dataclass
class ContainerSpec:
    name: str
    image: str
    env: dict
    mounts: list            # (host, container)
    labels: dict
    stop_timeout: int
    oom_score_adj: int
    log_path: str
    extra_args: list = field(default_factory=list)

    def digest(self) -> str:
        """Hash of everything but the labels (the hash itself is one)."""
        body = {"image": self.image, "env": self.env, "mounts": self.mounts,
                "stop_timeout": self.stop_timeout, "oom": self.oom_score_adj, "log": self.log_path}
        return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]


def oom_score(priority: int) -> int:
    return min(1000, 300 + 100 * max(0, int(priority) - 1))


def build_env(eff: dict, settings: HostSettings, *, update_pass: bool = False) -> dict:
    args = list(eff.get("custom_args") or [])
    if not eff.get("battleye") and not any(a.lower() == "-nobattleye" for a in args):
        args.append("-NoBattlEye")
    env = {k: str(v) for k, v in (eff.get("extra_env") or {}).items()}
    env.update({
        "PUID": str(settings.puid),
        "PGID": str(settings.pgid),
        "TZ": settings.tz,
        "INSTANCE_NAME": eff["container_name"],
        "MAP_NAME": eff["map_name"],
        "SESSION_NAME": eff["session_name"],
        "SERVER_ADMIN_PASSWORD": eff.get("admin_password") or "",
        "SERVER_PASSWORD": eff.get("server_password") or "",
        "ASA_PORT": str(eff["game_port"]),
        "RCON_PORT": str(eff["rcon_port"]),
        "RCON_ENABLED": "TRUE",
        "MAX_PLAYERS": str(eff.get("max_players") or 70),
        "CLUSTER_ID": eff["cluster_id"],
        "MOD_IDS": ",".join(eff.get("mod_ids") or []),
        "CUSTOM_SERVER_ARGS": " ".join(args),
        # The app sequences starts and updates itself.
        "RANDOM_STARTUP_DELAY": "FALSE",
        "UPDATE_SERVER": "TRUE" if update_pass else "FALSE",
    })
    return env


def build(eff: dict, settings: HostSettings, *, cluster: str, server: str, priority: int,
          log_dir: str, update_pass: bool = False) -> ContainerSpec:
    spec = ContainerSpec(
        name=eff["container_name"],
        image=settings.image,
        env=build_env(eff, settings, update_pass=update_pass),
        mounts=[(eff["install_dir"], INSTALL_TARGET), (eff["saved_dir"], SAVED_TARGET),
                (eff["transfer_dir"], CLUSTERS_TARGET)],
        labels={},
        stop_timeout=settings.stop_timeout_s,
        oom_score_adj=oom_score(priority),
        log_path=str(Path(log_dir) / f"{server}.log"),
    )
    spec.labels = {f"{LABEL}.managed": "1", f"{LABEL}.cluster": cluster,
                   f"{LABEL}.server": server, f"{LABEL}.spec": spec.digest()}
    return spec


def env_file_text(env: dict) -> str:
    for k, v in env.items():
        if "\n" in v or "\r" in v:
            raise ValueError(f"{k} contains a line break")
    return "".join(f"{k}={v}\n" for k, v in sorted(env.items()))


def create_argv(spec: ContainerSpec, env_file: str) -> list[str]:
    argv = ["create", "--name", spec.name, "--network", "host", "--restart", "no",
            "--env-file", env_file, "--stop-timeout", str(spec.stop_timeout),
            "--oom-score-adj", str(spec.oom_score_adj),
            "--log-driver", "k8s-file", "--log-opt", f"path={spec.log_path}",
            "--log-opt", "max-size=20mb"]
    for key, value in sorted(spec.labels.items()):
        argv += ["--label", f"{key}={value}"]
    for host, target in spec.mounts:
        argv += ["-v", f"{host}:{target}"]
    return [*argv, *spec.extra_args, spec.image]
