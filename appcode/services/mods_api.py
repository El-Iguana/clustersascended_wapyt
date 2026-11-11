"""BFF: a cluster's mods (by CurseForge id) and the names players know them by."""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class ModsApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def cluster(self, cluster_id: str) -> dict:
        from engine import views

        data = views.mods_detail(str(cluster_id))
        return {"ok": True, **data} if data else {"ok": False, "error": "Unknown cluster."}

    def save_cluster_mods(self, cluster_id: str, mod_ids: list, names: dict) -> dict:
        """The cluster's mods in load order, plus any names typed with them."""
        from engine import store, views
        from services.auth import actor

        try:
            for mod_id, name in (names or {}).items():
                views.name_mod(mod_id, name)
            result = views.save_cluster(str(cluster_id), {"mod_ids": list(mod_ids or [])})
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "cluster mods", str(cluster_id), {"mod_ids": list(mod_ids or [])})
        return {"ok": True, **result}

    def rename(self, mod_id: str, name: str) -> dict:
        from engine import views

        try:
            views.name_mod(str(mod_id), str(name or ""))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True}
