"""
World backups: a map's ``SavedArks/<MAP>`` and its ``Config``, as tar + zstd.

What is backed up is what a restore needs: the live ``<MAP>.ark`` (with
-usestore it holds the survivors too), tribe and tribute files, and the
settings. ASA's own history in the same folder is left out: the date-stamped
``<MAP>_DD.MM.YYYY_HH.MM.SS.ark`` copies, the rolling ``.arkrbf`` files and the
anti-corruption ``.bak``. ASA rotates those every few minutes (deleting old
ones while tar reads would fail the backup), and they make up most of the
size. Old ``.stripped`` / ``.noflags`` folders from the migration are left out
too.

A running map is told to SaveWorld first. Old backups are pruned per kind
(scheduled, manual, pre-update, pre-restore); pinned ones are never pruned.
Restoring (``restore_files``, run by lifecycle with the map stopped) moves
the current world aside to ``Saved/.pre-restore-<time>`` (the last one is
kept) and unpacks the backup in its place.

The files belong to the container's user (uid 7777 inside podman's user
namespace), so tar runs under ``podman unshare``: ownership survives a
restore. Archives go to ``<cluster root>/Backups/<server>/``.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from engine import podman, rcon, state, steam, store
from engine.models import effective

# Kept per map and kind; pinned backups are never pruned.
KEEP = {"scheduled": 7, "manual": 10, "pre-update": 3, "pre-restore": 3}


def backup_dir(cluster, server) -> Path:
    return Path(cluster.root_dir) / "Backups" / server.id


def world_dir(server) -> Path:
    return Path(server.saved_dir) / "SavedArks" / server.map_name


def newest_change(server) -> float:
    """When the map's world files last changed (0 if there are none)."""
    newest = 0.0
    try:
        for entry in os.scandir(world_dir(server)):
            if entry.is_file(follow_symlinks=False):
                newest = max(newest, entry.stat().st_mtime)
    except OSError:
        return 0.0
    return newest


def unchanged_since_last(server) -> bool:
    """True when the world has not been saved since the newest backup."""
    last = store.backups(server.id, 1)
    return bool(last) and newest_change(server) <= last[0]["created"]


# ASA's own history: rotated while it runs, not needed to restore.
EXCLUDES = ("*.stripped*", "*.noflags*", "*.arkrbf", "*_AntiCorruptionBackup.bak",
            "*_[0-9][0-9].[0-9][0-9].[0-9][0-9][0-9][0-9]_[0-9][0-9].[0-9][0-9].[0-9][0-9].ark")
QUIET_S = 5
TAR_TRIES = 3


def wait_quiet(server, quiet_s: float | None = None, max_s: float = 90) -> None:
    """Wait until nothing in the world folder has changed for ``quiet_s`` (a save in progress)."""
    quiet_s = QUIET_S if quiet_s is None else quiet_s
    end = time.time() + max_s
    while time.time() < end:
        if time.time() - newest_change(server) >= quiet_s:
            return
        time.sleep(1)


SAVE_LAND_S = 60


def save_world(server, cluster, log) -> None:
    """
    SaveWorld, then wait for the new save to be on disk: ASA answers "World
    Saved" about a second before the file is written (seen 2026-11-11).
    """
    if state.phase(server.id) != "ready":
        return
    password = effective(server, cluster).get("admin_password") or ""
    asked = time.time()
    try:
        reply = rcon.command(server.rcon_port, password, "SaveWorld", timeout=store.host_settings().save_timeout_s)
        log(f"SaveWorld: {reply.strip() or 'ok'}")
    except rcon.RconError as exc:
        log(f"SaveWorld failed ({exc}); backing up the last save on disk.")
        return
    end = time.time() + SAVE_LAND_S
    while time.time() < end:
        if newest_change(server) >= asked - 1:
            return
        time.sleep(0.5)
    log("The new save did not appear on disk within a minute; backing up the last one there.")


