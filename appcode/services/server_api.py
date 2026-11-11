"""
BFF: actions on a map. Every one goes through the engine's queue, which
checks memory admission before anything starts.
"""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy

ACTIONS = ("start", "stop", "restart", "make_room_start")


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class ServerApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def act(self, server_id: str, action: str) -> dict:
        from engine import supervisor
        from services.auth import actor

        if action not in ACTIONS:
            return {"ok": False, "error": f"Unknown action: {action}"}
        return supervisor.enqueue(action, str(server_id), requested_by=actor(self._user))

    def preview(self, server_id: str) -> dict:
        """Would starting this map be admitted right now? With the breakdown."""
        from engine import admission, state, supervisor

        snap = state.snapshot()
        if server_id not in snap.servers:
            return {"ok": False, "error": "Unknown server."}
        return {"ok": True, "decision": admission.admit(snap, "start", server_id, supervisor.pending()).to_dict()}

    def update(self, server_id: str, values: dict) -> dict:
        """Priority, need override (GiB, empty = learned) and autostart."""
        from engine import store
        from engine.models import GiB
        from services.auth import actor

        s = store.server(str(server_id))
        if s is None:
            return {"ok": False, "error": "Unknown server."}
        try:
            if "priority" in values:
                s.priority = max(1, int(values["priority"]))
            if "need_override" in values:
                raw = values["need_override"]
                s.need_override = int(float(raw) * GiB) if raw not in (None, "", 0, "0") else None
            if "autostart" in values:
                s.autostart = bool(values["autostart"])
        except (TypeError, ValueError):
            return {"ok": False, "error": "Numbers only, please."}
        store.save_server(s)
        store.audit(actor(self._user), "server settings", s.id, values)
        return {"ok": True}
