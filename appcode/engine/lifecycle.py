"""
What a job does to a server. Only this module starts, stops, creates or
removes containers.

Start (the sequential gate):
    admission on a fresh snapshot → make sure the container matches the
    map's settings → podman start → wait for "advertising for join" stamped
    after this start → RCON answers → settle → done.
    Aborts on exit, "Hang detected on GameThread" or timeout, and stops the
    container so a hung map does not hold its memory.

Stop: RCON SaveWorld, then podman stop with a long grace period (ASA needs
minutes to flush a multi-GB save; podman's default 10 s would kill it
mid-write).

Containers: the app creates its own (``spec.py``). A container compose made
("foreign") still starts and stops, but is only replaced by "adopt", which
renames it ``<name>.compose-old`` so "rollback" can put it back.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from engine import admission, backups, game_settings, logtail, paths, podman, rcon, spec, state, steam, store
from engine.models import effective
from engine.supervisor import abort_flag, cancel_requested, set_stage

POLL_S = 5
OLD_SUFFIX = ".compose-old"


class Refused(RuntimeError):
    pass


class Aborted(RuntimeError):
    pass


def run(job: dict, log) -> str:
    kind, server_id, params = job["kind"], job["server"], job.get("params") or {}
    if kind == "start":
        _desire(server_id, "running")
        return start(server_id, log, job=job)
    if kind == "stop":
        if job.get("requested_by") not in ("watchdog",):
            _desire(server_id, "stopped")
        return stop(server_id, log, reason=params.get("reason") or "stop")
    if kind == "restart":
        _desire(server_id, "running")
        set_stage(job["id"], "stopping", "Saving and stopping before the restart")
        stop(server_id, log, reason="restart")
        return start(server_id, log, check="restart", job=job)
    if kind == "make_room_start":
        for victim in params.get("victims", []):
            other = store.server(victim)
            log(f"Making room: stopping {other.name if other else victim}.")
            set_stage(job["id"], "making_room", f"Saving and stopping {other.name if other else victim}")
            stop(victim, log, reason="make room")
        _desire(server_id, "running")
        return start(server_id, log, job=job)
    if kind == "adopt":
        return adopt(server_id, log)
    if kind == "rollback":
        return rollback(server_id, log)
    if kind == "update":
        return update(server_id, log, force=bool(params.get("force")), make_room=bool(params.get("make_room")),
                      job=job)
    if kind == "backup":
        server, cluster, _eff, _settings = _context(server_id)
        return backups.create(server, cluster, params.get("kind") or "manual", log,
                              note=params.get("note") or "")["path"]
    raise RuntimeError(f"Unknown job kind {kind}")


def _context(server_id: str):
    server = store.server(server_id)
    if server is None:
        raise RuntimeError("Unknown server.")
    cluster = store.cluster(server.cluster)
    return server, cluster, effective(server, cluster), store.host_settings()


def _desire(server_id: str, desired: str) -> None:
    s = store.server(server_id)
    if s is not None and (s.desired != desired or (desired == "running" and s.held)):
        s.desired = desired
        if desired == "running":
            s.held = ""          # an explicit start clears a crash-loop or watchdog hold
        store.save_server(s)


# -- containers ---------------------------------------------------------------

def desired_spec(server, cluster, eff, settings, update_pass: bool = False) -> spec.ContainerSpec:
    log_dir = paths.data_dir() / "logs"
    return spec.build(eff, settings, cluster=cluster.id, server=server.id, priority=server.priority,
                      log_dir=str(log_dir), update_pass=update_pass)


def is_managed(info: dict | None) -> bool:
    labels = ((info or {}).get("Config") or {}).get("Labels") or {}
    return labels.get(f"{spec.LABEL}.managed") == "1"


def ensure_container(server, cluster, eff, settings, log, update_pass: bool = False) -> None:
    """
    Make the map's container match its settings. Creates it if missing and
    recreates a stopped one whose settings changed (all state is in bind
    mounts). Leaves a compose-made container alone (see adopt).
    """
    want = desired_spec(server, cluster, eff, settings, update_pass)
    info = podman.inspect(server.container_name)
    if info is not None:
        if not is_managed(info):
            return
        have = ((info.get("Config") or {}).get("Labels") or {}).get(f"{spec.LABEL}.spec")
        if have == want.labels[f"{spec.LABEL}.spec"]:
            return
        if (info.get("State") or {}).get("Running"):
            raise RuntimeError(f"{server.name}'s settings changed while it runs; restart it to apply them.")
        log(f"Settings changed: recreating {server.container_name}.")
        podman.remove(server.container_name)
    for host_dir, _target in want.mounts:
        if not Path(host_dir).is_dir():
            # podman would create a missing bind source owned by root.
            raise RuntimeError(f"{host_dir} does not exist.")
    env_dir = paths.data_dir() / "env"
    env_dir.mkdir(parents=True, exist_ok=True)
    (paths.data_dir() / "logs").mkdir(parents=True, exist_ok=True)
    env_file = env_dir / f"{server.id}.env"
    fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(spec.env_file_text(want.env))
    log(f"Creating container {server.container_name}{' for an update pass' if update_pass else ''}.")
    podman.create(spec.create_argv(want, str(env_file)))
    state.request_refresh()


def spec_drift(server, cluster, settings, info: dict | None) -> bool:
    """A managed container whose settings no longer match the map's."""
    if not is_managed(info):
        return False
    want = desired_spec(server, cluster, effective(server, cluster), settings)
    have = ((info.get("Config") or {}).get("Labels") or {}).get(f"{spec.LABEL}.spec")
    return have != want.labels[f"{spec.LABEL}.spec"]


