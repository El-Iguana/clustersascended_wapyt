"""
The memory watchdog: act before the kernel's OOM killer does.

Every poller tick it looks at free RAM + free swap and memory pressure
(PSI "full" avg10: the share of time every task was stalled on memory).

* warn:     free below ``warn_free``, or pressure above half of ``psi_crit``
* critical: free below ``crit_free``, or pressure above ``psi_crit``, held for
            ``crit_sustain_s`` → save and stop one map (at most once per
            ``watchdog_cooldown_s``), and hold its autostart until the admin
            starts it again.

The map chosen is one still loading (the likely cause, and nobody is on it
yet), else the least important, emptiest, most recently started one.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from engine.admission import HostMem
from engine.models import HostSettings

_lock = threading.Lock()
_status = {"level": "ok", "message": "", "crit_since": None, "last_action": 0.0, "last_victim": ""}


def status() -> dict:
    with _lock:
        return dict(_status)


def evaluate(host: HostMem, s: HostSettings) -> tuple[str, str]:
    free = host.mem_available + host.swap_free
    gib = 1024 ** 3
    if free < s.crit_free or host.psi_full10 >= s.psi_crit:
        return "critical", (f"Only {free / gib:.1f} GiB of RAM and swap free, memory pressure "
                            f"{host.psi_full10:.0f}%.")
    if free < s.warn_free or host.psi_full10 >= s.psi_crit / 2:
        return "warn", (f"Memory is getting tight: {free / gib:.1f} GiB of RAM and swap free, "
                        f"pressure {host.psi_full10:.0f}%.")
    return "ok", ""


def pick_victim(servers) -> Optional[str]:
    """``servers``: admission ServerViews. Loading maps first, then the least important."""
    live = [v for v in servers if v.phase in ("starting", "ready", "updating")]
    if not live:
        return None
    loading = sorted((v for v in live if v.phase in ("starting", "updating")), key=lambda v: -v.started_at)
    if loading:
        return loading[0].id
    return sorted(live, key=lambda v: (-v.priority, v.players, -v.started_at))[0].id


def tick(host: HostMem, s: HostSettings, servers, now: Optional[float] = None) -> Optional[str]:
    """Update the status; return a server id to stop, or None."""
    now = now or time.time()
    level, message = evaluate(host, s)
    with _lock:
        _status["level"], _status["message"] = level, message
        if level != "critical":
            _status["crit_since"] = None
            return None
        if _status["crit_since"] is None:
            _status["crit_since"] = now
        if not s.watchdog_enabled:
            return None
        if now - _status["crit_since"] < s.crit_sustain_s:
            return None
        if now - _status["last_action"] < s.watchdog_cooldown_s:
            return None
        victim = pick_victim(list(servers))
        if victim:
            _status["last_action"] = now
            _status["last_victim"] = victim
        return victim


def reset() -> None:
    with _lock:
        _status.update(level="ok", message="", crit_since=None, last_action=0.0, last_victim="")
