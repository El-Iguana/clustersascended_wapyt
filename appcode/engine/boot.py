"""Start the engine once per process (service.py calls ``start_engine``)."""
from __future__ import annotations

import threading

from engine import poller, store, supervisor

_lock = threading.Lock()
_started = False


def start_engine() -> None:
    global _started
    with _lock:
        if _started:
            return
        store.conn()
        supervisor.recover()
        from engine import scheduler
        scheduler.ensure_defaults()
        _import_old_player_log()
        try:
            poller.tick()   # a first reading before any request arrives
        except Exception as exc:  # noqa: BLE001
            print(f"[clusters-ascended] first poll failed: {exc!r}", flush=True)
        poller.start()
        supervisor.start_workers()
        autostart()
        _started = True


def autostart() -> list[dict]:
    """
    After a reboot (or an app restart), bring back the maps that should be
    running, most important first. Each goes through the queue, so through
    admission and the one-at-a-time gate.
    """
    from engine import state

    results = []
    for s in sorted(store.servers(), key=lambda s: s.priority):
        if not s.autostart or s.desired != "running" or s.held:
            continue
        if state.phase(s.id) not in ("stopped", "absent"):
            continue
        results.append(supervisor.enqueue("start", s.id, requested_by="boot"))
    return results


def started() -> bool:
    return _started


def _import_old_player_log() -> None:
    """Once: the old webadmin's join/leave log (<cluster root>/webadmin/var/logins.jsonl)."""
    from pathlib import Path

    from engine import players

    for c in store.clusters():
        path = Path(c.root_dir) / "webadmin" / "var" / "logins.jsonl"
        mapping = {s.container_name: s.id for s in store.servers(c.id)}
        try:
            n = players.import_webadmin_log(str(path), mapping)
        except Exception as exc:  # noqa: BLE001 - history is a nice-to-have
            print(f"[clusters-ascended] old player log: {exc!r}", flush=True)
            continue
        if n:
            print(f"[clusters-ascended] imported {n} player sessions from {path}", flush=True)
