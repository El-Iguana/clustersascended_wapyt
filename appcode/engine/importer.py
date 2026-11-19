"""
Apply a compose import: write the cluster and its servers to the database,
and seed each server's memory need from its old logs' readiness readings.
Never touches containers or files.

The app owns what it has imported. Importing a file again only adds maps that
are not here yet; the cluster and its existing maps stay as they are. Anything
that would collide with another cluster refuses the whole import.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import time
from pathlib import Path

from engine import compose_import, logtail, memlearn, paths, store


def _reconcile(plan: compose_import.ImportPlan) -> None:
    """Fit the plan to what is already here: plan.servers becomes the maps to add."""
    c = plan.cluster
    clusters = store.clusters()
    names = {x.id: x.name for x in clusters}
    target = next((x for x in clusters
                   if x.cluster_id == c.cluster_id and x.transfer_dir == c.transfer_dir), None)
    if target:
        plan.cluster = c = target
        plan.warnings.append(f"{target.name} is already here: its settings stay as they are.")
    else:
        for x in clusters:
            if x.id == c.id:
                plan.errors.append(f"There is already a cluster called {x.name}. Type another cluster name.")
            if x.cluster_id == c.cluster_id:
                plan.errors.append(f"{x.name} already uses the ClusterId {c.cluster_id}.")
            if x.transfer_dir == c.transfer_dir:
                plan.errors.append(f"{x.name} already uses the transfer folder {c.transfer_dir}.")

    existing = store.servers()
    by_container = {s.container_name: s for s in existing}
    by_id = {s.id: s for s in existing}
    by_saved = {s.saved_dir: s for s in existing}
    ports = {p: s for s in existing for p in (s.game_port, s.rcon_port)}
    here = {s.map_name for s in existing if s.cluster == c.id}
    seen_ids, seen_containers, new = set(), set(), []

    def where(s) -> str:
        return f"{s.name} in {names.get(s.cluster, s.cluster)}"

    for s in plan.servers:
        s.cluster = c.id
        old = by_container.get(s.container_name)
        if old is not None and old.cluster == c.id:
            plan.warnings.append(f"{old.name} is already imported and stays as it is.")
            continue
        if old is not None:
            plan.errors.append(f"Container {s.container_name} already belongs to {where(old)}.")
            continue
        if s.id in by_id:
            plan.errors.append(f"The service name of {s.name} ({s.id}) is already used by {where(by_id[s.id])}.")
        if s.id in seen_ids or s.container_name in seen_containers:
            plan.errors.append(f"{s.name}: two services in this file have the same name.")
        seen_ids.add(s.id)
        seen_containers.add(s.container_name)
        for p in (s.game_port, s.rcon_port):
            if p in ports:
                plan.errors.append(f"{s.name}: port {p} belongs to {where(ports[p])}.")
        if s.saved_dir in by_saved:
            plan.errors.append(f"{s.name}: its Saved folder belongs to {where(by_saved[s.saved_dir])}.")
        if s.map_name in here:
            plan.errors.append(f"{s.name} is already in {c.name}.")
        new.append(s)

    first = max([s.priority for s in existing if s.cluster == c.id] or [0]) + 1
    for i, s in enumerate(new):
        s.priority = first + i
    plan.servers = new
    if not new and not plan.errors:
        plan.errors.append("Every map in this file is already imported: nothing to add.")


def preview(compose_path: str, cluster_name: str = "") -> compose_import.ImportPlan:
    path = Path(compose_path).expanduser()
    if not path.is_file():
        return compose_import.ImportPlan(None, errors=[f"No file at {path}."])
    plan = compose_import.plan_import(path.read_text(encoding="utf-8"), str(path.parent),
                                      cluster_name=cluster_name)
    if plan.cluster is None or plan.errors:
        return plan
    _reconcile(plan)
    for s in plan.servers:
        if not Path(s.saved_dir).is_dir():
            plan.warnings.append(f"{s.name}: Saved folder {s.saved_dir} not found.")
    return plan


def apply(compose_path: str, cluster_name: str = "", actor: str = "admin") -> dict:
    plan = preview(compose_path, cluster_name)
    if plan.errors or not plan.cluster:
        return {"ok": False, "error": " ".join(plan.errors) or "Nothing to import."}
    from engine import addmap, supervisor

    why = addmap.must_stop_first()     # checked again under the lock below
    if why:
        return {"ok": False, "error": why}
    backup_dir = paths.data_dir() / "import"
    backup_dir.mkdir(parents=True, exist_ok=True)
    copy = backup_dir / f"docker-compose.{time.strftime('%Y%m%d-%H%M%S')}.yaml"
    shutil.copy2(compose_path, copy)
    os.chmod(copy, 0o600)

    with supervisor._enqueue_lock:     # every map stopped, and none starts while this lands
        why = addmap.must_stop_first()
        if why:
            return {"ok": False, "error": why}
        try:
            with store.transaction():          # all of it or none of it
                if store.cluster(plan.cluster.id) is None:
                    store.save_cluster(plan.cluster)
                for s in plan.servers:
                    store.save_server(s)
        except sqlite3.IntegrityError as exc:
            return {"ok": False, "error": f"Nothing was imported: {exc}"}
    seeded = {}
    for s in plan.servers:
        readings = logtail.ready_reading_history(Path(s.saved_dir) / "Logs")
        for gb in readings[-5:]:
            run = store.open_run(s.id, time.time() - 86400, source="import")
            store.update_run(run, ready_mem=memlearn.gb_to_bytes(gb), stopped_at=time.time() - 86400,
                             end_reason="imported reading")
        seeded[s.id] = readings[-5:]
    from engine import scheduler
    scheduler.ensure_defaults()
    store.audit(actor, "import", plan.cluster.id, {"compose": str(compose_path), "copy": str(copy),
                                                   "servers": [s.id for s in plan.servers]})
    return {"ok": True, "cluster": plan.cluster.id, "servers": [s.id for s in plan.servers],
            "seeded": seeded, "copy": str(copy)}
