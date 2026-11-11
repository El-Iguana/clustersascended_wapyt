"""
Apply a compose import: write the cluster and its servers to the database,
and seed each server's memory need from its old logs' readiness readings.
Never touches containers or files.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from engine import compose_import, logtail, memlearn, paths, store


def preview(compose_path: str, cluster_name: str = "") -> compose_import.ImportPlan:
    path = Path(compose_path).expanduser()
    if not path.is_file():
        return compose_import.ImportPlan(None, errors=[f"No file at {path}."])
    plan = compose_import.plan_import(path.read_text(encoding="utf-8"), str(path.parent),
                                      cluster_name=cluster_name)
    existing = {s.container_name: s for s in store.servers()}
    for s in plan.servers:
        if s.container_name in existing:
            plan.warnings.append(f"{s.container_name} is already imported; importing again updates it.")
    if plan.cluster and store.cluster(plan.cluster.id):
        plan.warnings.append(f"Cluster {plan.cluster.id} exists; importing again updates it.")
    for s in plan.servers:
        if not Path(s.saved_dir).is_dir():
            plan.warnings.append(f"{s.name}: Saved folder {s.saved_dir} not found.")
    return plan


def apply(compose_path: str, cluster_name: str = "", actor: str = "admin") -> dict:
    plan = preview(compose_path, cluster_name)
    if plan.errors or not plan.cluster:
        return {"ok": False, "error": " ".join(plan.errors) or "Nothing to import."}
    backup_dir = paths.data_dir() / "import"
    backup_dir.mkdir(parents=True, exist_ok=True)
    copy = backup_dir / f"docker-compose.{time.strftime('%Y%m%d-%H%M%S')}.yaml"
    shutil.copy2(compose_path, copy)
    os.chmod(copy, 0o600)

    existing = {s.container_name: s for s in store.servers()}
    store.save_cluster(plan.cluster)
    seeded = {}
    for s in plan.servers:
        old = existing.get(s.container_name)
        if old is not None:
            # Keep what the app owns about a server already imported.
            s.id, s.priority, s.autostart = old.id, old.priority, old.autostart
            s.need_override, s.desired = old.need_override, old.desired
        store.save_server(s)
        if old is None:
            readings = logtail.ready_reading_history(Path(s.saved_dir) / "Logs")
            for gb in readings[-5:]:
                run = store.open_run(s.id, time.time() - 86400, source="import")
                store.update_run(run, ready_mem=memlearn.gb_to_bytes(gb), stopped_at=time.time() - 86400,
                                 end_reason="imported reading")
            seeded[s.id] = readings[-5:]
    store.audit(actor, "import", plan.cluster.id, {"compose": str(compose_path), "copy": str(copy),
                                                   "servers": [s.id for s in plan.servers]})
    return {"ok": True, "cluster": plan.cluster.id, "servers": [s.id for s in plan.servers],
            "seeded": seeded, "copy": str(copy)}