# -- start / stop -------------------------------------------------------------

def start(server_id: str, log, check: str = "start", timeout_s: int | None = None,
          update_pass: bool = False, job: dict | None = None) -> str:
    """
    One map's turn in the start queue, with retries: a crash, hang or timeout
    while loading stops it, waits ``retry_delay_s`` and tries again (memory is
    re-checked first) until ``start_attempts`` is used up.
    """
    settings = store.host_settings()
    job_id = (job or {}).get("id")
    flag = abort_flag(server_id)
    attempts = max(1, settings.start_attempts)
    for attempt in range(1, attempts + 1):
        try:
            return _start_once(server_id, log, check, timeout_s, update_pass, job_id, flag, attempt, attempts)
        except (Aborted, Refused):
            raise
        except Exception as exc:  # noqa: BLE001 - a failed attempt
            if attempt >= attempts:
                raise
            log(f"Attempt {attempt} of {attempts} failed: {exc} Trying again in {settings.retry_delay_s} s.")
            set_stage(job_id, "retrying", f"Attempt {attempt} of {attempts} failed: {exc}", attempt)
            _pause(flag, settings.retry_delay_s, server_id)
            check = "start"      # the failed attempt stopped the map
    raise RuntimeError("unreachable")


def _pause(flag, seconds: float, server_id: str) -> None:
    end = time.time() + seconds
    while time.time() < end:
        if flag.is_set():
            raise Aborted("The start was stopped.")
        time.sleep(min(POLL_S, max(0.0, end - time.time())))


def _wait_for_memory(server, check: str, settings, flag, log, job_id, attempt: int) -> None:
    """Re-check admission against what is really running until it fits, or give up."""
    deadline = time.time() + settings.memory_wait_s
    checks = 0
    while True:
        if flag.is_set():
            raise Aborted(f"{server.name}'s start was stopped.")
        decision = admission.admit(state.snapshot(), check, server.id)
        checks += 1
        if decision.ok:
            for line in decision.lines:
                log(line)
            return
        if checks == 1:
            for line in decision.lines:
                log(line)
            log(f"{decision.reason} Waiting for memory: checking every {settings.memory_recheck_s} s "
                f"for up to {settings.memory_wait_s // 60} minutes.")
        left = deadline - time.time()
        if left <= 0:
            raise Refused(f"{decision.reason} Waited {settings.memory_wait_s // 60} minutes for memory.")
        set_stage(job_id, "waiting_memory",
                  f"{decision.reason} Check {checks}; gives up in {max(1, int(left // 60))} min.", attempt)
        _pause(flag, min(settings.memory_recheck_s, left), server.id)


