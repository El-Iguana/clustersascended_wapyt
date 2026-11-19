"""
Memory over time, for the charts. The poller takes a reading a minute: each
running map's memory (the same figure the dashboard shows), everything else
on the host, and the host's total. Readings are kept for 14 days.

The charts show the highest reading in each slice of time (a few minutes to an
hour, by range), because peaks are what admission control and the watchdog
care about. A slice with no host reading (the app was not running) is a gap;
a map with no reading in a covered slice was not running (0).
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from engine import state, store
from engine.models import GiB

SAMPLE_S = 60
KEEP_S = 14 * 86400
HOST, OTHER = "_host", "_other"
# range -> (span, slice), about 180-340 points each
RANGES = {"6h": (6 * 3600, 120), "24h": (86400, 300), "7d": (7 * 86400, 1800), "14d": (14 * 86400, 3600)}
DEFAULT_RANGE = "24h"

_last = {"sample": 0.0, "prune": 0.0}


def maybe_record(now: Optional[float] = None) -> bool:
    """Take a reading if a minute has passed (the poller calls this every tick)."""
    now = now or time.time()
    if now - _last["sample"] < SAMPLE_S:
        return False
    snap = state.snapshot()
    host = snap.host
    if host is None or not host.mem_total:
        return False
    rows, managed = [], 0
    for v in snap.servers.values():
        if v.actual:
            rows.append((v.id, v.actual))
            managed += v.actual
    rows += [(HOST, host.used_total), (OTHER, max(0, host.used_total - managed))]
    store.add_mem_samples(int(now), rows)
    _last["sample"] = now
    if now - _last["prune"] >= 3600:
        store.prune_mem_samples(now - KEEP_S)
        _last["prune"] = now
    return True


def _label(ts: int, span: int) -> str:
    t = datetime.fromtimestamp(ts)
    return t.strftime("%H:%M") if span <= 86400 else t.strftime("%a %d %H:%M")


def _gib(value: Optional[int]) -> Optional[float]:
    return None if value is None else round(value / GiB, 2)


def _frame(range_key: str, now: Optional[float], servers: Optional[list] = None):
    span, step = RANGES.get(range_key) or RANGES[DEFAULT_RANGE]
    now = int(now or time.time())
    start = (now - span) // step * step + step
    buckets = list(range(start, now // step * step + step, step))
    by: dict = {}
    for b, sid, total in store.mem_sample_buckets(start, step, servers):
        by.setdefault(sid, {})[b] = total
    covered = by.get(HOST, {})

    def series(sid: str) -> list:
        mine = by.get(sid, {})
        return [_gib(mine.get(b, 0)) if b in covered else None for b in buckets]

    first = store.first_mem_sample()
    return {"range": range_key if range_key in RANGES else DEFAULT_RANGE, "step_s": step,
            "labels": [_label(b, span) for b in buckets],
            "since": datetime.fromtimestamp(first).strftime("%a %d %b %H:%M") if first else None,
            "partial": bool(first is None or first > start)}, series, covered, buckets


def overview(range_key: str = DEFAULT_RANGE, now: Optional[float] = None) -> dict:
    """Every map (stacked), everything else, and the host's RAM and budget."""
    snap = state.snapshot()
    settings, host = snap.settings, snap.host
    out, series, covered, buckets = _frame(range_key, now)
    maps, position = [], 0
    for c in store.clusters():
        for s in store.servers(c.id):
            values = series(s.id)
            if any(values):          # a map that never ran in the range is left out
                maps.append({"id": s.id, "name": s.name, "cluster": c.name, "colour": position, "values": values})
            position += 1            # the dashboard's colour order, so a map keeps its colour
    peak = max(((covered[b], b) for b in buckets if b in covered), default=None)
    budget = host.mem_total + settings.swap_allowance - settings.reserve if host else 0
    return {**out, "maps": maps, "other": series(OTHER),
            "ram": _gib(host.mem_total if host else 0), "budget": _gib(budget),
            "peak": {"gib": _gib(peak[0]), "label": _label(peak[1], RANGES[out["range"]][0])} if peak else None,
            "clusters": len(store.clusters())}


def for_server(server_id: str, range_key: str = DEFAULT_RANGE, now: Optional[float] = None) -> Optional[dict]:
    """One map's memory and the running need admission counts for it."""
    s = store.server(server_id)
    if s is None:
        return None
    position = 0
    for c in store.clusters():
        ids = [x.id for x in store.servers(c.id)]
        if s.id in ids:
            position += ids.index(s.id)
            break
        position += len(ids)
    out, series, covered, buckets = _frame(range_key, now, [s.id, HOST])
    values = series(s.id)
    run_need, start_need = state.needs_of(s, store.host_settings())
    highest = max((v for v in values if v is not None), default=None)
    return {**out, "name": s.name, "colour": position, "values": values,
            "need": _gib(run_need), "need_start": _gib(start_need), "highest": highest}
