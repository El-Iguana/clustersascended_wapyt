"""
What a job does to a server. Only this module starts or stops containers.

Start (the sequential gate):
    admission on a fresh snapshot → podman start → wait for "advertising for
    join" stamped after this start → RCON answers → settle → done.
    Aborts on exit, "Hang detected on GameThread" or timeout, and stops the
    container so a hung map does not hold its memory.

Stop: RCON saveworld, then podman stop with a long grace period (ASA needs
minutes to flush a multi-GB save; podman's default 10 s would kill it
mid-write).
"""
from __future__ import annotations

import time

from engine import admission, podman, rcon, state, store
from engine.models import effective
from engine.supervisor import abort_flag

POLL_S = 5


class Refused(RuntimeError):
    pass


class Aborted(RuntimeError):
    pass


def run(job: dict, log) -> str:
    kind, server_id = job["kind"], job["server"]
    if kind == "start":
        return start(server_id, log)
    if kind == "stop":
        return stop(server_id, log, reason="stop")
    if kind == "restart":
        stop(server_id, log, reason="restart")
        return start(server_id, log, check="restart")
    if kind == "make_room_start":
        for victim in job["params"].get("victims", []):
            other = store.server(victim)
            log(f"Making room: stopping {other.name if other else victim}.")
            stop(victim, log, reason="make room")
        return start(server_id, log)
    raise RuntimeError(f"Unknown job kind {kind}")


def _context(server_id: str):
    server = store.server(server_id)
    if server is None:
        raise RuntimeError("Unknown server.")
    cluster = store.cluster(server.cluster)
    return server, cluster, effective(server, cluster), store.host_settings()


def start(server_id: str, log, check: str = "start") -> str:
    server, cluster, eff, settings = _context(server_id)
    info = podman.inspect(server.container_name)
    if info is None:
        raise RuntimeError(f"Container {server.container_name} does not exist yet.")
    running = bool((info.get("State") or {}).get("Running"))

    if not running:
        decision = admission.admit(state.snapshot(), check, server_id)
        for line in decision.lines:
            log(line)
        if not decision.ok:
            raise Refused(decision.reason)

    flag = abort_flag(server_id)
    state.set_override(server_id, "starting")
    try:
        t0 = time.time()
        if running:
            log(f"{server.name} is already running; waiting for it to be ready.")
            t0 = state.live(server_id).started_at or t0
        else:
            log(f"Starting {server.name} ({server.container_name}).")
            state.mark_app_start(server_id)
            podman.start(server.container_name)
        state.request_refresh()
        _wait_ready(server, settings, t0, flag, log)
        _wait_rcon(server, eff, settings, flag, log)
        _settle(server, settings, flag, log)
    except Aborted:
        raise
    except Exception:
        log(f"Start failed; stopping {server.name} so it does not hold memory.")
        state.set_override(server_id, None)
        try:
            stop(server_id, log, reason="failed start", save=False)
        except Exception as exc:  # noqa: BLE001
            log(f"Stopping after the failure also failed: {exc}")
        raise
    finally:
        state.set_override(server_id, None)
    lv = state.live(server_id)
    mem = f" using {lv.mem['total'] / 1024 ** 3:.1f} GiB" if lv.mem else ""
    return f"{server.name} is up{mem}."


def _wait_ready(server, settings, t0: float, flag, log) -> None:
    deadline = t0 + settings.ready_timeout_s
    last_note = 0.0
    while True:
        if flag.is_set():
            raise Aborted(f"{server.name}'s start was stopped.")
        lv = state.live(server.id)
        fresh = lv.started_at >= t0 - 2
        if fresh and lv.ready_at:
            mem = f" ({lv.ready_mem:.2f} GB reported)" if lv.ready_mem else ""
            log(f"{server.name} is advertising for join{mem}.")
            return
        if fresh and lv.container_state not in ("running", ""):
            raise RuntimeError(f"{server.name} exited while loading (state {lv.container_state}, "
                               f"exit code {lv.exit_code}).")
        if fresh and lv.hang:
            raise RuntimeError(f"{server.name}: Hang detected on GameThread (usually memory pressure).")
        if time.time() > deadline:
            raise RuntimeError(f"{server.name} was not ready after {settings.ready_timeout_s // 60} minutes.")
        if time.time() - last_note >= 60:
            mem = f", {lv.mem['total'] / 1024 ** 3:.1f} GiB so far" if lv.mem else ""
            log(f"Loading{mem}…")
            last_note = time.time()
        state.request_refresh()
        time.sleep(POLL_S)


def _wait_rcon(server, eff, settings, flag, log) -> None:
    deadline = time.time() + settings.rcon_timeout_s
    while True:
        if flag.is_set():
            raise Aborted(f"{server.name}'s start was stopped.")
        try:
            rcon.command(server.rcon_port, eff.get("admin_password") or "", "ListPlayers", timeout=5)
            log("RCON answers.")
            return
        except rcon.RconError as exc:
            if time.time() > deadline:
                raise RuntimeError(f"RCON did not answer within {settings.rcon_timeout_s} s: {exc}")
        time.sleep(POLL_S)


def _settle(server, settings, flag, log) -> None:
    if settings.settle_s <= 0:
        return
    log(f"Letting {server.name} settle for {settings.settle_s} s before anything else starts.")
    end = time.time() + settings.settle_s
    while time.time() < end:
        if flag.is_set():
            raise Aborted(f"{server.name}'s start was stopped.")
        time.sleep(min(POLL_S, max(0.0, end - time.time())))


def stop(server_id: str, log, reason: str = "stop", save: bool = True) -> str:
    server, _cluster, eff, settings = _context(server_id)
    info = podman.inspect(server.container_name)
    if info is None or not (info.get("State") or {}).get("Running"):
        log(f"{server.name} is already stopped.")
        state.request_refresh()
        return f"{server.name} was already stopped."
    state.set_override(server_id, "stopping")
    state.set_end_reason(server_id, reason)
    try:
        if save:
            try:
                reply = rcon.command(server.rcon_port, eff.get("admin_password") or "", "SaveWorld",
                                     timeout=settings.save_timeout_s)
                log(f"SaveWorld: {reply.strip() or 'ok'}")
            except rcon.RconError as exc:
                log(f"SaveWorld failed ({exc}); stopping anyway.")
        log(f"Stopping {server.name} (up to {settings.stop_timeout_s} s for ASA to exit).")
        stderr = podman.stop(server.container_name, settings.stop_timeout_s)
        if "SIGKILL" in stderr:
            log(f"Warning: {server.name} did not exit in {settings.stop_timeout_s} s and was killed.")
    finally:
        state.request_refresh()
        # Hold "stopping" until the poller has seen the container stop.
        for _ in range(12):
            if state.live(server_id).container_state != "running":
                break
            time.sleep(POLL_S / 2)
        state.set_override(server_id, None)
    return f"{server.name} stopped."
