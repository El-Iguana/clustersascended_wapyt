"""BFF: a cluster's shared settings (the defaults its maps start from)."""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class ClusterApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def detail(self, cluster_id: str) -> dict:
        from engine import views

        data = views.cluster_detail(str(cluster_id))
        return {"ok": True, **data} if data else {"ok": False, "error": "Unknown cluster."}

    def save(self, cluster_id: str, values: dict) -> dict:
        from engine import store, views
        from services.auth import actor

        try:
            result = views.save_cluster(str(cluster_id), dict(values or {}))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        shown = {k: ("••••••" if "password" in k else v) for k, v in (values or {}).items()}
        store.audit(actor(self._user), "cluster settings", str(cluster_id), shown)
        return {"ok": True, **result}

    def add_map_info(self, cluster_id: str) -> dict:
        """Defaults and requirements for the add-a-map wizard."""
        from engine import addmap

        try:
            return {"ok": True, **addmap.suggest(str(cluster_id))}
        except addmap.Invalid as exc:
            return {"ok": False, "error": str(exc)}

    def add_map(self, cluster_id: str, values: dict) -> dict:
        from engine import supervisor
        from services.auth import actor

        return supervisor.enqueue_add_map(str(cluster_id), dict(values or {}), requested_by=actor(self._user))

    def stop_all(self) -> dict:
        """Save and stop every running map on this machine (for the wizard)."""
        from engine import state, store, supervisor
        from engine.models import COMMITTED
        from services.auth import actor

        queued = []
        for s in store.servers():
            if state.phase(s.id) in COMMITTED:
                r = supervisor.enqueue("stop", s.id, requested_by=actor(self._user))
                if r.get("ok"):
                    queued.append(s.name)
        return {"ok": True, "queued": queued}
