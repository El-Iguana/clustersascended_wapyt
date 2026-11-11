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
