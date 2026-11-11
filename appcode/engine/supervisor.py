"""
The job queue. Everything that changes a server goes through ``enqueue``.

Two lanes, one worker each:

* ``lifecycle``: start, restart, make-room-and-start. One at a time for the
  whole host, and a start only finishes once the map is ready and settled,
  so this lane *is* the sequential-start gate.
* ``stop``: graceful stops. They only free memory, so they never wait behind
  a 20-minute start; a stop of a map that is still starting aborts that start.

Starts form a queue, one entry per map. A start is not refused for memory
here (only one that could never fit, even with every other map stopped):
it waits its turn, then re-checks memory against what is really running
(waiting, with a limit, if it does not fit yet), starts fully, settles and
is measured, and only then does the next start begin. Each job records its
stage, so the page can show where every start is, and any start can be
cancelled: queued ones are dropped, a running one is stopped.
"""
from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime

from engine import admission, state, store

LANES = ("lifecycle", "stop", "io")
KIND_LANE = {"start": "lifecycle", "restart": "lifecycle", "make_room_start": "lifecycle",
             "adopt": "lifecycle", "rollback": "lifecycle", "update": "lifecycle",
             "restore": "lifecycle", "warned_restart": "lifecycle", "add_map": "lifecycle",
             "stop": "stop", "backup": "io"}
ACTIVE = ("queued", "running")

_enqueue_lock = threading.Lock()
_aborts_lock = threading.Lock()
_wake = {lane: threading.Event() for lane in LANES}
_aborts: dict[str, threading.Event] = {}
_cancels: set[int] = set()
_threads: dict[str, threading.Thread] = {}
_stop = threading.Event()


class JobLog:
    def __init__(self, job_id: int) -> None:
        self.job_id = job_id
        self.path = store.job_log_path(job_id)
        self.last = ""

    def __call__(self, message: str) -> None:
        self.last = message
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now():%H:%M:%S}] {message}\n")


def abort_flag(server_id: str) -> threading.Event:
    with _aborts_lock:
        return _aborts.setdefault(server_id, threading.Event())


def set_stage(job_id: int | None, stage: str, detail: str = "", attempt: int | None = None) -> None:
    """Where a job is now (shown on the page)."""
    if not job_id:
        return
    fields = {"stage": stage, "detail": detail[:300]}
    if attempt is not None:
        fields["attempt"] = attempt
    store.set_job(job_id, **fields)


def cancel_requested(job_id: int | None) -> bool:
    with _aborts_lock:
        return bool(job_id) and job_id in _cancels


def queue() -> list[dict]:
    """The lifecycle lane in order: the running job first, then the queued ones."""
    jobs = [j for j in store.jobs(limit=200, states=ACTIVE) if j["lane"] == "lifecycle"]
    return sorted(jobs, key=lambda j: (j["state"] != "running", j["id"]))


def pending() -> list[tuple[str, str]]:
    """Queued and running jobs as (kind, server) for admission's projection."""
    out = []
    for j in store.jobs(limit=200, states=ACTIVE):
        if j["kind"] == "make_room_start":
            out += [("stop", v) for v in j["params"].get("victims", [])]
            out.append(("start", j["server"]))
        elif j["kind"] in admission.START_KINDS or j["kind"] == "stop":
            out.append((j["kind"], j["server"]))
    return out[::-1]


def enqueue(kind: str, server_id: str, requested_by: str = "admin", params: dict | None = None) -> dict:
    """Queue a job, or refuse it. Returns ``{"ok", "job", "decision"?, "error"?}``."""
    if kind not in KIND_LANE:
        return {"ok": False, "error": f"Unknown action: {kind}"}
    server = store.server(server_id)
    if server is None:
        return {"ok": False, "error": "Unknown server."}
    params = dict(params or {})
    with _enqueue_lock:
        busy = [j for j in store.jobs(limit=200, states=ACTIVE)
                if j["server"] == server_id and j["lane"] != "io"]
        if kind == "backup":
            busy = [j for j in store.jobs(limit=200, states=ACTIVE)
                    if j["server"] == server_id and j["kind"] == "backup"]
        active_kinds = {j["kind"] for j in store.jobs(limit=200, states=ACTIVE) if j["server"] == server_id}
        if kind == "restore" and "backup" in active_kinds:
            return {"ok": False, "error": f"{server.name} is being backed up; restore when it is done."}
        if kind == "backup" and "restore" in active_kinds:
            return {"ok": False, "error": f"{server.name} is being restored; back up when it is done."}
        if kind != "stop" and busy:
            return {"ok": False, "error": f"{server.name} already has a {busy[0]['kind']} job "
                                          f"{busy[0]['state']}."}
        if kind == "stop" and any(j["kind"] == "stop" for j in busy):
            return {"ok": False, "error": f"{server.name} is already being stopped."}
        decision = None
        if kind in admission.START_KINDS:
            snap = state.snapshot()
            check = "start" if kind == "make_room_start" else kind
            if kind == "restart" and snap.servers[server_id].phase not in ("ready", "starting"):
                check = kind = "start"     # restarting a stopped map is a start
            refusal = admission.never_fits(snap, server_id)
            decision = admission.admit(snap, check, server_id, pending())
            if kind == "make_room_start":
                if decision.ok:
                    kind = "start"
                elif decision.victims:
                    params["victims"] = decision.victims
                else:
                    refusal = refusal or (decision.reason + " Stopping other maps would not make enough room.")
            if refusal:
                job_id = store.add_job(KIND_LANE[kind], kind, server_id, params, requested_by,
                                       state="refused", summary=refusal)
                store.set_job(job_id, finished=time.time(), stage="refused")
                JobLog(job_id)("Refused. " + refusal + " " + " ".join(decision.lines))
                store.audit(requested_by, f"refused {kind}", server_id, decision.to_dict())
                return {"ok": False, "error": refusal, "decision": decision.to_dict(),
                        "job": store.job(job_id)}
        if kind == "stop":
            # A stop wins over a start still loading the same map.
            abort_flag(server_id).set()
        job_id = store.add_job(KIND_LANE[kind], kind, server_id, params, requested_by)
        store.set_job(job_id, stage="queued")
        store.audit(requested_by, kind, server_id, params)
    _wake[KIND_LANE[kind]].set()
    position = next((i for i, j in enumerate(queue(), start=1) if j["id"] == job_id), None)
    return {"ok": True, "job": store.job(job_id), "position": position,
            "waits": bool(decision and not decision.ok),
            "decision": decision.to_dict() if decision else None}