def _start_once(server_id: str, log, check: str, timeout_s, update_pass: bool, job_id, flag,
                attempt: int, attempts: int) -> str:
    server, cluster, eff, settings = _context(server_id)
    info = podman.inspect(server.container_name)
    running = bool(((info or {}).get("State") or {}).get("Running"))
    tag = f" (attempt {attempt} of {attempts})" if attempts > 1 and attempt > 1 else ""

    if not running:
        set_stage(job_id, "waiting_memory", "Checking memory…", attempt)
        _wait_for_memory(server, check, settings, flag, log, job_id, attempt)
        set_stage(job_id, "preparing", "Container and game settings", attempt)
        ensure_container(server, cluster, eff, settings, log, update_pass=update_pass)
        # Stopped now, so ASA's shutdown rewrite cannot undo this.
        game_settings.apply(server, cluster, log)
        if not is_managed(podman.inspect(server.container_name)):
            log(f"Note: {server.container_name} is still the compose container (adopt it to let the app own it).")

    state.set_override(server_id, "updating" if update_pass else "starting")
    started_here = False
    try:
        t0 = time.time()
        if running:
            log(f"{server.name} is already running; waiting for it to be ready.")
            t0 = state.live(server_id).started_at or t0
        else:
            log(f"Starting {server.name} ({server.container_name}){tag}.")
            # A stop reason left from a run the poller never saw must not
            # make this run's crash look like an app stop.
            state.pop_end_reason(server_id)
            state.forget_run(server_id)
            state.mark_app_start(server_id)
            podman.start(server.container_name)
            started_here = True
        state.request_refresh()
        set_stage(job_id, "loading", "Loading the world", attempt)
        peak = _wait_ready(server, settings, t0, flag, log, timeout_s or settings.ready_timeout_s, job_id, attempt)
        set_stage(job_id, "verifying", "Waiting for RCON to answer", attempt)
        _wait_rcon(server, eff, settings, flag, log)
        peak = max(peak, _settle(server, settings, flag, log, job_id, attempt))
        set_stage(job_id, "measuring", "Measuring its running memory", attempt)
        _wait_seen_ready(server, flag)
        settled = _measure(server)
        peak = max(peak, settled)
        run_id = state.live(server_id).run_id
        if run_id is not None:
            store.update_run(run_id, peak_start=peak or None, settled=settled or None)
    except Aborted:
        if cancel_requested(job_id) and (started_here or running):
            log(f"Cancelled: stopping {server.name} again.")
            state.set_override(server_id, None)
            try:
                stop(server_id, log, reason="cancelled", save=bool(state.live(server_id).ready_at))
            except Exception as exc:  # noqa: BLE001
                log(f"Stopping after the cancel failed: {exc}")
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
    gib = 1024 ** 3
    if settled:
        log(f"{server.name} is up: it took up to {peak / gib:.1f} GiB to start and settled at {settled / gib:.1f} GiB.")
        return f"{server.name} is up (start peak {peak / gib:.1f} GiB, running {settled / gib:.1f} GiB)."
    return f"{server.name} is up."


def _measure(server, samples: int = 3) -> int:
    """Its running memory once fully started: the highest of a few readings."""
    best = 0
    for i in range(samples):
        lv = state.live(server.id)
        if lv.mem:
            best = max(best, lv.mem["total"])
        if i < samples - 1:
            state.request_refresh()
            time.sleep(POLL_S / 5)
    return best


def _wait_ready(server, settings, t0: float, flag, log, timeout_s: int, job_id=None, attempt: int = 1) -> int:
    """
    Its own checks, not the poller's: podman start returns once the container
    runs, so not running later means it exited; and the log is followed here,
    counting only lines stamped after this start. Returns the highest memory
    seen while loading.
    """
    deadline = t0 + timeout_s
    last_note = 0.0
    peak = 0
    since = datetime.fromtimestamp(t0 - 1, tz=timezone.utc)
    follower = logtail.Follower(logtail.log_path(server.saved_dir))
    while True:
        if flag.is_set():
            raise Aborted(f"{server.name}'s start was stopped.")
        for event in logtail.scan(follower.read_new(), since):
            if event.kind == "ready":
                mem = f" ({event.mem_gb:.2f} GB reported)" if event.mem_gb else ""
                log(f"{server.name} is advertising for join{mem}.")
                return peak
            if event.kind == "hang":
                raise RuntimeError(f"{server.name}: Hang detected on GameThread (usually memory pressure).")
        info = podman.inspect(server.container_name)
        st = (info or {}).get("State") or {}
        if not st.get("Running"):
            raise RuntimeError(f"{server.name} exited while loading (state {st.get('Status', 'gone')}, "
                               f"exit code {st.get('ExitCode')}).")
        lv = state.live(server.id)
        if lv.mem:
            peak = max(peak, lv.mem["total"])
        if time.time() > deadline:
            raise RuntimeError(f"{server.name} was not ready after {timeout_s // 60} minutes.")
        so_far = f"{lv.mem['total'] / 1024 ** 3:.1f} GiB so far" if lv.mem else "starting up"
        set_stage(job_id, "loading", f"Loading the world: {so_far}, {int(time.time() - t0)} s", attempt)
        if time.time() - last_note >= 60:
            log(f"Loading, {so_far}…")
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


def _settle(server, settings, flag, log, job_id=None, attempt: int = 1) -> int:
    """Wait before anything else starts; memory keeps climbing after ready. Returns its peak."""
    peak = 0
    if settings.settle_s <= 0:
        return peak
    log(f"Letting {server.name} settle for {settings.settle_s} s before anything else starts.")
    end = time.time() + settings.settle_s
    while time.time() < end:
        if flag.is_set():
            raise Aborted(f"{server.name}'s start was stopped.")
        lv = state.live(server.id)
        if lv.mem:
            peak = max(peak, lv.mem["total"])
        set_stage(job_id, "settling", f"Settling: {int(end - time.time())} s left", attempt)
        time.sleep(min(POLL_S, max(0.0, end - time.time())))
    return peak


