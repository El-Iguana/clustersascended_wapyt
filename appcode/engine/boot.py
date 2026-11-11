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
        try:
            poller.tick()   # a first reading before any request arrives
        except Exception as exc:  # noqa: BLE001
            print(f"[clusters-ascended] first poll failed: {exc!r}", flush=True)
        poller.start()
        supervisor.start_workers()
        _started = True


def started() -> bool:
    return _started
