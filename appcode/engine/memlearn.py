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
    if override:
        return int(override)
    recent = [p for p in list(peaks)[:RECENT_RUNS] if p]
    learned = int(max(recent) * settings.need_margin) if recent else 0
    return max(settings.default_need, learned)


def gb_to_bytes(value: float) -> int:
    """ASA's "(16.91GB Mem)" is binary gigabytes."""
    return int(float(value) * GiB)
