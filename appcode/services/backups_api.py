"""BFF: world backups, restores and the cluster's nightly backup / daily restart schedules."""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class BackupsApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def cluster(self, cluster_id: str) -> dict:
        from engine import views

        data = views.backups_detail(str(cluster_id))
        return {"ok": True, **data} if data else {"ok": False, "error": "Unknown cluster."}

    def save_schedule(self, cluster_id: str, kind: str, values: dict) -> dict:
        from engine import store, views
        from services.auth import actor

        try:
            views.save_schedule(str(cluster_id), str(kind), dict(values or {}))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), f"{kind} schedule", str(cluster_id), dict(values or {}))
        return {"ok": True}

    def run_now(self, schedule_id: int) -> dict:
        from engine import scheduler

        return {"ok": True, "result": scheduler.run_now(int(schedule_id))}

    def backup_all(self, cluster_id: str) -> dict:
        from engine import store, supervisor
        from services.auth import actor

        queued, errors = [], []
        for s in sorted(store.servers(str(cluster_id)), key=lambda s: s.priority):
            r = supervisor.enqueue("backup", s.id, requested_by=actor(self._user), params={"kind": "manual"})
            (queued if r.get("ok") else errors).append(s.name if r.get("ok") else r.get("error"))
        return {"ok": bool(queued), "queued": queued, "error": " ".join(errors)}

    def pin(self, backup_id: int, pinned: bool) -> dict:
        from engine import views

        try:
            views.pin_backup(int(backup_id), bool(pinned))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}

    def delete(self, backup_id: int) -> dict:
        from engine import backups, store
        from services.auth import actor

        try:
            backups.delete(int(backup_id))
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "delete backup", str(backup_id))
        return {"ok": True}

    def restore(self, backup_id: int) -> dict:
        from engine import store, supervisor
        from services.auth import actor

        b = store.backup(int(backup_id))
        if b is None:
            return {"ok": False, "error": "No such backup."}
        return supervisor.enqueue("restore", b["server"], requested_by=actor(self._user),
                                  params={"backup_id": int(backup_id)})
