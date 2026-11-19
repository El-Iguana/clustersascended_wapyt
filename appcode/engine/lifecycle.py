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
FORCE_GRACE_S = 10        # SIGTERM → SIGKILL once the world is saved
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
    if kind == "update_cluster":
        return update_cluster(params["cluster"], log, job, warn=params.get("warn") or [],
                              message=params.get("message") or "", restart=params.get("restart", True))
    if kind == "retire_old":
        return retire_old(server_id, log)
    if kind == "remove_map":
        return remove_map(server_id, log, delete_files=bool(params.get("delete_files")))
    if kind == "backup":
        server, cluster, _eff, _settings = _context(server_id)
        if params.get("skip_unchanged") and backups.unchanged_since_last(server):
            log(f"{server.name}'s world has not changed since its last backup; nothing to do.")
            return f"{server.name}: unchanged since the last backup."
        return backups.create(server, cluster, params.get("kind") or "manual", log,
                              note=params.get("note") or "", keep=params.get("keep"))["path"]
    if kind == "add_map":
        return add_map(params, log, job)
    if kind == "restore":
        return restore(server_id, int(params["backup_id"]), log, job)
    if kind == "warned_restart":
        return warned_restart(server_id, log, job, params.get("warn") or [15, 5, 1],
                              params.get("message") or "")
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

def container_errors(server_id: str, keep: int = 3) -> list[str]:
    """
    The last distinct error lines of a map's container output (the k8s-file log
    in data/logs, which outlives the container), so a failed start says why.
    """
    path = paths.data_dir() / "logs" / f"{server_id}.log"
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            fh.seek(max(0, fh.tell() - 64 * 1024))
            lines = fh.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []
    found: list[str] = []
    for line in reversed(lines):
        msg = line.split(" ", 3)[3] if line.count(" ") >= 3 else line     # "<time> <stream> <F|P> <text>"
        msg = msg.strip()
        if ("ERROR" in msg or "❌" in msg) and msg not in found:
            found.append(msg[:200])
            if len(found) == keep:
                break
    return list(reversed(found))


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
        _apply_bans(server, log)
        if not update_pass:      # it is stopped right after; nothing waits on it
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
            errors = container_errors(server.id)
            raise RuntimeError(f"{server.name} exited while loading (state {st.get('Status', 'gone')}, "
                               f"exit code {st.get('ExitCode')})."
                               + (" Its last errors: " + " | ".join(errors) if errors else ""))
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


def _apply_bans(server, log) -> None:
    """The cluster's bans reach a map that was stopped when they were made."""
    from engine import players

    pending = [b for b in players.bans(server.cluster) if server.id not in b["applied"]]
    if not pending:
        return
    result = players.apply_bans(server.cluster, only_server=server.id)
    log(f"Applied {len(pending)} cluster ban(s)." if result["done"] else "Could not apply the cluster's bans yet.")


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
        saved = _save_for_stop(server, eff, settings, log) if save else False
        if saved or not save:
            # Nothing left to lose: let ASA quit by itself for a bit, then force it.
            # POK's own SIGTERM handler cannot verify -usestore saves and would
            # otherwise hold the container for the whole grace period.
            if saved:
                _quit_game(server, eff, settings, log)
            grace = FORCE_GRACE_S
            log(f"Stopping {server.name}.")
        else:
            grace = settings.stop_timeout_s
            log(f"Stopping {server.name} (the save was not confirmed: up to {grace} s for ASA to exit).")
        stderr = podman.stop(server.container_name, grace)
        if "SIGKILL" in stderr:
            if saved:
                log(f"{server.name} was forced down (its world had already been saved).")
            elif save:
                log(f"Warning: {server.name} did not exit in {grace} s and was killed.")
    finally:
        state.request_refresh()
        # Hold "stopping" until the poller has seen the container stop.
        for _ in range(12):
            if state.live(server_id).container_state != "running":
                break
            time.sleep(POLL_S / 2)
        state.set_override(server_id, None)
    return f"{server.name} stopped."