CANCELLABLE = frozenset({"start", "restart", "make_room_start", "update", "warned_restart", "add_map"})


def enqueue_add_map(cluster_id: str, values: dict, requested_by: str = "admin") -> dict:
    """The add-a-map wizard's job: only with every map stopped and nothing else under way."""
    from engine import addmap

    with _enqueue_lock:
        busy = addmap.busy_maps()
        if busy:
            return {"ok": False, "error": f"Stop every map first (still running or busy: {', '.join(busy)})."}
        if any(j["kind"] == "add_map" for j in store.jobs(limit=50, states=ACTIVE)):
            return {"ok": False, "error": "A map is already being added."}
        try:
            plan = addmap.plan(cluster_id, values)
        except addmap.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        job_id = store.add_job("lifecycle", "add_map", plan["server_id"], plan, requested_by)
        store.set_job(job_id, stage="queued")
        store.audit(requested_by, "add map", cluster_id, plan)
    _wake["lifecycle"].set()
    return {"ok": True, "job": store.job(job_id), "plan": plan}


def cancel(job_id: int) -> dict:
    """Drop a queued job, or stop a start that is running (the map is stopped again)."""
    with _enqueue_lock:
        j = store.job(job_id)
        if j is None or j["state"] not in ACTIVE:
            return {"ok": False, "error": "That job is not queued or running."}
        if j["state"] == "queued":
            store.set_job(job_id, state="cancelled", finished=time.time(), stage="cancelled",
                          summary="Cancelled before it started.")
            return {"ok": True, "was": "queued"}
        if j["kind"] not in CANCELLABLE:
            return {"ok": False, "error": f"A running {j['kind']} cannot be cancelled."}
        with _aborts_lock:
            _cancels.add(job_id)
        abort_flag(j["server"]).set()
        store.set_job(job_id, detail="Cancelling…")
    return {"ok": True, "was": "running"}


def cancel_queue() -> int:
    """Drop every queued start (the running one carries on)."""
    n = 0
    for j in queue():
        if j["state"] == "queued" and cancel(j["id"]).get("ok"):
            n += 1
    return n


def start_workers() -> None:
    _stop.clear()
    for lane in LANES:
        t = _threads.get(lane)
        if t and t.is_alive():
            continue
        _threads[lane] = threading.Thread(target=_worker, args=(lane,), name=f"ca-{lane}", daemon=True)
        _threads[lane].start()


def stop_workers(timeout: float = 30) -> bool:
    """Stop the lanes; a start still waiting is aborted. True when all exited."""
    _stop.set()
    with _aborts_lock:
        for flag in _aborts.values():
            flag.set()
    for e in _wake.values():
        e.set()
    deadline = time.time() + timeout
    for t in list(_threads.values()):
        t.join(max(0.0, deadline - time.time()))
    return not any(t.is_alive() for t in _threads.values())


def recover() -> None:
    """At boot: jobs that were running when the app stopped did not finish."""
    for j in store.jobs(limit=500, states=("running",)):
        store.set_job(j["id"], state="interrupted", finished=time.time(),
                      summary="The app restarted while this job ran.")


def _worker(lane: str) -> None:
    while not _stop.is_set():
        j = store.next_queued(lane)
        if j is None:
            _wake[lane].wait(2)
            _wake[lane].clear()
            continue
        run_job(j)


def run_job(j: dict) -> dict:
    from engine import lifecycle

    log = JobLog(j["id"])
    store.set_job(j["id"], state="running", started=time.time())
    if j["kind"] != "stop":
        abort_flag(j["server"]).clear()
    final, summary = "done", ""
    set_stage(j["id"], "running")
    try:
        summary = lifecycle.run(j, log) or log.last
    except lifecycle.Refused as exc:
        final, summary = "refused", str(exc)
        log(f"Refused: {exc}")
    except lifecycle.Aborted as exc:
        final, summary = "cancelled", str(exc)
        log(f"Stopped: {exc}")
    except Exception as exc:  # noqa: BLE001 - a job failing must not kill the lane
        final, summary = "failed", str(exc) or exc.__class__.__name__
        log(f"Failed: {summary}")
        log(traceback.format_exc())
    with _aborts_lock:
        _cancels.discard(j["id"])
    store.set_job(j["id"], state=final, finished=time.time(), summary=summary[:500], stage=final, detail="")
    return store.job(j["id"])
