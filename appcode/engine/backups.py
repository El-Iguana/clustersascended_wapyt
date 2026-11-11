"""
World backups: a map's ``SavedArks/<MAP>`` (without the old ``.stripped`` /
``.noflags`` copies) and its ``Config``, as tar + zstd.

The files belong to the container's user (uid 7777 inside podman's user
namespace), so tar runs under ``podman unshare``: ownership survives a
restore. Archives go to ``<cluster root>/Backups/<server>/``.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from engine import podman, steam, store


def backup_dir(cluster, server) -> Path:
    return Path(cluster.root_dir) / "Backups" / server.id


def create(server, cluster, kind: str, log, note: str = "") -> dict:
    saved = Path(server.saved_dir)
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
           "--exclude=*.stripped*", "--exclude=*.noflags*", *members]
    started = time.time()
    cp = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if cp.returncode != 0:
        if tmp.exists():
            tmp.unlink()
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
    return {"id": backup_id, "path": str(target), "size": size}