def _save_for_stop(server, eff, settings, log) -> bool:
    """SaveWorld, then wait for it to land on disk. True when ASA confirmed the save."""
    asked = time.time()
    try:
        reply = rcon.command(server.rcon_port, eff.get("admin_password") or "", "SaveWorld",
                             timeout=settings.save_timeout_s)
    except rcon.RconError as exc:
        log(f"SaveWorld failed ({exc}).")
        return False
    log(f"SaveWorld: {reply.strip() or 'ok'}")
    # ASA answers about a second before the file is written.
    end = time.time() + backups.SAVE_LAND_S
    while time.time() < end:
        if backups.newest_change(server) >= asked - 1:
            return True
        time.sleep(0.5)
    log("The new save did not show up on disk within a minute; ASA said it saved, so carrying on.")
    return True


def _quit_game(server, eff, settings, log) -> None:
    """
    Ask ASA to quit (DoExit) and wait up to ``exit_wait_s`` for its process to go.
    POK restarts a game that exits unless /home/pok/shutdown.flag exists, so set it first.
    """
    name = server.container_name
    try:
        podman.exec_(name, ["touch", "/home/pok/shutdown.flag"], timeout=20)
        rcon.command(server.rcon_port, eff.get("admin_password") or "", "DoExit", timeout=15)
    except (rcon.RconError, podman.PodmanTimeout) as exc:
        log(f"Could not ask {server.name} to quit ({exc}); forcing it down.")
        return
    log(f"Asked {server.name} to quit; waiting up to {settings.exit_wait_s} s.")
    end = time.time() + settings.exit_wait_s
    while time.time() < end:
        try:
            if podman.exec_(name, ["pgrep", "-f", "ArkAscendedServer.exe"], timeout=20) == 1:
                log(f"{server.name} has quit.")
                return
        except podman.PodmanTimeout:
            pass
        time.sleep(POLL_S)
    log(f"{server.name} did not quit within {settings.exit_wait_s} s; forcing it down.")


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


def retire_old(server_id: str, log) -> str:
    """Remove the kept compose container once the app's own has proved itself. No file is touched."""
    server, *_ = _context(server_id)
    old_name = server.container_name + OLD_SUFFIX
    old = podman.inspect(old_name)
    if old is None:
        return f"{server.name} has no old compose container."
    if not is_managed(podman.inspect(server.container_name)):
        raise Refused(f"{server.name} does not run in the app's own container yet: keep {old_name}.")
    if (old.get("State") or {}).get("Running"):
        raise Refused(f"{old_name} is running; it should never be. Stop it first.")
    podman.remove(old_name)
    log(f"Removed {old_name}. Its world and game files were never in the container: nothing else changed.")
    return f"{server.name}: the old compose container is gone (no rollback now)."


# -- restore and warned restarts ----------------------------------------------

def restore(server_id: str, backup_id: int, log, job: dict | None = None) -> str:
    """
    Put a backup back: save and stop the map, back up what it has now
    (pre-restore), move the current world aside, unpack the backup, and start
    the map again (through admission) if it was running.
    """
    server, cluster, _eff, _settings = _context(server_id)
    b = store.backup(backup_id)
    if b is None or b["server"] != server_id:
        raise RuntimeError("That backup does not belong to this map.")
    if not Path(b["path"]).is_file():
        raise RuntimeError(f"The backup file is missing: {b['path']}")
    job_id = (job or {}).get("id")
    backups.check_archive(server, b["path"])
    was_running = state.phase(server_id) in ("ready", "starting")
    if was_running:
        set_stage(job_id, "stopping", "Saving and stopping before the restore")
        stop(server_id, log, reason="restore")
    set_stage(job_id, "backing_up", "Backing up the current world first")
    backups.create(server, cluster, "pre-restore", log, note=f"before restoring backup {backup_id}")
    set_stage(job_id, "restoring", f"Unpacking {Path(b['path']).name}")
    aside = backups.restore_files(server, b["path"], log)
    log(f"Restored. The world it replaced is kept in {aside}.")
    if was_running:
        return start(server_id, log, job=job)
    return f"{server.name} restored from {time.strftime('%b %d %H:%M', time.localtime(b['created']))}."


