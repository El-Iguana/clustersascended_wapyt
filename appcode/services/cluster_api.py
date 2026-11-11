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
