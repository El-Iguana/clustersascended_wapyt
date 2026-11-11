"""
The poller thread: every 5 s it reads host memory and each running
container's cgroup; every 15 s (or when asked) it lists containers once.
It works out each server's phase, follows ShooterGame.log for readiness,
records runs (start, ready, peak memory, end) and polls player lists.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

from engine import logtail, meminfo, podman, rcon, state, store
from engine.models import effective

TICK_S = 5
LIST_EVERY = 3            # ticks between container listings (15 s)
PLAYERS_EVERY_S = 30

_followers: dict[str, logtail.Follower] = {}
_containers: dict = {}
_stop = threading.Event()
_thread: threading.Thread | None = None


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="ca-poller", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def _loop() -> None:
    n = 0
    while not _stop.is_set():
        try:
            tick(list_containers=(n % LIST_EVERY == 0) or state.refresh_requested())
        except Exception as exc:  # noqa: BLE001 - the poller must keep going
            print(f"[clusters-ascended] poller: {exc!r}", flush=True)
        n += 1
        backoff = 60 if podman.degraded else TICK_S
        _stop.wait(backoff)


def tick(list_containers: bool = True, now: float | None = None) -> None:
    global _containers
    now = now or time.time()
    state.set_host(meminfo.read_host())
    if list_containers:
        try:
            _containers = podman.containers()
        except podman.PodmanError as exc:
            print(f"[clusters-ascended] podman ps: {exc}", flush=True)
    clusters = {c.id: c for c in store.clusters()}
    for server in store.servers():
        _update(server, clusters.get(server.cluster), now)


def _update(server, cluster, now: float) -> None:
    c = _containers.get(server.container_name)
    lv = state.mutate(server.id)
    if c is None:
        lv.container_state, lv.container_id = "absent", ""
    else:
        lv.container_state, lv.container_id = c["state"], c["id"]
        lv.exit_code = c.get("exit_code")
        if c["started_at"] and c["started_at"] != lv.started_at:
            # A new start: forget the previous run's readiness.
            lv.started_at = c["started_at"]
            lv.ready_at, lv.ready_mem, lv.hang = None, None, False
            lv.cgroup, lv.peak, lv.players = "", 0, None
            _followers.pop(server.id, None)

    running = lv.container_state == "running"
    if running:
        _follow_log(server, lv)
        if not lv.cgroup:
            info = podman.inspect(server.container_name) if not podman.degraded else None
            lv.cgroup = ((info or {}).get("State") or {}).get("CgroupPath", "") or ""
        lv.mem = meminfo.read_container(lv.cgroup)
        if lv.mem:
            lv.peak = max(lv.peak, lv.mem["total"])
        if lv.ready_at and cluster and now - lv.players_at >= _players_every():
            _poll_players(server, cluster, lv, now)
    else:
        lv.mem = None

    if lv.container_state == "absent":
        lv.phase = "absent"
    elif lv.container_state in ("stopping",):
        lv.phase = "stopping"
    elif running:
        lv.phase = "ready" if lv.ready_at else "starting"
    elif lv.container_state in ("exited", "created", "stopped", "configured"):
        lv.phase = "stopped"
    else:
        lv.phase = "unknown"
    _record_run(server, lv, running, now)


def _players_every() -> int:
    return PLAYERS_EVERY_S


def _follow_log(server, lv) -> None:
    follower = _followers.get(server.id)
    if follower is None:
        follower = _followers[server.id] = logtail.Follower(logtail.log_path(server.saved_dir))
    text = follower.read_new()
    if not text or not lv.started_at:
        return
    since = datetime.fromtimestamp(lv.started_at - 5, tz=timezone.utc)
    for event in logtail.scan(text, since):
        if event.kind == "ready" and not lv.ready_at:
            lv.ready_at = event.at.timestamp()
            lv.ready_mem = event.mem_gb
        elif event.kind == "hang":
            lv.hang = True


def _poll_players(server, cluster, lv, now: float) -> None:
    lv.players_at = now
    eff = effective(server, cluster)
    try:
        reply = rcon.command(server.rcon_port, eff.get("admin_password") or "", "ListPlayers", timeout=4)
        lv.players = rcon.parse_players(reply)
    except rcon.RconError:
        lv.players = None


def _record_run(server, lv, running: bool, now: float) -> None:
    if running:
        if lv.run_id is None:
            row = store.open_run_for(server.id)
            if row is not None and abs(row["started_at"] - lv.started_at) < 5:
                lv.run_id = row["id"]
            else:
                if row is not None:
                    store.update_run(row["id"], stopped_at=now, end_reason="unknown")
                source = "app" if state.pop_app_start(server.id) else "external"
                lv.run_id = store.open_run(server.id, lv.started_at or now, source)
        if lv.ready_at and not lv.extra.get("ready_recorded") == lv.run_id:
            mem = int(lv.ready_mem * 1024 ** 3) if lv.ready_mem else None
            store.update_run(lv.run_id, ready_at=lv.ready_at, ready_mem=mem)
            lv.extra["ready_recorded"] = lv.run_id
        if lv.peak and lv.peak > lv.extra.get("peak_recorded", 0) + 64 * 1024 ** 2:
            store.bump_peak(lv.run_id, lv.peak)
            lv.extra["peak_recorded"] = lv.peak
    elif lv.run_id is not None or store.open_run_for(server.id) is not None:
        run_id = lv.run_id or store.open_run_for(server.id)["id"]
        reason = state.pop_end_reason(server.id)
        if not reason:
            reason = "exited" if lv.exit_code in (0, None) else f"exited ({lv.exit_code})"
        if lv.peak:
            store.bump_peak(run_id, lv.peak)
        store.update_run(run_id, stopped_at=now, end_reason=reason)
        lv.run_id = None
        lv.extra.pop("peak_recorded", None)