def warned_restart(server_id: str, log, job: dict | None, warn: list, message: str = "") -> str:
    """Count down in game chat, save, restart (the start goes through admission)."""
    server, _cluster, eff, _settings = _context(server_id)
    job_id = (job or {}).get("id")
    if state.phase(server_id) != "ready":
        return f"{server.name} is not running; nothing to restart."
    flag = abort_flag(server_id)
    marks = sorted({int(m) for m in warn if int(m) > 0}, reverse=True)
    password = eff.get("admin_password") or ""
    for i, minutes in enumerate(marks):
        text = f"{message or 'Server restart'} in {minutes} minute{'s' if minutes != 1 else ''}."
        try:
            rcon.command(server.rcon_port, password, f"ServerChat {text}", timeout=10)
            log(f"Warned players: {text}")
        except rcon.RconError as exc:
            log(f"Could not warn players ({exc}).")
        following = marks[i + 1] if i + 1 < len(marks) else 0
        set_stage(job_id, "warning", f"Restart in {minutes} min", None)
        _pause(flag, (minutes - following) * 60 * WARN_UNIT, server_id)
    try:
        rcon.command(server.rcon_port, password, "ServerChat Restarting now. See you in a few minutes!", timeout=10)
    except rcon.RconError:
        pass
    _desire(server_id, "running")
    set_stage(job_id, "stopping", "Saving and stopping")
    stop(server_id, log, reason="scheduled restart")
    return start(server_id, log, check="restart", job=job)


WARN_UNIT = 1.0   # tests shorten the countdown


# -- adding a map --------------------------------------------------------------

def add_map(p: dict, log, job: dict | None = None) -> str:
    """The wizard's job (engine/addmap.py plans it). Every map must be stopped."""
    from engine import addmap
    from engine.models import Server

    job_id = (job or {}).get("id")
    busy = addmap.busy_maps()
    if busy:
        raise Refused(f"Stop every map first (still running or busy: {', '.join(busy)}).")
    settings = store.host_settings()
    owner = f"{settings.puid}:{settings.pgid}"
    install, saved = Path(p["install"]), Path(p["saved"])
    flag = abort_flag(p["server_id"])
    created = False
    try:
        set_stage(job_id, "preparing", "Making the map's folders")
        # ShooterGame too: the world is mounted at <install>/ShooterGame/Saved, and with an empty install
        # podman would make ShooterGame itself, owned by root, so the first-boot download can't write there.
        addmap._mkdirs([str(install), str(install / "ShooterGame"), str(saved)], owner)
        log(f"Folders: {install} and {saved}.")
        if p["source"] != "download":
            total = int(p.get("source_bytes") or 0)
            log(f"Copying the game files from {p['source_path']} ({total / 1024 ** 3:.1f} GiB).")

            def progress(done: int, total_bytes: int) -> None:
                gib = 1024 ** 3
                set_stage(job_id, "copying",
                          f"Copying game files: {done / gib:.1f} of {total_bytes / gib:.1f} GiB")

            addmap._copy(p["source_path"], str(install), total, progress, flag)
            log("Game files copied.")
        server = Server(p["server_id"], p["cluster"], p["name"], p["map_name"], p["session"],
                        f"asa-{p['server_id']}", p["game_port"], p["rcon_port"], str(install), str(saved),
                        priority=p["priority"], autostart=False, desired="stopped")
        store.save_server(server)
        created = True
        cluster = store.cluster(p["cluster"])
        eff = effective(server, cluster)
        if p["source"] == "download":
            log("Downloading ASA with SteamCMD on the first boot (about 13 GiB; this takes a while).")
            start(server.id, log, check="start", timeout_s=settings.update_timeout_s, update_pass=True, job=job)
            built = steam.installed_build(str(install))
            if not built:
                raise RuntimeError("The first boot did not leave an ASA install behind.")
            log(f"Installed build {built}.")
            stop(server.id, log, reason="first boot done")
        else:
            set_stage(job_id, "preparing", "Creating the map's container")
            ensure_container(server, cluster, eff, settings, log)
        if p.get("start_after"):
            _desire(server.id, "running")
            return start(server.id, log, job=job)
        return f"{server.name} was added to {cluster.name}. Start it from the dashboard."
    except BaseException:
        log("Adding the map failed; removing what was made.")
        if created:
            try:
                info = podman.inspect(f"asa-{p['server_id']}")
                if info and is_managed(info):
                    if (info.get("State") or {}).get("Running"):
                        stop(p["server_id"], log, reason="failed add", save=False)
                    podman.remove(f"asa-{p['server_id']}")
            except Exception as exc:  # noqa: BLE001
                log(f"Removing the container failed: {exc}")
            store.conn().execute("DELETE FROM servers WHERE id=?", (p["server_id"],))
            store.export_config()
        _remove_dirs([str(install.parent), str(saved.parent)], log)
        raise


# -- removing a map ------------------------------------------------------------

