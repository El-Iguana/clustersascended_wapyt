"""
BFF: a map's page and its actions. Every action goes through the engine's
queue, which checks memory admission before anything starts.
"""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy

ACTIONS = ("start", "stop", "restart", "make_room_start", "adopt", "rollback", "update", "backup")


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class ServerApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def act(self, server_id: str, action: str, params: dict = None) -> dict:
        from engine import supervisor
        from services.auth import actor

        if action not in ACTIONS:
            return {"ok": False, "error": f"Unknown action: {action}"}
        clean = {}
        p = dict(params or {})
        if action == "update":
            clean = {"force": bool(p.get("force")), "make_room": bool(p.get("make_room"))}
        elif action == "backup":
            clean = {"kind": "manual", "note": str(p.get("note") or "")[:200]}
        return supervisor.enqueue(action, str(server_id), requested_by=actor(self._user), params=clean)

    def start_cluster(self, cluster_id: str) -> dict:
        """Queue a start for each stopped map of the cluster, most important first."""
        from engine import state, store, supervisor
        from services.auth import actor

        queued, refused = [], []
        for s in sorted(store.servers(str(cluster_id)), key=lambda s: s.priority):
            if state.phase(s.id) not in ("stopped", "absent"):
                continue
            r = supervisor.enqueue("start", s.id, requested_by=actor(self._user))
            (queued if r.get("ok") else refused).append(s.name if r.get("ok") else f"{s.name}: {r.get('error')}")
        return {"ok": bool(queued) or not refused, "queued": queued, "refused": refused,
                "error": "; ".join(refused)}

    def update_cluster(self, cluster_id: str) -> dict:
        """Save and stop every running map, then update each map that is behind. None restart."""
        from engine import supervisor
        from services.auth import actor

        return supervisor.enqueue_update_cluster(str(cluster_id), requested_by=actor(self._user))

    def cancel_queue(self) -> dict:
        from engine import supervisor

        return {"ok": True, "cancelled": supervisor.cancel_queue()}

    def detail(self, server_id: str) -> dict:
        from engine import views

        data = views.server_detail(str(server_id))
        return {"ok": True, **data} if data else {"ok": False, "error": "Unknown map."}

    def preview(self, server_id: str) -> dict:
        """Would starting this map be admitted right now? With the breakdown."""
        from engine import admission, state, supervisor

        snap = state.snapshot()
        if server_id not in snap.servers:
            return {"ok": False, "error": "Unknown server."}
        return {"ok": True, "decision": admission.admit(snap, "start", server_id, supervisor.pending()).to_dict()}

    def save(self, server_id: str, values: dict) -> dict:
        from engine import store, views
        from services.auth import actor

        try:
            result = views.save_server(str(server_id), dict(values or {}))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        shown = {k: ("••••••" if "password" in k else v) for k, v in (values or {}).items()}
        store.audit(actor(self._user), "map settings", str(server_id), shown)
        return {"ok": True, **result}

    def clear_hold(self, server_id: str) -> dict:
        from engine import store, views
        from services.auth import actor

        try:
            views.clear_hold(str(server_id))
        except views.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "clear hold", str(server_id))
        return {"ok": True}
