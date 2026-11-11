"""Display helpers, used by the page (and safe on the server: stdlib only)."""
from __future__ import annotations

import time

GiB = 1024 ** 3

PHASE_LABELS = {
    "absent": ("No container", "off"),
    "stopped": ("Stopped", "off"),
    "starting": ("Starting", "warn"),
    "ready": ("Running", "ok"),
    "stopping": ("Stopping", "warn"),
    "updating": ("Updating", "warn"),
    "unknown": ("Unknown", "off"),
}


def gib(n) -> str:
    if n is None:
        return "–"
    return f"{n / GiB:.1f} GiB"


def phase_label(phase: str) -> tuple[str, str]:
    return PHASE_LABELS.get(phase or "unknown", (phase or "?", "off"))


def ago(ts) -> str:
    if not ts:
        return "–"
    s = max(0, int(time.time() - float(ts)))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m ago"
    return f"{s // 86400}d ago"


def duration(seconds) -> str:
    if seconds is None:
        return "–"
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m {s % 60:02d}s"
    if s < 2 * 86400:
        return f"{s // 3600}h {s % 3600 // 60:02d}m"
    return f"{s // 86400}d {s % 86400 // 3600}h"


def clock(ts) -> str:
    if not ts:
        return "–"
    return time.strftime("%b %d %H:%M:%S", time.localtime(float(ts)))
