"""
Making a new cluster from nothing: a name, its own ClusterId, its own folders.
It starts with no maps; the add-a-map wizard fills it. Nothing it owns may
overlap another cluster's, so its worlds and transfers stay separate.
"""
from __future__ import annotations

import os
import re
import secrets
import threading
from pathlib import Path

from engine import addmap, store, views
from engine.models import Cluster

Invalid = views.Invalid

# What the app's maps have always run with.
LAUNCH_ARGS = ["-newsaveformat", "-usestore", "-NoTransferFromFiltering"]
CONTAINER_ENV = {"UPDATE_COORDINATION_ROLE": "MASTER", "UPDATE_COORDINATION_PRIORITY": "1"}

_CLUSTER_ID = re.compile(r"^[A-Za-z0-9_-]{3,64}$")
_FOLDER = re.compile(r"^/[A-Za-z0-9._/ -]+$")   # it ends up in shell commands and -v host:target
_lock = threading.Lock()


def base_dir() -> Path:
    return Path.home() / "asa-clusters"


def defaults() -> dict:
    return {"base_dir": str(base_dir()), "max_players": 70}


def _slug(name: str) -> str:
    out = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not out:
        raise Invalid("The cluster's name needs at least one letter or digit.")
    return out


def _folder(value: str) -> Path:
    text = os.path.expanduser(str(value).strip())
    if not _FOLDER.match(text) or ".." in text.split("/"):
        raise Invalid("The folder must be a full path of letters, digits, spaces and . _ - only.")
    return Path(os.path.normpath(text))


def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def plan(values: dict) -> Cluster:
    """Check the form; returns the cluster to create."""
    name = str(values.get("name") or "").strip()
    if not name:
        raise Invalid("The cluster needs a name.")
    if len(name) > 40:
        raise Invalid("The cluster's name is at most 40 characters.")
    cid = _slug(name)
    cluster_id = str(values.get("cluster_id") or "").strip() or cid.replace("-", "") + "cluster"
    if not _CLUSTER_ID.match(cluster_id):
        raise Invalid("The ClusterId is 3 to 64 letters, digits, _ or -.")
    root = _folder(values.get("root_dir") or base_dir() / cid)

    for c in store.clusters():
        if c.id == cid:
            raise Invalid(f"There is already a cluster called {c.name}.")
        if c.cluster_id.lower() == cluster_id.lower():
            raise Invalid(f"{c.name} already uses the ClusterId {c.cluster_id}.")
        if _overlap(root, Path(c.root_dir)) or _overlap(root, Path(c.transfer_dir)):
            raise Invalid(f"That folder overlaps {c.name}'s folder.")
    for s in store.servers():
        if _overlap(root, Path(s.install_dir)) or _overlap(root, Path(s.saved_dir)):
            raise Invalid(f"That folder overlaps the files of {s.name}.")
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise Invalid(f"{root} already exists and is not an empty folder.")
    parent = next(p for p in (root, *root.parents) if p.exists())
    if not os.access(parent, os.W_OK | os.X_OK):
        raise Invalid(f"This app cannot write to {parent}.")

    admin = str(values.get("admin_password") or "").strip()
    d = {
        "mod_ids": [], "custom_args": list(LAUNCH_ARGS), "battleye": False,
        # The app needs one for RCON (saves, stops, players), so there always is one.
        "admin_password": views._password(admin, "Admin password", "") or secrets.token_hex(12),
        "server_password": views._password(str(values.get("server_password") or "").strip(), "Join password", ""),
        "max_players": views._int(values.get("max_players") or 70, "Players per map", 1, 200),
        "extra_env": dict(CONTAINER_ENV),
    }
    # game={}: the app manages the settings from the start (official values until changed).
    return Cluster(id=cid, name=name, cluster_id=cluster_id, transfer_dir=str(root / "Cluster"),
                   root_dir=str(root), defaults=d, game={})


def create(values: dict, actor: str = "admin") -> dict:
    """A new, empty cluster: only with every map on this machine stopped."""
    from engine import scheduler, supervisor

    with _lock, supervisor._enqueue_lock:
        why = addmap.must_stop_first()
        if why:
            raise Invalid(why)
        c = plan(values)
        root = Path(c.root_dir)
        made_root = not root.exists()
        settings = store.host_settings()
        try:
            root.mkdir(parents=True, exist_ok=True)
            # The transfer folder is mounted into every map: the container's user owns it.
            addmap._mkdirs([c.transfer_dir], f"{settings.puid}:{settings.pgid}")
        except (OSError, RuntimeError) as exc:
            if made_root:
                try:
                    root.rmdir()
                except OSError:
                    pass
            raise Invalid(f"Could not make the cluster's folders: {exc}")
        store.save_cluster(c)
        scheduler.ensure_defaults()
    store.audit(actor, "new cluster", c.id, {"name": c.name, "cluster_id": c.cluster_id, "root_dir": c.root_dir})
    return {"cluster": c.id, "name": c.name}


def delete(cluster_id: str, actor: str = "admin") -> dict:
    """Delete a cluster that has no maps. Only empty folders are removed: no file is ever deleted."""
    with _lock:
        c = store.cluster(cluster_id)
        if c is None:
            raise Invalid("Unknown cluster.")
        if store.servers(c.id):
            raise Invalid(f"{c.name} still has maps. Remove them first.")
        store.delete_cluster(c.id)
    kept = []
    root = Path(c.root_dir)
    for d in (Path(c.transfer_dir), root / "Backups", root):
        try:
            d.rmdir()
        except FileNotFoundError:
            pass
        except OSError:
            kept.append(str(d))
    store.audit(actor, "delete cluster", c.id, {"name": c.name, "kept": kept})
    return {"name": c.name, "kept": kept}
