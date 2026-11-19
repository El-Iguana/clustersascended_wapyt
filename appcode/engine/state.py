"""
Live state: what each server is doing right now, and the host's memory.

The poller writes it; lifecycle jobs set short-lived phase overrides
("starting" before podman reports the container running, "stopping" while a
graceful stop runs); everything else reads ``snapshot()`` or ``view()``.
"""
from __future__ import annotations

import copy
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

from engine import memlearn, store
from engine.admission import HostMem, ServerView, Snapshot

_lock = threading.RLock()
_refresh = threading.Event()


@dataclass
class Live:
    phase: str = "unknown"
    container_state: str = ""
    container_id: str = ""
    exit_code: Optional[int] = None
    started_at: float = 0.0
    ready_at: Optional[float] = None
    ready_mem: Optional[float] = None       # GiB, from the log's "(xx GB Mem)"
    hang: bool = False
    cgroup: str = ""
    mem: Optional[dict] = None              # anon/file/swap/total bytes
    peak: int = 0                           # this run's highest total
    players: Optional[list] = None
    players_at: float = 0.0
    run_id: Optional[int] = None
    note: str = ""
    extra: dict = field(default_factory=dict)


_live: dict[str, Live] = {}
_host: Optional[HostMem] = None
_overrides: dict[str, str] = {}
_end_reasons: dict[str, str] = {}
_app_starts: set[str] = set()
_updated_at = 0.0


def forget(server_id: str) -> None:
    """A removed map."""
    with _lock:
        _live.pop(server_id, None)
        _overrides.pop(server_id, None)
        _end_reasons.pop(server_id, None)
        _app_starts.discard(server_id)


def live(server_id: str) -> Live:
    with _lock:
        return copy.deepcopy(_live.get(server_id) or Live())


def mutate(server_id: str) -> Live:
    """The live record itself (poller use; hold no reference across ticks)."""
    with _lock:
        return _live.setdefault(server_id, Live())


def set_host(host: HostMem) -> None:
    global _host, _updated_at
    with _lock:
        _host = host
        _updated_at = time.time()


def host() -> Optional[HostMem]:
    with _lock:
        return copy.copy(_host)


def set_override(server_id: str, phase: Optional[str]) -> None:
    with _lock:
        if phase:
            _overrides[server_id] = phase
        else:
            _overrides.pop(server_id, None)


def override(server_id: str) -> Optional[str]:
    with _lock:
        return _overrides.get(server_id)


def set_end_reason(server_id: str, reason: str) -> None:
    with _lock:
        _end_reasons[server_id] = reason


def pop_end_reason(server_id: str) -> Optional[str]:
    with _lock:
        return _end_reasons.pop(server_id, None)


def mark_app_start(server_id: str) -> None:
    with _lock:
        _app_starts.add(server_id)


def pop_app_start(server_id: str) -> bool:
    with _lock:
        if server_id in _app_starts:
            _app_starts.discard(server_id)
            return True
        return False


def forget_run(server_id: str) -> None:
    """Before a start: drop the previous run's readiness so only a fresh line counts."""
    with _lock:
        lv = _live.setdefault(server_id, Live())
        lv.started_at, lv.ready_at, lv.ready_mem, lv.hang = 0.0, None, None, False


def request_refresh() -> None:
    """Ask the poller to re-list containers on its next tick."""
    _refresh.set()


def refresh_requested() -> bool:
    if _refresh.is_set():
        _refresh.clear()
        return True
    return False


def phase(server_id: str) -> str:
    with _lock:
        return _overrides.get(server_id) or (_live.get(server_id) or Live()).phase


def needs_of(server, settings) -> tuple[int, int]:
    """(running need, startup need) from the map's measured runs."""
    running, startup = store.need_samples(server.id)
    return memlearn.needs(settings, server.need_override, running, startup)


def need_of(server, settings) -> int:
    return needs_of(server, settings)[0]


def snapshot() -> Snapshot:
    """The admission input: settings, host memory, every server's phase/need/actual."""
    settings = store.host_settings()
    clusters = {c.id: c for c in store.clusters()}
    views = {}
    with _lock:
        host_mem = copy.copy(_host)
        for s in store.servers():
            lv = _live.get(s.id) or Live()
            running = lv.container_state == "running"
            run_need, start_need = needs_of(s, settings)
            views[s.id] = ServerView(
                id=s.id, name=s.name, cluster=s.cluster, priority=s.priority,
                phase=_overrides.get(s.id) or lv.phase,
                need=run_need, need_start=start_need,
                actual=(lv.mem or {}).get("total") if running else None,
                players=len(lv.players or []),
                started_at=lv.started_at,
            )
    if host_mem is None:
        from engine import meminfo
        host_mem = meminfo.read_host()
    return Snapshot(host=host_mem, settings=settings, servers=views, clusters=clusters)


def updated_at() -> float:
    with _lock:
        return _updated_at
