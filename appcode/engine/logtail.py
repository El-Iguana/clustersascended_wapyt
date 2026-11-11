"""
Reading ASA's ShooterGame.log (``<saved_dir>/Logs/ShooterGame.log``).

Lines start with a UTC stamp, ``[2026.11.11-12.15.50:042][232]``. Readiness
only counts a line stamped after the start we are waiting on: the file can
still hold the previous run's "advertising for join" (the old updater matched
exactly that and declared a server ready too early).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_STAMP = re.compile(r"^\[(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})\.(\d{2}):(\d{3})\]")
READY = re.compile(r"Server has completed startup and is now advertising for join\.(?: \(([\d.]+)GB Mem\))?")
HANG = re.compile(r"Hang detected on GameThread")


def stamp(line: str) -> Optional[datetime]:
    m = _STAMP.match(line)
    if not m:
        return None
    y, mo, d, h, mi, s, ms = (int(g) for g in m.groups())
    try:
        return datetime(y, mo, d, h, mi, s, ms * 1000, tzinfo=timezone.utc)
    except ValueError:
        return None


@dataclass
class Event:
    kind: str                 # "ready" | "hang"
    at: datetime
    mem_gb: Optional[float] = None


def scan(text: str, since: datetime) -> list[Event]:
    """Ready and hang events stamped at or after ``since``."""
    events = []
    for line in text.splitlines():
        if "advertising for join" not in line and "Hang detected" not in line:
            continue
        at = stamp(line)
        if at is None or at < since:
            continue
        m = READY.search(line)
        if m:
            events.append(Event("ready", at, float(m.group(1)) if m.group(1) else None))
        elif HANG.search(line):
            events.append(Event("hang", at))
    return events


def log_path(saved_dir: str) -> Path:
    return Path(saved_dir) / "Logs" / "ShooterGame.log"


class Follower:
    """
    Reads a growing log incrementally (by inode and offset), so polling every
    few seconds does not re-read a 10 MB file. A new inode (ASA rotates the
    log on each start) restarts from the top.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.inode = None
        self.offset = 0
        self._partial = ""

    def read_new(self) -> str:
        try:
            st = os.stat(self.path)
        except OSError:
            return ""
        if st.st_ino != self.inode or st.st_size < self.offset:
            self.inode, self.offset, self._partial = st.st_ino, 0, ""
        if st.st_size == self.offset:
            return ""
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            data = fh.read(st.st_size - self.offset)
        self.offset += len(data)
        text = self._partial + data.decode("utf-8", "replace")
        head, sep, tail = text.rpartition("\n")
        self._partial = tail
        return head + sep if sep else ""


def ready_reading_history(logs_dir: Path) -> list[float]:
    """Every "(xx GB Mem)" readiness reading in a Logs folder (seeds needs)."""
    out = []
    for path in sorted(Path(logs_dir).glob("ShooterGame*.log")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in READY.finditer(text):
            if m.group(1):
                out.append(float(m.group(1)))
    return out
