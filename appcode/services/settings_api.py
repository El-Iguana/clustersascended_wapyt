"""
BFF: game settings (rates, breeding, stacks, rules) for a cluster and its
maps. Saving stores them; the engine writes them into each map's ini right
before it starts.
"""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class SettingsApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def cluster(self, cluster_id: str) -> dict:
        from engine import views

        data = views.game_detail(str(cluster_id))
        return {"ok": True, **data} if data else {"ok": False, "error": "Unknown cluster."}

    def save_cluster(self, cluster_id: str, values: dict) -> dict:
        from engine import store, views
        from services.auth import actor

        try:
            result = views.save_game(str(cluster_id), dict(values or {}))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "game settings", str(cluster_id), {"differs": result["differs"]})
        return {"ok": True, **result}

    def import_from_maps(self, cluster_id: str) -> dict:
        from engine import store, views
        from services.auth import actor

        try:
            result = views.import_game(str(cluster_id))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "import game settings", str(cluster_id), result)
        return {"ok": True, **result}

    def save_map(self, server_id: str, overrides: dict) -> dict:
        from engine import store, views
        from services.auth import actor

        try:
            result = views.save_map_game(str(server_id), dict(overrides or {}))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "map game settings", str(server_id), {"keys": sorted(overrides or {})})
        return {"ok": True, **result}

    def restart_running(self, cluster_id: str) -> dict:
        """Restart the cluster's running maps (one at a time) so new settings apply."""
        from engine import state, store, supervisor
        from services.auth import actor

        queued, errors = [], []
        for s in store.servers(str(cluster_id)):
            if state.phase(s.id) == "ready":
                r = supervisor.enqueue("restart", s.id, requested_by=actor(self._user))
                (queued if r.get("ok") else errors).append(s.name if r.get("ok") else r.get("error"))
        return {"ok": not errors, "queued": queued, "error": " ".join(errors)}

    def stacks(self, cluster_id: str) -> dict:
        from engine import views

        data = views.stacks_detail(str(cluster_id))
        return {"ok": True, **data} if data else {"ok": False, "error": "Unknown cluster."}

    def save_stacks(self, cluster_id: str, items: dict) -> dict:
        from engine import store, views
        from services.auth import actor

        try:
            result = views.save_stacks(str(cluster_id), dict(items or {}))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "stack sizes", str(cluster_id), {"count": result["count"]})
        return {"ok": True, **result}