def _wait_seen_ready(server, flag, timeout_s: float = 60) -> None:
    """
    The job ends only once the poller also sees the map ready: admission,
    crash detection and run records all read the poller's view, and the next
    start must count this map as committed.
    """
    end = time.time() + timeout_s
    while time.time() < end:
        if flag.is_set():
            raise Aborted(f"{server.name}'s start was stopped.")
        lv = state.live(server.id)
        if lv.container_state == "running" and lv.ready_at and lv.run_id is not None:
            return
        state.request_refresh()
        time.sleep(POLL_S / 5)
    raise RuntimeError(f"{server.name} is up, but the app could not confirm it (podman slow?).")


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


# -- adopt / rollback ---------------------------------------------------------

def adopt(server_id: str, log) -> str:
    """Replace the compose container with one the app owns (the old one is kept)."""
    server, cluster, eff, settings = _context(server_id)
    info = podman.inspect(server.container_name)
    if info is not None and is_managed(info):
        return f"{server.name} is already managed by the app."
    old_name = server.container_name + OLD_SUFFIX
    if podman.inspect(old_name) is not None:
        raise RuntimeError(f"{old_name} already exists; roll back or remove it first.")
    was_running = bool(((info or {}).get("State") or {}).get("Running"))
    if was_running:
        stop(server_id, log, reason="adopt")
    if info is not None:
        log(f"Keeping the compose container as {old_name} (rollback puts it back).")
        podman.rename(server.container_name, old_name)
    ensure_container(server, cluster, eff, settings, log)
    s = store.server(server_id)
    s.autostart = True
    store.save_server(s)
    log("Autostart and crash recovery are on for this map (change it on the map's page).")
    if was_running:
        return start(server_id, log)
    return f"{server.name} now runs in a container the app owns."


def rollback(server_id: str, log) -> str:
    """Put the compose container back in place of the app's."""
    server, *_ = _context(server_id)
    old_name = server.container_name + OLD_SUFFIX
    if podman.inspect(old_name) is None:
        raise RuntimeError(f"There is no {old_name} to roll back to.")
    info = podman.inspect(server.container_name)
    if info is not None:
        if not is_managed(info):
            raise RuntimeError(f"{server.container_name} is not the app's container; not removing it.")
        if (info.get("State") or {}).get("Running"):
            stop(server_id, log, reason="rollback")
        podman.remove(server.container_name)
    podman.rename(old_name, server.container_name)
    s = store.server(server_id)
    s.autostart = False
    store.save_server(s)
    state.request_refresh()
    return f"{server.name} is back on its compose container (stopped)."


# -- updates ------------------------------------------------------------------

def update(server_id: str, log, force: bool = False, make_room: bool = False, job: dict | None = None) -> str:
    """
    Bring a map to the latest ASA build: backup → boot once with
    UPDATE_SERVER=TRUE (SteamCMD) → check the build → boot normally if it was
    running. Memory admission applies to the update boot like any start.
    """
    server, cluster, eff, settings = _context(server_id)
    latest, error = steam.latest_build(force=True)
    installed = steam.installed_build(server.install_dir)
    log(f"Installed build {installed or '?'}; latest {latest or '?'}{' (' + error + ')' if error else ''}.")
    if latest and installed == latest and not force:
        return f"{server.name} is already on build {installed}."
    info = podman.inspect(server.container_name)
    if info is not None and not is_managed(info):
        raise RuntimeError(f"Adopt {server.name} first: updates need a container the app owns.")
    was_running = bool(((info or {}).get("State") or {}).get("Running"))
    victims: list[str] = []
    if was_running:
        stop(server_id, log, reason="update")
    else:
        decision = admission.admit(state.snapshot(), "start", server_id)
        if not decision.ok and make_room and decision.victims:
            # Otherwise the update boot waits for memory like any start.
            victims = decision.victims
            for v in victims:
                log(f"Making room for the update: stopping {store.server(v).name}.")
                stop(v, log, reason="make room for update")
    try:
        set_stage((job or {}).get("id"), "backing_up", "Backing up the world before the update")
        backups.create(server, cluster, "pre-update", log)
        start(server_id, log, check="update", timeout_s=settings.update_timeout_s, update_pass=True, job=job)
        after = steam.installed_build(server.install_dir)
        log(f"Build after the update pass: {after or '?'}.")
        stop(server_id, log, reason="update pass done")
        if latest and after != latest:
            raise RuntimeError(f"Expected build {latest}, found {after}.")
        if was_running:
            start(server_id, log, job=job)
    finally:
        for v in victims:
            other = store.server(v)
            log(f"Bringing {other.name} back.")
            try:
                start(v, log)
            except Exception as exc:  # noqa: BLE001 - report, keep restoring the others
                log(f"Could not bring {other.name} back: {exc}")
    return f"{server.name} updated to build {latest or after}."
