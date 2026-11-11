"""
Memory readings from the kernel: host totals, pressure, and per-container
usage from cgroup v2. Read from files, never through podman, so a wedged
podman (Podman Desktop holding its database) cannot stall them.

``PROC`` and ``CGROUP`` are module paths so tests can point them at fakes.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from engine.admission import HostMem

PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")


def read_host() -> HostMem:
    values = {}
    for line in (PROC / "meminfo").read_text().splitlines():
        key, _, rest = line.partition(":")
        parts = rest.split()
        if parts:
            values[key] = int(parts[0]) * 1024
    some, full = read_psi()
    return HostMem(
        mem_total=values.get("MemTotal", 0),
        mem_available=values.get("MemAvailable", 0),
        swap_total=values.get("SwapTotal", 0),
        swap_free=values.get("SwapFree", 0),
        psi_some10=some, psi_full10=full,
    )


def read_psi() -> tuple[float, float]:
    """avg10 of /proc/pressure/memory, "some" and "full" (0 when absent)."""
    try:
        text = (PROC / "pressure" / "memory").read_text()
    except OSError:
        return 0.0, 0.0
    out = {"some": 0.0, "full": 0.0}
    for line in text.splitlines():
        kind, *fields = line.split()
        for f in fields:
            if f.startswith("avg10="):
                out[kind] = float(f.split("=", 1)[1])
    return out["some"], out["full"]


def read_container(cgroup_path: str) -> Optional[dict]:
    """
    ``{"anon", "file", "swap", "total"}`` in bytes for a container's cgroup
    (``.State.CgroupPath`` from podman inspect), or None if it is gone.
    ``total`` is anon + swap: what the container really holds.
    """
    if not cgroup_path:
        return None
    base = CGROUP / cgroup_path.lstrip("/")
    try:
        stat = (base / "memory.stat").read_text()
    except OSError:
        return None
    fields = {}
    for line in stat.splitlines():
        key, _, value = line.partition(" ")
        if key in ("anon", "file"):
            fields[key] = int(value)
    try:
        swap = int((base / "memory.swap.current").read_text().strip())
    except (OSError, ValueError):
        swap = 0
    anon = fields.get("anon", 0)
    return {"anon": anon, "file": fields.get("file", 0), "swap": swap, "total": anon + swap}
