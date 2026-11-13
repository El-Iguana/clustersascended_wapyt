"""
The podman CLI, with a timeout on every call.

Podman Desktop's ``podman system service`` can hold podman's database and
make ``ps``/``inspect`` hang for minutes. So: every call has a timeout, at
most two calls run at once, and three timeouts in a row mark podman
"degraded" (the UI shows it and the poller backs off) until a call succeeds.

``CA_PODMAN`` points at another binary (tests use a fake).
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
from typing import Optional

_sem = threading.BoundedSemaphore(2)
_lock = threading.Lock()
_timeouts = 0
degraded = False


class PodmanError(RuntimeError):
    pass


class PodmanTimeout(PodmanError):
    pass


def binary() -> str:
    return os.getenv("CA_PODMAN", "podman")


def run(args: list[str], timeout: float = 30, check: bool = True) -> subprocess.CompletedProcess:
    global _timeouts, degraded
    acquired = _sem.acquire(timeout=timeout)
    if not acquired:
        raise PodmanTimeout(f"podman busy: {' '.join(args[:2])}")
    try:
        try:
            cp = subprocess.run([binary(), *args], capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            with _lock:
                _timeouts += 1
                if _timeouts >= 3:
                    degraded = True
            raise PodmanTimeout(f"podman {' '.join(args[:2])} timed out after {timeout:.0f}s") from exc
    finally:
        _sem.release()
    with _lock:
        _timeouts = 0
        degraded = False
    if check and cp.returncode != 0:
        raise PodmanError((cp.stderr or cp.stdout).strip() or f"podman {args[0]} failed ({cp.returncode})")
    return cp


def containers() -> dict[str, dict]:
    """Every container by name: ``{"state", "id", "started_at", "exit_code"}``."""
    cp = run(["ps", "-a", "--format", "json"], timeout=20)
    out = {}
    for c in json.loads(cp.stdout or "[]"):
        for name in c.get("Names") or []:
            out[name] = {
                "state": c.get("State", "unknown"),
                "id": c.get("Id", ""),
                "started_at": float(c.get("StartedAt") or 0),
                "exit_code": c.get("ExitCode"),
                "labels": c.get("Labels") or {},
            }
    return out


def inspect(name: str) -> Optional[dict]:
    cp = run(["inspect", "--type", "container", name], timeout=20, check=False)
    if cp.returncode != 0:
        return None
    data = json.loads(cp.stdout or "[]")
    return data[0] if data else None


def start(name: str) -> None:
    run(["start", name], timeout=120)


def create(argv: list[str]) -> None:
    run(argv, timeout=120)


def remove(name: str) -> None:
    run(["rm", name], timeout=120)


def rename(old: str, new: str) -> None:
    run(["rename", old, new], timeout=60)


def exec_(name: str, argv: list[str], timeout: float = 30) -> int:
    """Run a command inside a running container; returns its exit code."""
    return run(["exec", name, *argv], timeout=timeout, check=False).returncode


def stop(name: str, grace: int) -> str:
    """Stop with a long grace; returns stderr (podman says when it had to SIGKILL)."""
    cp = run(["stop", "-t", str(grace), name], timeout=grace + 120)
    return cp.stderr or ""