def create(server, cluster, kind: str, log, note: str = "", keep: int | None = None) -> dict:
    saved = Path(server.saved_dir)
    save_world(server, cluster, log)
    world = Path("SavedArks") / server.map_name
    members = [str(p) for p in (world, Path("Config")) if (saved / p).exists()]
    if not members:
        raise RuntimeError(f"Nothing to back up in {saved}.")
    out_dir = backup_dir(cluster, server)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = out_dir / f"{server.id}-{stamp}-{kind}.tar.zst"
    tmp = target.with_suffix(".tmp")
    log(f"Backing up {server.name}: {', '.join(members)} → {target}")
    cmd = [podman.binary(), "unshare", "tar", "--zstd", "-cf", str(tmp), "-C", str(saved),
           *[f"--exclude={p}" for p in EXCLUDES], *members]
    started = time.time()
    for attempt in range(1, TAR_TRIES + 1):
        wait_quiet(server)
        cp = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
        if cp.returncode == 0:
            break
        if tmp.exists():
            tmp.unlink()
        # tar exits 1 when a file changed while it read it: the game saved; try again.
        if cp.returncode == 1 and attempt < TAR_TRIES:
            log(f"The world changed while it was being read; trying again ({attempt + 1} of {TAR_TRIES}).")
            time.sleep(QUIET_S)
            continue
        raise RuntimeError(f"Backup failed: {(cp.stderr or cp.stdout).strip()[:300]}")
    check = subprocess.run(["zstd", "-tq", str(tmp)], capture_output=True, text=True, timeout=1800)
    if check.returncode != 0:
        tmp.unlink()
        raise RuntimeError(f"Backup did not verify: {check.stderr.strip()[:300]}")
    os.replace(tmp, target)
    size = target.stat().st_size
    build = steam.installed_build(server.install_dir)
    backup_id = store.add_backup(server.id, server.cluster, kind, str(target), size, build, note)
    log(f"Backup done: {size / 1024 ** 3:.2f} GiB in {time.time() - started:.0f} s.")
    prune(server, kind, keep if keep is not None else KEEP.get(kind, 10), log)
    return {"id": backup_id, "path": str(target), "size": size}


def prune(server, kind: str, keep: int, log) -> int:
    """Delete this map's oldest unpinned backups of ``kind`` beyond ``keep``."""
    rows = [b for b in store.backups(server.id, 1000) if b["kind"] == kind and not b["pinned"]]
    removed = 0
    for b in rows[max(0, keep):]:
        delete(b["id"], log)
        removed += 1
    return removed


def delete(backup_id: int, log=None) -> None:
    b = store.backup(backup_id)
    if b is None:
        return
    if b["pinned"]:
        raise RuntimeError("That backup is kept forever; unpin it first.")
    Path(b["path"]).unlink(missing_ok=True)
    store.delete_backup_row(backup_id)
    if log:
        log(f"Removed old backup {Path(b['path']).name}.")


def archive_members(archive: str) -> list[str]:
    cp = subprocess.run(["tar", "--zstd", "-tf", archive], capture_output=True, text=True, timeout=600)
    if cp.returncode != 0:
        raise RuntimeError(f"Cannot read the backup: {(cp.stderr or '').strip()[:300]}")
    return [line.strip() for line in cp.stdout.splitlines() if line.strip()]


def check_archive(server, archive: str) -> None:
    """Only this map's world and Config, nothing absolute, nothing climbing out."""
    allowed = (f"SavedArks/{server.map_name}", "Config")
    names = archive_members(archive)
    if not names:
        raise RuntimeError("The backup is empty.")
    for name in names:
        clean = name.rstrip("/")
        if clean.startswith("/") or ".." in clean.split("/"):
            raise RuntimeError(f"The backup holds an unsafe path: {name}")
        if not any(clean == a or clean.startswith(a + "/") for a in allowed):
            raise RuntimeError(f"The backup holds {name}, which is not {server.name}'s world.")
    if not any(n.rstrip("/").startswith(allowed[0]) for n in names):
        raise RuntimeError(f"The backup has no {allowed[0]} world in it.")


def restore_files(server, archive: str, log) -> str:
    """Move the current world aside and unpack ``archive`` (the map must be stopped)."""
    check_archive(server, archive)
    saved = Path(server.saved_dir)
    aside = saved / f".pre-restore-{time.strftime('%Y%m%d-%H%M%S')}"
    world = f"SavedArks/{server.map_name}"
    script = (
        f'set -e; cd "{saved}"; mkdir -p "{aside}/SavedArks"; '
        f'if [ -e "{world}" ]; then mv "{world}" "{aside}/SavedArks/"; fi; '
        f'if [ -e Config ]; then mv Config "{aside}/"; fi; '
        f'tar --zstd -xpf "{archive}" -C "{saved}"; '
        # Keep only the newest set-aside world.
        f'for d in $(ls -d .pre-restore-* 2>/dev/null | sort | head -n -1); do rm -rf "$d"; done'
    )
    log(f"Moving the current world aside to {aside.name} and unpacking {Path(archive).name}.")
    cp = subprocess.run([podman.binary(), "unshare", "sh", "-c", script], capture_output=True, text=True,
                        timeout=3600)
    if cp.returncode != 0:
        raise RuntimeError(f"Restore failed: {(cp.stderr or cp.stdout).strip()[:300]}. "
                           f"The previous world is in {aside}.")
    return str(aside)
