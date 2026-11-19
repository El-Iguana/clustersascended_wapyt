"""
Adding a map to a cluster. Only with every map on the host stopped (checked
when it is asked for and again when the job runs): copying a 14 GB install
or downloading one competes with a running map for disk and memory, and the
new map's first start must be the only start.

Layout follows the cluster's (``<root>/ServerFiles_<Folder>/arkserver`` and
``<root>/Instance_<Folder>/Saved``). Folders are made inside podman's user
namespace and owned by the container's user, before podman would create them
as root. The new map takes the cluster's settings and mods, comes last in
priority, and has autostart off.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

from engine import podman, state, steam, store
from engine.models import COMMITTED, Server  # noqa: F401
from services.ark_maps import display_name, folder_name

_MAP_NAME = re.compile(r"^[A-Za-z0-9_]{2,64}$")
_SESSION = re.compile(r"^[^\r\n\x00\"?]{1,64}$")
SPARE_BYTES = 5 * 1024 ** 3


class Invalid(ValueError):
    pass


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower()) or "map"


def busy_maps() -> list[str]:
    """Maps that are not stopped, or have a job under way (all clusters: one host)."""
    out = [s.name for s in store.servers() if state.phase(s.id) in COMMITTED]
    for j in store.jobs(limit=200, states=("queued", "running")):
        if j["lane"] in ("lifecycle", "stop") and j["kind"] != "add_map":
            server = store.server(j["server"])
            name = server.name if server else j["server"]
            if name not in out:
                out.append(name)
    return out


def must_stop_first() -> str:
    """
    Why a map or cluster can't be added right now ("" when it can): every map
    on this machine, in every cluster, must be stopped and nothing being added.
    Callers hold ``supervisor._enqueue_lock`` so no start sneaks in between.
    """
    busy = busy_maps()
    if busy:
        return f"Stop every map first (still running or busy: {', '.join(busy)})."
    if any(j["kind"] == "add_map" for j in store.jobs(limit=50, states=("queued", "running"))):
        return "A map is being added; wait for it to finish."
    return ""


def ports_in_use() -> set[int]:
    used = set()
    for flag in ("-Hlun", "-Hltn"):
        try:
            cp = subprocess.run(["ss", flag], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired):
            continue
        for line in cp.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 4 and ":" in parts[3]:
                port = parts[3].rsplit(":", 1)[1]
                if port.isdigit():
                    used.add(int(port))
    return used


def dir_size(path: Path) -> int:
    cp = subprocess.run(["du", "-sb", str(path)], capture_output=True, text=True, timeout=300)
    try:
        return int(cp.stdout.split()[0])
    except (IndexError, ValueError):
        return 0


def suggest(cluster_id: str) -> dict:
    """Defaults for the wizard: next session name, free ports, install sources."""
    c = store.cluster(cluster_id)
    if c is None:
        raise Invalid("Unknown cluster.")
    servers = store.servers()                      # ports are host-wide
    mine = store.servers(c.id)
    taken_ports = {p for s in servers for p in (s.game_port, s.rcon_port)} | ports_in_use()
    game = max([s.game_port for s in servers] or [7767]) + 10
    while game in taken_ports or game + 1 in taken_ports:
        game += 10
    rcon_port = max([s.rcon_port for s in servers] or [27019]) + 1
    while rcon_port in taken_ports:
        rcon_port += 1
    sessions = [s.session_name for s in mine]
    m = [re.match(r"^(.*?)(\d+)$", x) for x in sessions]
    if sessions and all(m) and len({x.group(1) for x in m}) == 1:
        session = f"{m[0].group(1)}{max(int(x.group(2)) for x in m) + 1}"
    elif not sessions:                             # a new cluster's first map
        session = re.sub(r'[\r\n\x00"?]', "", c.name)[:63] + "1"
    else:
        session = ""
    sources = []
    for s in servers:
        install = Path(s.install_dir)
        if (install / "ShooterGame" / "Binaries").is_dir():
            sources.append({"id": s.id, "name": s.name, "build": steam.installed_build(s.install_dir),
                            "path": s.install_dir})
    usage = shutil.disk_usage(c.root_dir)
    return {"cluster": {"id": c.id, "name": c.name, "root_dir": c.root_dir},
            "session": session, "game_port": game, "rcon_port": rcon_port,
            "priority": max([s.priority for s in mine] or [0]) + 1,
            "taken_maps": sorted({s.map_name for s in mine}), "sources": sources,
            "disk_free": usage.free, "busy": busy_maps(), "why": must_stop_first(),
            "latest_build": steam.cached_latest()[0]}


def plan(cluster_id: str, values: dict) -> dict:
    """Check the wizard's choices; returns what the job needs."""
    c = store.cluster(cluster_id)
    if c is None:
        raise Invalid("Unknown cluster.")
    map_name = str(values.get("map_name") or "").strip()
    if not _MAP_NAME.match(map_name):
        raise Invalid("Pick a map (or type a MAP_NAME like TheIsland_WP).")
    if any(s.map_name == map_name for s in store.servers(c.id)):
        raise Invalid(f"{display_name(map_name)} is already in this cluster.")
    name = str(values.get("name") or display_name(map_name)).strip()[:40] or display_name(map_name)
    session = str(values.get("session") or "").strip()
    if not _SESSION.match(session):
        raise Invalid("Give it a name for the server list (up to 64 characters, no quotes).")
    try:
        game_port, rcon_port = int(values.get("game_port")), int(values.get("rcon_port"))
    except (TypeError, ValueError):
        raise Invalid("Ports are numbers.")
    for p in (game_port, rcon_port):
        if not 1024 <= p <= 65535:
            raise Invalid("Ports are between 1024 and 65535.")
    if game_port == rcon_port:
        raise Invalid("The game and RCON ports must differ.")
    taken = {p: s.name for s in store.servers() for p in (s.game_port, s.rcon_port)}
    for p in (game_port, rcon_port):
        if p in taken:
            raise Invalid(f"Port {p} belongs to {taken[p]}.")
    if {game_port, rcon_port} & ports_in_use():
        raise Invalid("A port is in use by another program on this machine.")
    folder = folder_name(map_name)
    root = Path(c.root_dir)
    install = root / f"ServerFiles_{folder}" / "arkserver"
    saved = root / f"Instance_{folder}" / "Saved"
    for d in (install.parent, saved.parent):
        if d.exists() and any(d.iterdir()):
            raise Invalid(f"{d} already exists and is not empty.")
    sid = slug(name)
    existing_ids = {s.id for s in store.servers()}
    base, n = sid, 2
    while sid in existing_ids:
        sid, n = f"{base}{n}", n + 1
    source = str(values.get("source") or "download")
    src = None
    if source != "download":
        src = store.server(source)
        if src is None:
            raise Invalid("Pick an install to copy, or download a fresh one.")
    need = (dir_size(Path(src.install_dir)) if src else 14 * 1024 ** 3) + SPARE_BYTES
    if shutil.disk_usage(c.root_dir).free < need:
        raise Invalid(f"Not enough disk space: it needs about {need / 1024 ** 3:.0f} GiB free.")
    return {"cluster": c.id, "server_id": sid, "name": name, "map_name": map_name, "session": session,
            "game_port": game_port, "rcon_port": rcon_port, "install": str(install), "saved": str(saved),
            "source": src.id if src else "download", "source_path": src.install_dir if src else "",
            "source_bytes": need - SPARE_BYTES,
            "priority": max([s.priority for s in store.servers(c.id)] or [0]) + 1,
            "start_after": bool(values.get("start_after"))}


def _mkdirs(paths: list[str], owner: str) -> None:
    script = " && ".join(f'mkdir -p "{p}" && chown {owner} "{p}"' for p in paths)
    cp = subprocess.run([podman.binary(), "unshare", "sh", "-c", script], capture_output=True, text=True, timeout=60)
    if cp.returncode != 0:
        raise RuntimeError(f"Could not make the folders: {(cp.stderr or cp.stdout).strip()[:300]}")


def _copy(src: str, dst: str, total: int, progress, flag) -> None:
    """Copy an install (keeping owners) and report progress; stops on abort."""
    proc = subprocess.Popen([podman.binary(), "unshare", "cp", "-a", f"{src}/.", f"{dst}/"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    done = threading.Event()

    def watch():
        while not done.wait(5):
            progress(dir_size(Path(dst)), total)

    t = threading.Thread(target=watch, daemon=True)
    t.start()
    try:
        while proc.poll() is None:
            if flag.is_set():
                proc.kill()
                raise RuntimeError("Cancelled while copying.")
            time.sleep(1)
        if proc.returncode != 0:
            raise RuntimeError(f"Copying the game files failed: {(proc.stderr.read() or '').strip()[:300]}")
    finally:
        done.set()
