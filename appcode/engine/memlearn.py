"""
How much memory a server needs, learned from its own runs.

    need = override, or max(default_need, margin × highest recent peak)

A run's peak is the most anonymous memory plus swap its container held
(page cache is not counted: ASA maps big pak files that the kernel can drop).
Before a server has runs, the "(16.91GB Mem)" its ShooterGame logs print when
it becomes ready seed the estimate.
"""
from __future__ import annotations

from typing import Iterable, Optional

from engine.models import GiB, HostSettings

RECENT_RUNS = 5


def need(settings: HostSettings, override: Optional[int], peaks: Iterable[int]) -> int:
    """The running need: margin × the highest recent running peak (the default until measured)."""
    if override:
        return int(override)
    recent = [p for p in list(peaks)[:RECENT_RUNS] if p]
    return int(max(recent) * settings.need_margin) if recent else settings.default_need


def needs(settings: HostSettings, override: Optional[int], running: Iterable[int],
          startup: Iterable[int]) -> tuple[int, int]:
    """
    ``(running need, startup need)``. Starting a map is always assumed to take
    more than running it: the startup need is at least startup_factor × the
    running need, and more if a measured start peaked higher (× margin).
    """
    run = need(settings, override, running)
    start = int(run * settings.startup_factor)
    starts = [p for p in list(startup)[:RECENT_RUNS] if p]
    if starts:
        start = max(start, int(max(starts) * settings.need_margin))
    return run, start


def gb_to_bytes(value: float) -> int:
    """ASA's "(16.91GB Mem)" is binary gigabytes."""
    return int(float(value) * GiB)
