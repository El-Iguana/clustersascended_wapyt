"""
Schedules: a cluster's nightly backup and daily restart, checked on every
poller tick. Times are the host's local time; ``days`` are 0 (Monday) to 6.

* backup:  every map of the cluster, one at a time on the io lane; a running
           map is saved first; a stopped map whose world has not changed since
           its last backup is skipped; the newest ``keep`` scheduled backups stay.
* restart: every running map, one at a time: in-game countdown (``warn``
           minutes), save, stop, start (through the start queue and admission).

A run missed by more than an hour (the app was down) is skipped for
restarts and caught up for backups.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

from engine import state, store

DEFAULTS = {
    "backup": {"at": "04:00", "enabled": True, "params": {"keep": 7}},
    "restart": {"at": "05:00", "enabled": False, "params": {"warn": [15, 5, 1], "message": "Daily restart"}},
}
ALL_DAYS = [0, 1, 2, 3, 4, 5, 6]
LATE_S = 3600


def parse_at(at: str) -> tuple[int, int]:
    h, _, m = str(at).strip().partition(":")
    hour, minute = int(h), int(m or 0)
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("A time like 04:30, please.")
    return hour, minute


def next_run(at: str, days: list, after: float) -> float:
    """The first matching local time strictly after ``after``."""
    hour, minute = parse_at(at)
    days = sorted(set(days or ALL_DAYS))
    base = datetime.fromtimestamp(after)
    for offset in range(0, 8):
        day = base + timedelta(days=offset)
        candidate = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if candidate.weekday() in days and candidate.timestamp() > after:
            return candidate.timestamp()
    raise ValueError("No day selected.")


def ensure_defaults(now: float | None = None) -> None:
    """Every cluster gets its two schedules (nightly backup on, daily restart off)."""
    now = now or time.time()
    for c in store.clusters():
        have = {s["kind"] for s in store.schedules(c.id)}
        for kind, d in DEFAULTS.items():
            if kind not in have:
                store.save_schedule(c.id, kind, d["at"], ALL_DAYS, dict(d["params"]), d["enabled"],
                                    next_run(d["at"], ALL_DAYS, now))


def tick(now: float | None = None) -> list:
    """Run what is due. Returns what was started (for tests)."""
    now = now or time.time()
    started = []
    for sch in store.schedules():
        if not sch["enabled"] or not sch["next_run"] or now < sch["next_run"]:
            continue
        late = now - sch["next_run"] > LATE_S
        if late and sch["kind"] == "restart":
            result = "Skipped: the app was not running at the time."
        else:
            result = _run(sch)
            started.append((sch["kind"], sch["cluster"], result))
        store.set_schedule(sch["id"], last_run=now, last_result=result[:300],
                           next_run=next_run(sch["at"], sch["days"], now))
    return started


def run_now(schedule_id: int) -> str:
    sch = next((s for s in store.schedules() if s["id"] == schedule_id), None)
    if sch is None:
        return "No such schedule."
    result = _run(sch, by="admin")
    store.set_schedule(schedule_id, last_run=time.time(), last_result=result[:300])
    return result


def _run(sch: dict, by: str = "schedule") -> str:
    from engine import supervisor

    servers = sorted(store.servers(sch["cluster"]), key=lambda s: s.priority)
    queued, notes = [], []
    if sch["kind"] == "backup":
        for s in servers:
            r = supervisor.enqueue("backup", s.id, requested_by=by,
                                   params={"kind": "scheduled", "keep": int(sch["params"].get("keep", 7)),
                                           "skip_unchanged": True})
            (queued if r.get("ok") else notes).append(s.name if r.get("ok") else r.get("error"))
        return f"Backing up {', '.join(queued) or 'nothing'}." + (f" {' '.join(notes)}" if notes else "")
    if sch["kind"] == "restart":
        for s in servers:
            if state.phase(s.id) != "ready":
                continue
            r = supervisor.enqueue("warned_restart", s.id, requested_by=by,
                                   params={"warn": sch["params"].get("warn") or [15, 5, 1],
                                           "message": sch["params"].get("message") or "Daily restart"})
            (queued if r.get("ok") else notes).append(s.name if r.get("ok") else r.get("error"))
        return (f"Restarting {', '.join(queued)} one at a time." if queued else "No map was running.") + \
            (f" {' '.join(notes)}" if notes else "")
    return f"Unknown schedule kind {sch['kind']}."
