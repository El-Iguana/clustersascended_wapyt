"""
Players: who is on which map (from the poller's ListPlayers every 30 s),
their join/leave history, kicks, cluster-wide bans and in-game messages.

A ban belongs to the cluster: it is sent to every running map at once and
to each stopped map right after it next starts (``apply_bans``), since ASA
keeps bans per server.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Optional

from engine import rcon, state, store
from engine.models import effective

EOS = re.compile(r"^[0-9a-fA-F]{16,64}$")
_TEXT = re.compile(r"^[^\r\n\x00]{1,200}$")


class Invalid(ValueError):
    pass


def _db():
    return store.conn()


# -- sessions (called by the poller) -------------------------------------------

def record_roster(server_id: str, players: Optional[list], now: float) -> None:
    """Open sessions for new arrivals, close them for those who left."""
    if players is None:            # RCON did not answer: no change rather than everyone "leaving"
        return
    online = {p["id"]: p["name"] for p in players}
    open_rows = _db().execute("SELECT id, eos_id FROM player_sessions WHERE server=? AND left_at IS NULL",
                              (server_id,)).fetchall()
    have = {r["eos_id"]: r["id"] for r in open_rows}
    for eos_id, row_id in have.items():
        if eos_id not in online:
            _db().execute("UPDATE player_sessions SET left_at=? WHERE id=?", (now, row_id))
    for eos_id, name in online.items():
        if eos_id not in have:
            _db().execute("INSERT INTO player_sessions(server, eos_id, name, joined_at) VALUES(?, ?, ?, ?)",
                          (server_id, eos_id, name, now))


def close_all(server_id: str, now: float) -> None:
    """The map stopped: everyone on it left."""
    _db().execute("UPDATE player_sessions SET left_at=? WHERE server=? AND left_at IS NULL", (now, server_id))


def online(server_id: str) -> list[dict]:
    """Who is on now, with when they joined (from the open sessions)."""
    lv = state.live(server_id)
    joined = {r["eos_id"]: r["joined_at"] for r in _db().execute(
        "SELECT eos_id, joined_at FROM player_sessions WHERE server=? AND left_at IS NULL", (server_id,))}
    return [{"name": p["name"], "id": p["id"], "joined_at": joined.get(p["id"])} for p in (lv.players or [])]


def history(server_ids: list[str], limit: int = 200) -> list[dict]:
    if not server_ids:
        return []
    marks = ",".join("?" * len(server_ids))
    rows = _db().execute(
        f"SELECT * FROM player_sessions WHERE server IN ({marks}) ORDER BY joined_at DESC LIMIT ?",
        (*server_ids, limit)).fetchall()
    return [dict(r) for r in rows]


def known_players(server_ids: list[str]) -> list[dict]:
    """Everyone seen on these maps: last name, total time, last seen."""
    if not server_ids:
        return []
    marks = ",".join("?" * len(server_ids))
    now = time.time()
    rows = _db().execute(
        f"SELECT eos_id, name, joined_at, COALESCE(left_at, ?) AS left_at, server FROM player_sessions "
        f"WHERE server IN ({marks}) ORDER BY joined_at", (now, *server_ids)).fetchall()
    out: dict = {}
    for r in rows:
        p = out.setdefault(r["eos_id"], {"id": r["eos_id"], "name": r["name"], "seconds": 0.0,
                                         "last_seen": 0.0, "sessions": 0, "maps": set()})
        p["name"] = r["name"]
        p["seconds"] += max(0.0, r["left_at"] - r["joined_at"])
        p["last_seen"] = max(p["last_seen"], r["left_at"])
        p["sessions"] += 1
        p["maps"].add(r["server"])
    result = []
    for p in out.values():
        p["maps"] = sorted(p["maps"])
        result.append(p)
    return sorted(result, key=lambda p: -p["last_seen"])


def import_webadmin_log(path: str, container_to_server: dict) -> int:
    """The old webadmin's logins.jsonl (join/leave events) as sessions. Idempotent."""
    p = Path(path)
    if not p.is_file():
        return 0
    if _db().execute("SELECT 1 FROM player_sessions WHERE source='webadmin' LIMIT 1").fetchone():
        return 0
    from datetime import datetime

    opened: dict = {}
    count = 0
    for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            e = json.loads(line)
            ts = datetime.fromisoformat(e["ts"]).timestamp()
        except (ValueError, KeyError):
            continue
        server = container_to_server.get(e.get("service"))
        if not server or not e.get("id"):
            continue
        key = (server, e["id"])
        if e.get("event") == "join":
            if key in opened:
                continue
            cur = _db().execute("INSERT INTO player_sessions(server, eos_id, name, joined_at, left_at, source) "
                                "VALUES(?, ?, ?, ?, ?, 'webadmin')", (server, e["id"], e.get("name", ""), ts, ts))
            opened[key] = cur.lastrowid
            count += 1
        elif e.get("event") == "leave" and key in opened:
            _db().execute("UPDATE player_sessions SET left_at=? WHERE id=?", (ts, opened.pop(key)))
    return count


