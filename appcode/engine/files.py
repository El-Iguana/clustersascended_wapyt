"""
A map's files, for the Files tab: its Saved folder (world, settings, logs), its
game install and the cluster's transfer folder. Reading is open; writing is for
small text and config files in Saved only, with the map stopped, and keeps the
old copy. Nothing outside those folders can be reached (symlinks included).
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from engine import paths, state, store
from engine.models import COMMITTED

MAX_READ = 2 * 1024 ** 2
MAX_ENTRIES = 2000
TEXT_EXT = {".ini", ".txt", ".log", ".json", ".cfg", ".csv", ".yaml", ".yml", ".md", ".xml"}
EDIT_EXT = {".ini", ".txt", ".json", ".cfg"}
MANAGED = {"gameusersettings.ini", "game.ini"}     # the app rewrites the keys it manages at each start
_SAFE_NAME = re.compile(r"^[A-Za-z0-9._ ()-]+$")    # editable paths end up in a shell command
KEEP_BACKUPS = 10


class Invalid(ValueError):
    pass


def roots(server) -> dict:
    cluster = store.cluster(server.cluster)
    return {
        "saved": ("World and settings", Path(server.saved_dir), True),
        "game": ("Game files", Path(server.install_dir), False),
        "transfers": ("Cluster transfers", Path(cluster.transfer_dir), False),
    }


def _resolve(server_id: str, root_key: str, rel: str):
    server = store.server(server_id)
    if server is None:
        raise Invalid("Unknown map.")
    found = roots(server).get(root_key)
    if found is None:
        raise Invalid("Unknown folder.")
    label, root, writable = found
    parts = [p for p in str(rel or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." or "\x00" in p for p in parts):
        raise Invalid("That path is outside this map's folders.")
    base = root.resolve()
    target = base.joinpath(*parts).resolve()
    if target != base and base not in target.parents:
        raise Invalid("That path is outside this map's folders.")
    return server, base, target, writable, "/".join(parts)


def _crumbs(rel: str) -> list:
    parts = rel.split("/") if rel else []
    return [{"name": p, "path": "/".join(parts[: i + 1])} for i, p in enumerate(parts)]


def listing(server_id: str, root_key: str = "saved", rel: str = "") -> dict:
    server, base, target, writable, rel = _resolve(server_id, root_key, rel)
    if not target.is_dir():
        raise Invalid("That folder does not exist (yet).")
    entries = []
    try:
        for i, child in enumerate(sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))):
            if i >= MAX_ENTRIES:
                break
            try:
                st = child.stat()
            except OSError:
                continue
            entries.append({"name": child.name, "dir": child.is_dir(), "size": st.st_size, "mtime": st.st_mtime,
                            "text": child.suffix.lower() in TEXT_EXT or child.name.startswith("ShooterGame.log")})
    except PermissionError:
        raise Invalid("This app cannot read that folder.")
    return {"roots": [{"key": k, "label": v[0]} for k, v in roots(server).items()], "root": root_key,
            "path": rel, "crumbs": _crumbs(rel), "entries": entries, "more": len(entries) >= MAX_ENTRIES,
            "writable": writable}


def _decode(data: bytes) -> tuple[str, bool]:
    """(text, plain UTF-8?) — ASA's files are UTF-8; a UTF-16 one is shown but not edited."""
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace"), False
    try:
        return data.decode("utf-8"), True
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace"), False


def _why_not(server, writable: bool, target: Path, base: Path, size: int, utf8: bool) -> str:
    if not writable:
        return "This folder is read-only here."
    if target.suffix.lower() == ".log" or target.name.startswith("ShooterGame.log"):
        return "Logs are read-only."
    if target.suffix.lower() not in EDIT_EXT:
        return "Only .ini, .txt, .json and .cfg files can be changed here."
    if size > MAX_READ or not utf8:
        return "This file is too big or not plain text."
    if not all(_SAFE_NAME.match(p) for p in target.relative_to(base).parts):
        return "Its name has characters this app does not write."
    if state.phase(server.id) in COMMITTED:
        return f"Stop {server.name} to change its files: ASA rewrites some of them while it runs."
    return ""


def read(server_id: str, root_key: str, rel: str) -> dict:
    server, base, target, writable, rel = _resolve(server_id, root_key, rel)
    if not target.is_file():
        raise Invalid("That file does not exist.")
    st = target.stat()
    with open(target, "rb") as fh:
        head = fh.read(8192)
        if b"\x00" in head and head[:2] not in (b"\xff\xfe", b"\xfe\xff"):
            return {"path": rel, "name": target.name, "size": st.st_size, "mtime": st.st_mtime, "binary": True,
                    "text": "", "truncated": False, "editable": False, "why_not": "Not a text file."}
        truncated = st.st_size > MAX_READ
        fh.seek(max(0, st.st_size - MAX_READ) if truncated else 0)
        data = fh.read(MAX_READ)
    text, utf8 = _decode(data)
    if truncated:
        text = text.split("\n", 1)[-1]            # start at a whole line
    why = _why_not(server, writable, target, base, st.st_size, utf8)
    note = ("The app writes the game settings it manages into this file each time the map starts: "
            "change those on the Game settings tab, not here." if target.name.lower() in MANAGED else "")
    return {"path": rel, "name": target.name, "size": st.st_size, "mtime": st.st_mtime, "binary": False,
            "text": text, "truncated": truncated, "editable": not why, "why_not": why, "note": note,
            "crlf": "\r\n" in text}


def write(server_id: str, root_key: str, rel: str, text: str, expected_mtime: float, actor: str = "admin") -> dict:
    from engine.game_settings import _write

    server, base, target, writable, rel = _resolve(server_id, root_key, rel)
    if not target.is_file():
        raise Invalid("That file does not exist (this page only changes existing files).")
    st = target.stat()
    old = target.read_bytes()
    old_text, utf8 = _decode(old)
    why = _why_not(server, writable, target, base, st.st_size, utf8)
    if why:
        raise Invalid(why)
    if abs(st.st_mtime - float(expected_mtime or 0)) > 0.001:
        raise Invalid("The file changed since you opened it. Open it again, then make your change.")
    text = str(text or "").replace("\r\n", "\n")
    if "\r\n" in old_text:
        text = text.replace("\n", "\r\n")       # keep Windows line endings (a textarea drops them)
    if len(text.encode("utf-8")) > MAX_READ:
        raise Invalid("That is too much text for one file here.")
    keep = paths.data_dir() / "file-backups" / server.id
    keep.mkdir(parents=True, exist_ok=True)
    stem = rel.replace("/", "__")
    (keep / f"{stem}.{time.strftime('%Y%m%d-%H%M%S')}").write_bytes(old)
    for extra in sorted(keep.glob(f"{stem}.*"))[:-KEEP_BACKUPS]:
        extra.unlink()
    _write(target, text)
    store.audit(actor, "edit file", server.id, {"path": f"{root_key}/{rel}", "bytes": len(text)})
    st = target.stat()
    return {"mtime": st.st_mtime, "size": st.st_size}