def _overlap(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def remove_map(server_id: str, log, delete_files: bool = False) -> str:
    """
    Take a stopped map out of the app and remove the app's container. Its
    world, game files and backups stay unless ``delete_files``; even then a
    folder another map or the cluster's transfer folder uses is never touched.
    """
    import shutil

    from engine.models import COMMITTED

    server = store.server(server_id)
    if server is None:
        return "That map was already removed."
    if state.phase(server_id) in COMMITTED:
        raise Refused(f"{server.name} is running. Stop it first.")
    cluster = store.cluster(server.cluster)
    info = podman.inspect(server.container_name)
    if info is not None:
        if not is_managed(info):
            raise Refused(f"{server.container_name} is the compose container, not the app's: adopt the map "
                          "first, or remove that container yourself.")
        if (info.get("State") or {}).get("Running"):
            raise Refused(f"{server.name} is running. Stop it first.")
        podman.remove(server.container_name)
        log(f"Removed the container {server.container_name}.")
    if podman.inspect(server.container_name + OLD_SUFFIX) is not None:
        log(f"Kept {server.container_name + OLD_SUFFIX} (the old compose container; not the app's).")

    targets, kept = [], []
    if delete_files:
        root = Path(cluster.root_dir)
        others = [s for s in store.servers() if s.id != server.id]
        for d in (Path(server.saved_dir), Path(server.install_dir), backups.backup_dir(cluster, server)):
            user = next((s.name for s in others if _overlap(d, Path(s.install_dir)) or _overlap(d, Path(s.saved_dir))),
                        None)
            if user:
                kept.append(f"{d} (also used by {user})")
            elif _overlap(d, Path(cluster.transfer_dir)) or d == root or d in root.parents:
                kept.append(f"{d} (a cluster folder)")
            else:
                targets.append(d)

    store.delete_server(server.id)
    state.forget(server.id)
    log(f"{server.name} is out of the app (its runs, player history and backup list too).")
    if targets:
        log(f"Deleting {', '.join(map(str, targets))}.")
        _remove_dirs([str(d) for d in targets], log)
        for d in targets:
            try:
                if d.parent != Path(cluster.root_dir):
                    d.parent.rmdir()         # e.g. Instance_<Map>, if nothing else is in it
            except OSError:
                pass
        shutil.rmtree(paths.data_dir() / "ini-backups" / server.id, ignore_errors=True)
    for k in kept:
        log(f"Kept {k}.")
    if delete_files:
        return f"{server.name} was removed and its files deleted." + (f" Kept {len(kept)} shared folder(s)." if kept else "")
    return f"{server.name} was removed. Its world and game files are still in {Path(server.saved_dir).parent}."


def _remove_dirs(paths: list[str], log) -> None:
    import subprocess

    script = " ; ".join(f'rm -rf "{d}"' for d in paths)
    cp = subprocess.run([podman.binary(), "unshare", "sh", "-c", script], capture_output=True, text=True, timeout=600)
    if cp.returncode != 0:
        log(f"Could not remove {', '.join(paths)}: {(cp.stderr or '').strip()[:200]}")


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
            for v in decision.victims:
                if state.live(v).container_state != "running":
                    continue           # already stopped (or stopping): nothing to bring back
                log(f"Making room for the update: stopping {store.server(v).name}.")
                stop(v, log, reason="make room for update")
                victims.append(v)
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
            abort_flag(v).clear()      # left set by the stop above
            try:
                start(v, log)
            except Exception as exc:  # noqa: BLE001 - report, keep restoring the others
                log(f"Could not bring {other.name} back: {exc}")
    return f"{server.name} updated to build {latest or after}."


def update_cluster(cluster_id: str, log, job: dict | None = None, warn: list | None = None,
                   message: str = "", restart: bool = True) -> str:
    """
    Update a whole cluster: count down in game chat if ``warn`` is given,
    save and stop every running map at once (forced if it is slow), update
    each map that is behind, one at a time, then (``restart``) start the maps
    that were running again, one at a time through admission.
    """
    import threading

    from engine.supervisor import abort_flag as _flag

    job_id = (job or {}).get("id")
    cluster = store.cluster(cluster_id)
    if cluster is None:
        raise RuntimeError("Unknown cluster.")
    servers = sorted(store.servers(cluster_id), key=lambda s: s.priority)
    latest, error = steam.latest_build(force=True)
    behind = [s for s in servers if not latest or steam.installed_build(s.install_dir) != latest]
    log(f"Latest build {latest or '?'}{' (' + error + ')' if error else ''}; "
        f"behind: {', '.join(s.name for s in behind) or 'none'}.")
    if not behind:
        return f"Every map in {cluster.name} is already on build {latest}."

    running = [s for s in servers
               if ((podman.inspect(s.container_name) or {}).get("State") or {}).get("Running")]
    if running and warn:
        _count_down(running, warn, message or "Server update", log, job_id)
    if running:
        set_stage(job_id, "stopping", f"Saving and stopping {', '.join(s.name for s in running)}")
        log(f"Saving and stopping {', '.join(s.name for s in running)}.")
        failures: dict[str, str] = {}

        def _one(s) -> None:
            try:
                _desire(s.id, "stopped")
                stop(s.id, lambda m, n=s.name: log(f"[{n}] {m}"), reason="cluster update")
            except Exception as exc:  # noqa: BLE001 - reported below
                failures[s.name] = str(exc)

        threads = [threading.Thread(target=_one, args=(s,), name=f"ca-stop-{s.id}") for s in running]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        if failures:
            raise RuntimeError("Could not stop " + "; ".join(f"{k}: {v}" for k, v in failures.items()))

    done, failed = [], []
    for i, s in enumerate(behind, start=1):
        if cancel_requested(job_id):
            raise Aborted(f"Cluster update cancelled before {s.name}.")
        _flag(s.id).clear()      # a flag left by an earlier stop must not abort this boot
        set_stage(job_id, "updating", f"Updating {s.name} ({i} of {len(behind)})")
        log(f"--- Updating {s.name} ({i} of {len(behind)}) ---")
        try:
            done.append(update(s.id, log, job=job))
        except Aborted:
            raise
        except Exception as exc:  # noqa: BLE001 - carry on with the next map
            log(f"Updating {s.name} failed: {exc}")
            failed.append(f"{s.name}: {exc}")
    back, not_back = [], []
    if restart and running:
        for i, s in enumerate(running, start=1):
            if cancel_requested(job_id):
                not_back.extend(r.name for r in running[i - 1:])
                log("Cancelled: the remaining maps stay stopped.")
                break
            _flag(s.id).clear()
            _desire(s.id, "running")
            set_stage(job_id, "starting", f"Starting {s.name} again ({i} of {len(running)})")
            log(f"--- Starting {s.name} again ({i} of {len(running)}) ---")
            try:
                start(s.id, log, job=job)
                back.append(s.name)
            except Exception as exc:  # noqa: BLE001 - carry on with the next map
                log(f"Starting {s.name} again failed: {exc}")
                not_back.append(s.name)
    summary = " ".join(done) + (" Failed: " + "; ".join(failed) if failed else "")
    if not running:
        summary += " No map was running, so none was started."
    elif not restart:
        summary += " Maps left stopped."
    else:
        summary += (f" Running again: {', '.join(back)}." if back else "") + \
                   (f" Not started again: {', '.join(not_back)}." if not_back else "")
    if failed and not done:
        raise RuntimeError(summary.strip())
    return summary.strip()


def _count_down(servers: list, warn: list, message: str, log, job_id) -> None:
    """Warn every running map's players at each mark (minutes), all maps at once."""
    marks = sorted({int(m) for m in warn if int(m) > 0}, reverse=True)

    def say(text: str) -> None:
        for s in servers:
            _s, _c, eff, _st = _context(s.id)
            try:
                rcon.command(s.rcon_port, eff.get("admin_password") or "", f"ServerChat {text}", timeout=10)
            except rcon.RconError as exc:
                log(f"Could not warn {s.name}'s players ({exc}).")

    for i, minutes in enumerate(marks):
        text = f"{message} in {minutes} minute{'s' if minutes != 1 else ''}."
        say(text)
        log(f"Warned players: {text}")
        set_stage(job_id, "warning", f"Update in {minutes} min", None)
        following = marks[i + 1] if i + 1 < len(marks) else 0
        end = time.time() + (minutes - following) * 60 * WARN_UNIT
        while time.time() < end:
            if cancel_requested(job_id):
                say("The update was called off.")
                raise Aborted("Cluster update cancelled during the countdown; nothing was stopped.")
            time.sleep(min(POLL_S, max(0.0, end - time.time())))
    say("Updating now. The server will be back in a while!")