# -- actions -------------------------------------------------------------------

def _rcon(server, cmd: str, timeout: float = 8) -> str:
    cluster = store.cluster(server.cluster)
    password = effective(server, cluster).get("admin_password") or ""
    return rcon.command(server.rcon_port, password, cmd, timeout=timeout)


def _check_id(eos_id: str) -> str:
    eos_id = str(eos_id or "").strip()
    if not EOS.match(eos_id):
        raise Invalid("That is not a player id.")
    return eos_id


def kick(server_id: str, eos_id: str) -> str:
    s = store.server(server_id)
    if s is None:
        raise Invalid("Unknown map.")
    return _rcon(s, f"KickPlayer {_check_id(eos_id)}").strip()


def ban(cluster_id: str, eos_id: str, name: str, reason: str, by: str) -> dict:
    eos_id = _check_id(eos_id)
    if store.cluster(cluster_id) is None:
        raise Invalid("Unknown cluster.")
    _db().execute("INSERT INTO bans(cluster, eos_id, name, reason, banned_at, banned_by) VALUES(?, ?, ?, ?, ?, ?) "
                  "ON CONFLICT(cluster, eos_id) DO UPDATE SET name=excluded.name, reason=excluded.reason, "
                  "banned_at=excluded.banned_at, banned_by=excluded.banned_by, applied='[]'",
                  (cluster_id, eos_id, str(name or "")[:80], str(reason or "")[:200], time.time(), by))
    return apply_bans(cluster_id)


def unban(cluster_id: str, eos_id: str) -> dict:
    eos_id = _check_id(eos_id)
    _db().execute("DELETE FROM bans WHERE cluster=? AND eos_id=?", (cluster_id, eos_id))
    done, later = [], []
    for s in store.servers(cluster_id):
        if state.phase(s.id) == "ready":
            try:
                _rcon(s, f"UnbanPlayer {eos_id}")
                done.append(s.name)
            except rcon.RconError:
                later.append(s.name)
        else:
            later.append(s.name)
    return {"done": done, "later": later}


def bans(cluster_id: str) -> list[dict]:
    rows = _db().execute("SELECT * FROM bans WHERE cluster=? ORDER BY banned_at DESC", (cluster_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["applied"] = json.loads(d["applied"])
        out.append(d)
    return out


def apply_bans(cluster_id: str, only_server: Optional[str] = None) -> dict:
    """Send every ban to each running map that has not had it yet."""
    done, later = [], []
    for s in store.servers(cluster_id):
        if only_server and s.id != only_server:
            continue
        if state.phase(s.id) != "ready" and not only_server:
            later.append(s.name)
            continue
        for b in bans(cluster_id):
            if s.id in b["applied"]:
                continue
            try:
                _rcon(s, f"BanPlayer {b['eos_id']}")
                applied = sorted(set(b["applied"]) | {s.id})
                _db().execute("UPDATE bans SET applied=? WHERE cluster=? AND eos_id=?",
                              (json.dumps(applied), cluster_id, b["eos_id"]))
                if s.name not in done:
                    done.append(s.name)
            except rcon.RconError:
                if s.name not in later:
                    later.append(s.name)
    return {"done": done, "later": later}


def message(server_ids: list[str], text: str, big: bool = False) -> dict:
    """ServerChat (chat line) or Broadcast (big on-screen message) to each running map."""
    text = str(text or "").strip()
    if not _TEXT.match(text):
        raise Invalid("A message is one line, up to 200 characters.")
    sent, skipped = [], []
    for sid in server_ids:
        s = store.server(sid)
        if s is None:
            continue
        if state.phase(sid) != "ready":
            skipped.append(s.name)
            continue
        try:
            _rcon(s, f"{'Broadcast' if big else 'ServerChat'} {text}")
            sent.append(s.name)
        except rcon.RconError:
            skipped.append(s.name)
    return {"sent": sent, "skipped": skipped}
