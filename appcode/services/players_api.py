"""BFF: players (online, history, kick, cluster bans, messages) and the console."""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class PlayersApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def map(self, server_id: str) -> dict:
        from engine import players, state, store

        s = store.server(str(server_id))
        if s is None:
            return {"ok": False, "error": "Unknown map."}
        others = [x.id for x in store.servers(s.cluster)]
        known = players.known_players(others)
        return {"ok": True, "phase": state.phase(s.id), "online": players.online(s.id),
                "history": players.history([s.id], 100), "known": known[:200],
                "bans": players.bans(s.cluster), "cluster": s.cluster,
                "maps": {x.id: x.name for x in store.servers(s.cluster)}}

    def kick(self, server_id: str, eos_id: str) -> dict:
        from engine import players, rcon, store
        from services.auth import actor

        try:
            reply = players.kick(str(server_id), str(eos_id))
        except (players.Invalid, rcon.RconError) as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "kick", str(server_id), {"eos_id": eos_id})
        return {"ok": True, "reply": reply}

    def ban(self, cluster_id: str, eos_id: str, name: str, reason: str) -> dict:
        from engine import players, store
        from services.auth import actor

        try:
            result = players.ban(str(cluster_id), str(eos_id), str(name or ""), str(reason or ""),
                                 actor(self._user))
        except players.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "ban", str(cluster_id), {"eos_id": eos_id, "name": name, "reason": reason})
        return {"ok": True, **result}

    def unban(self, cluster_id: str, eos_id: str) -> dict:
        from engine import players, store
        from services.auth import actor

        try:
            result = players.unban(str(cluster_id), str(eos_id))
        except players.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "unban", str(cluster_id), {"eos_id": eos_id})
        return {"ok": True, **result}

    def message(self, target: str, target_id: str, text: str, big: bool = False) -> dict:
        """``target``: "map" or "cluster"."""
        from engine import players, store
        from services.auth import actor

        ids = [str(target_id)] if target == "map" else [s.id for s in store.servers(str(target_id))]
        try:
            result = players.message(ids, str(text or ""), bool(big))
        except players.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "message", f"{target}:{target_id}", {"text": text, "big": bool(big)})
        if not result["sent"]:
            return {"ok": False, "error": "No running map to send it to.", **result}
        return {"ok": True, **result}

    def console(self, server_id: str, inode: int = 0, offset: int = -1) -> dict:
        from engine import console, store

        s = store.server(str(server_id))
        if s is None:
            return {"ok": False, "error": "Unknown map."}
        return {"ok": True, **console.tail(s, int(inode) or None, None if int(offset) < 0 else int(offset))}

    def command(self, server_id: str, cmd: str) -> dict:
        from engine import console, rcon, state, store
        from services.auth import actor

        s = store.server(str(server_id))
        if s is None:
            return {"ok": False, "error": "Unknown map."}
        if state.phase(s.id) not in ("ready", "starting"):
            return {"ok": False, "error": f"{s.name} is not running."}
        try:
            reply = console.command(s, str(cmd or ""))
        except (ValueError, rcon.RconError) as exc:
            return {"ok": False, "error": str(exc)}
        store.audit(actor(self._user), "console", s.id, {"cmd": str(cmd)[:200]})
        return {"ok": True, "reply": reply or "(no reply)"}
