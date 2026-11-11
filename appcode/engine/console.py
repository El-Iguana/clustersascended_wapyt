"""
The console: a map's game log (ShooterGame.log), read a piece at a time by
the page, and admin commands over RCON.

The page asks for what is new since its last ``(inode, offset)``; ASA starts
a new log file on each boot (a new inode), and the page then starts over.
"""
from __future__ import annotations

import os
import re

from engine import logtail, rcon, store
from engine.models import effective

FIRST_BYTES = 48 * 1024     # what a page sees when it opens the console
MAX_BYTES = 256 * 1024      # most sent in one answer
_CMD = re.compile(r"^[^\r\n\x00]{1,500}$")


def tail(server, inode: int | None, offset: int | None) -> dict:
    path = logtail.log_path(server.saved_dir)
    try:
        st = os.stat(path)
    except OSError:
        return {"text": "", "inode": None, "offset": 0, "reset": inode is not None}
    reset = inode != st.st_ino or offset is None or offset > st.st_size
    start = max(0, st.st_size - FIRST_BYTES) if reset else offset
    start = max(start, st.st_size - MAX_BYTES)
    with open(path, "rb") as fh:
        fh.seek(start)
        data = fh.read(st.st_size - start)
    text = data.decode("utf-8", "replace")
    if reset and start > 0:
        text = text.split("\n", 1)[-1]          # drop a partial first line
    return {"text": text, "inode": st.st_ino, "offset": st.st_size, "reset": reset}


def command(server, cmd: str) -> str:
    cmd = str(cmd or "").strip()
    if not _CMD.match(cmd):
        raise ValueError("A command is one line, up to 500 characters.")
    password = effective(server, store.cluster(server.cluster)).get("admin_password") or ""
    return rcon.command(server.rcon_port, password, cmd, timeout=15).strip()
