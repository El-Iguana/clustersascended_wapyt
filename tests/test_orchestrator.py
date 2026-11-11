"""
The supervisor, lifecycle and poller together, against a fake podman and
fake RCON servers: the sequential gate, refusals, make room, hangs, aborts.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from fakes.rcon_server import FakeRcon

FAKES = Path(__file__).parent / "fakes"
GiB = 1024 ** 3


@pytest.fixture
def cluster_env(data_dir, fake_proc, tmp_path, monkeypatch):
    from engine import lifecycle, models, poller, state, store, supervisor

    state_dir = tmp_path / "podman"
    state_dir.mkdir()
    os.chmod(FAKES / "podman", 0o755)
    monkeypatch.setenv("CA_PODMAN", str(FAKES / "podman"))
    monkeypatch.setenv("FAKE_PODMAN_STATE", str(state_dir))
    monkeypatch.setattr(lifecycle, "POLL_S", 0.05)
    from engine import backups
    monkeypatch.setattr(backups, "QUIET_S", 0)
    monkeypatch.setattr(backups, "SAVE_LAND_S", 1)
    monkeypatch.setattr(poller, "TICK_S", 0.05)
    monkeypatch.setattr(poller, "LIST_EVERY", 1)
    # Fresh live state per test.
    state._live.clear()
    state._overrides.clear()
    state._end_reasons.clear()
    state._app_starts.clear()
    supervisor._aborts.clear()
    from engine import watchdog
    watchdog.reset()
    poller._followers.clear()

    rcons = {sid: FakeRcon() for sid in ("lc", "ast", "gen")}
    containers = {}
    store.save_host_settings(models.HostSettings(settle_s=0, ready_timeout_s=5, rcon_timeout_s=3,
                                                 stop_timeout_s=5, swap_allowance=16 * GiB,
                                                 memory_wait_s=1, memory_recheck_s=0.2,
                                                 start_attempts=2, retry_delay_s=0))
    store.save_cluster(models.Cluster("main", "Main", "maincluster", str(tmp_path / "Cluster"),
                                      str(tmp_path), defaults={"admin_password": "adminsecret"}))
    (tmp_path / "Cluster").mkdir()
    for prio, sid in enumerate(("lc", "ast", "gen"), start=1):
        saved = tmp_path / f"Instance_{sid}" / "Saved"
        (saved / "Logs").mkdir(parents=True)
        (tmp_path / sid).mkdir()
        containers[f"asa-{sid}"] = {"state": "exited", "started_at": 0, "saved_dir": str(saved),
                                    "on_start": "ready"}
        store.save_server(models.Server(sid, "main", sid.upper(), f"{sid}_WP", sid, f"asa-{sid}",
                                        7700 + prio, rcons[sid].port, str(tmp_path / sid),
                                        str(saved), priority=prio))
    (state_dir / "state.json").write_text(json.dumps({"containers": containers}))

    def configure(name, **fields):
        data = json.loads((state_dir / "state.json").read_text())
        data["containers"][name].update(fields)
        (state_dir / "state.json").write_text(json.dumps(data))

    def calls():
        p = state_dir / "calls.log"
        return [line.split() for line in p.read_text().splitlines()] if p.exists() else []

    poller.tick()
    poller.start()
    supervisor.start_workers()
    yield {"configure": configure, "calls": calls, "rcons": rcons}
    assert supervisor.stop_workers(timeout=30), "a lane did not stop"
    poller.stop()
    if poller._thread:
        poller._thread.join(timeout=10)
    for r in rcons.values():
        r.close()


def wait_job(job_id, timeout=15):
    from engine import store

    end = time.time() + timeout
    while time.time() < end:
        j = store.job(job_id)
        if j["state"] not in ("queued", "running"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} still {store.job(job_id)['state']}")


def log_of(job_id):
    from engine import store

    return store.job_log_path(job_id).read_text()


def test_starts_queue_one_per_map_and_a_third_waits_then_gives_up(cluster_env):
    from engine import supervisor

    a = supervisor.enqueue("start", "lc")
    b = supervisor.enqueue("start", "ast")
    c = supervisor.enqueue("start", "gen")
    assert a["ok"] and b["ok"] and c["ok"]          # all queued: memory is checked at each turn
    assert [a["position"], b["position"], c["position"]] == [1, 2, 3]
    jc = wait_job(c["job"]["id"], timeout=30)
    assert jc["state"] == "refused"
    assert "Not enough memory to start GEN" in jc["summary"] and "Waited" in jc["summary"]
    assert "Waiting for memory" in log_of(jc["id"])

    ja, jb = wait_job(a["job"]["id"]), wait_job(b["job"]["id"])
    assert ja["state"] == "done", log_of(ja["id"])
    assert jb["state"] == "done", log_of(jb["id"])
    # AST only started once LC was ready: it started after LC's job finished.
    assert jb["started"] >= ja["finished"]
    starts = [c[1] for c in cluster_env["calls"]() if c[0] == "start"]
    assert starts == ["asa-lc", "asa-ast"]
    assert "advertising for join" in log_of(ja["id"])


def test_make_room_stops_the_least_important_map(cluster_env):
    from engine import supervisor

    for sid in ("lc", "ast"):
        assert wait_job(supervisor.enqueue("start", sid)["job"]["id"])["state"] == "done"
    r = supervisor.enqueue("make_room_start", "gen")
    assert r["ok"], r
    assert r["job"]["params"]["victims"] == ["ast"]
    j = wait_job(r["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    assert "SaveWorld" in cluster_env["rcons"]["ast"].commands
    ops = [(c[0], c[-1]) for c in cluster_env["calls"]() if c[0] in ("start", "stop")]
    assert ops[-2:] == [("stop", "asa-ast"), ("start", "asa-gen")]


def test_a_hung_start_fails_and_frees_its_memory(cluster_env):
    from engine import supervisor

    cluster_env["configure"]("asa-lc", on_start="hang")
    j = wait_job(supervisor.enqueue("start", "lc")["job"]["id"])
    assert j["state"] == "failed"
    assert "Hang detected" in j["summary"]
    assert ("stop", "asa-lc") in [(c[0], c[-1]) for c in cluster_env["calls"]()]


def test_a_start_that_never_gets_ready_times_out(cluster_env):
    from engine import supervisor

    cluster_env["configure"]("asa-lc", on_start="silent")
    j = wait_job(supervisor.enqueue("start", "lc")["job"]["id"], timeout=20)
    assert j["state"] == "failed" and "not ready" in j["summary"]


def test_stop_aborts_a_start_still_loading(cluster_env):
    from engine import supervisor

    cluster_env["configure"]("asa-lc", on_start="silent")
    start = supervisor.enqueue("start", "lc")
    time.sleep(0.5)
    stop = supervisor.enqueue("stop", "lc")
    assert stop["ok"], stop
    assert wait_job(start["job"]["id"])["state"] == "cancelled"
    assert wait_job(stop["job"]["id"])["state"] == "done"


def test_duplicate_jobs_are_refused(cluster_env):
    from engine import supervisor

    cluster_env["configure"]("asa-lc", on_start="silent")
    supervisor.enqueue("start", "lc")
    again = supervisor.enqueue("start", "lc")
    assert not again["ok"] and "already has a start job" in again["error"]
    supervisor.enqueue("stop", "lc")


def test_runs_are_recorded_and_seed_the_need(cluster_env):
    from engine import state, store, supervisor

    cluster_env["configure"]("asa-lc", ready_gb=17.0)
    j = wait_job(supervisor.enqueue("start", "lc")["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    time.sleep(0.3)
    peaks = store.recent_peaks("lc")
    assert peaks and peaks[0] == int(17.0 * GiB)
    s = store.server("lc")
    assert state.need_of(s, store.host_settings()) == int(17.0 * GiB * 1.10)


# -- phase 2: ownership, recovery, updates -------------------------------------

def podman_state(tmp_path):
    return json.loads((tmp_path / "podman" / "state.json").read_text())


def test_adopt_replaces_the_compose_container_and_rollback_restores_it(cluster_env, tmp_path):
    from engine import store, supervisor

    j = wait_job(supervisor.enqueue("adopt", "gen")["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    containers = podman_state(tmp_path)["containers"]
    assert "asa-gen.compose-old" in containers
    new = containers["asa-gen"]
    assert new["labels"]["io.clustersascended.managed"] == "1"
    assert new["env"]["MAP_NAME"] == "gen_WP" and new["env"]["SERVER_ADMIN_PASSWORD"] == "adminsecret"
    assert "--restart" in new["argv"] and new["argv"][new["argv"].index("--restart") + 1] == "no"
    assert store.server("gen").autostart is True
    # The managed container starts like any other.
    assert wait_job(supervisor.enqueue("start", "gen")["job"]["id"])["state"] == "done"

    j = wait_job(supervisor.enqueue("rollback", "gen")["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    containers = podman_state(tmp_path)["containers"]
    assert "asa-gen.compose-old" not in containers
    assert containers["asa-gen"].get("labels", {}) == {}
    assert store.server("gen").autostart is False


def test_a_changed_setting_recreates_a_stopped_container(cluster_env, tmp_path):
    from engine import store, supervisor

    wait_job(supervisor.enqueue("adopt", "lc")["job"]["id"])
    before = podman_state(tmp_path)["containers"]["asa-lc"]["labels"]["io.clustersascended.spec"]
    c = store.cluster("main")
    c.defaults["max_players"] = 32
    store.save_cluster(c)
    j = wait_job(supervisor.enqueue("start", "lc")["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    after = podman_state(tmp_path)["containers"]["asa-lc"]
    assert after["labels"]["io.clustersascended.spec"] != before
    assert after["env"]["MAX_PLAYERS"] == "32"
    assert "Settings changed: recreating" in log_of(j["id"])


def test_a_crash_is_restarted_then_a_crash_loop_is_held(cluster_env, tmp_path):
    import subprocess

    from engine import store, supervisor

    wait_job(supervisor.enqueue("adopt", "lc")["job"]["id"])
    assert wait_job(supervisor.enqueue("start", "lc")["job"]["id"])["state"] == "done"
    fake = str(FAKES / "podman")

    def crash_and_wait_for_recovery():
        before = len([j for j in store.jobs(50) if j["requested_by"] == "recovery"])
        subprocess.run([fake, "crash", "asa-lc"], check=True)
        end = time.time() + 10
        while time.time() < end:
            recovery = [j for j in store.jobs(50) if j["requested_by"] == "recovery"]
            held = store.server("lc").held
            if held or (len(recovery) > before and recovery[0]["state"] not in ("queued", "running")):
                return recovery, held
            time.sleep(0.05)
        raise AssertionError("no recovery")

    recovery, held = crash_and_wait_for_recovery()
    assert recovery and recovery[0]["state"] == "done" and not held
    recovery, held = crash_and_wait_for_recovery()
    assert "Crash loop" in store.server("lc").held


def test_autostart_brings_back_what_should_run(cluster_env):
    from engine import boot, store

    for sid in ("lc", "ast", "gen"):
        s = store.server(sid)
        s.autostart, s.desired = True, "running"
        store.save_server(s)
    results = boot.autostart()
    assert [r["ok"] for r in results] == [True, True, True]     # one queue entry per map
    states = [wait_job(r["job"]["id"], timeout=30)["state"] for r in results]
    assert states == ["done", "done", "refused"]               # the third never got the memory


def test_update_backs_up_boots_with_update_server_and_restores(cluster_env, tmp_path, monkeypatch):
    from engine import backups, steam, supervisor

    made = []
    monkeypatch.setattr(backups, "create", lambda s, c, kind, log, note="": made.append(kind) or {})
    builds = iter(["100", "200"])    # installed before the update; after the pass
    monkeypatch.setattr(steam, "latest_build", lambda force=False: ("200", ""))
    monkeypatch.setattr(steam, "installed_build", lambda d: next(builds, "200"))
    wait_job(supervisor.enqueue("adopt", "lc")["job"]["id"])
    assert wait_job(supervisor.enqueue("start", "lc")["job"]["id"])["state"] == "done"

    seen = []
    real_create = None
    from engine import podman
    real_create = podman.create
    monkeypatch.setattr(podman, "create", lambda argv: seen.append(argv) or real_create(argv))
    j = wait_job(supervisor.enqueue("update", "lc")["job"]["id"], timeout=30)
    assert j["state"] == "done", log_of(j["id"])
    assert made == ["pre-update"]
    envs = [json.loads((tmp_path / "podman" / "state.json").read_text())]  # final state
    assert envs[0]["containers"]["asa-lc"]["env"]["UPDATE_SERVER"] == "FALSE"
    assert len(seen) == 2       # an update-pass container, then the normal one again
    assert "updated to build 200" in j["summary"]



# -- the start queue: stages, waiting, retries, cancelling -------------------------

def test_a_waiting_start_goes_once_memory_is_freed(cluster_env):
    from engine import store, supervisor

    for sid in ("lc", "ast"):
        assert wait_job(supervisor.enqueue("start", sid)["job"]["id"])["state"] == "done"
    s = store.host_settings()
    s.memory_wait_s = 20
    store.save_host_settings(s)
    gen = supervisor.enqueue("start", "gen")
    assert gen["ok"] and gen["waits"]
    end = time.time() + 10
    while store.job(gen["job"]["id"])["stage"] != "waiting_memory" and time.time() < end:
        time.sleep(0.05)
    assert "Not enough memory" in store.job(gen["job"]["id"])["detail"]
    assert wait_job(supervisor.enqueue("stop", "ast")["job"]["id"])["state"] == "done"
    j = wait_job(gen["job"]["id"], timeout=30)
    assert j["state"] == "done" and j["stage"] == "done", log_of(j["id"])


def test_a_failed_load_is_retried(cluster_env):
    from engine import supervisor

    cluster_env["configure"]("asa-lc", on_start_seq=["exit", "ready"])
    j = wait_job(supervisor.enqueue("start", "lc")["job"]["id"], timeout=30)
    assert j["state"] == "done", log_of(j["id"])
    assert "Attempt 1 of 2 failed" in log_of(j["id"])
    assert j["attempt"] == 2
    assert [c[1] for c in cluster_env["calls"]() if c[0] == "start"] == ["asa-lc", "asa-lc"]


def test_retries_are_limited(cluster_env):
    from engine import supervisor

    cluster_env["configure"]("asa-lc", on_start="exit")
    j = wait_job(supervisor.enqueue("start", "lc")["job"]["id"], timeout=30)
    assert j["state"] == "failed" and "exited while loading" in j["summary"]
    assert len([c for c in cluster_env["calls"]() if c[0] == "start"]) == 2


def test_cancelling_a_loading_start_stops_the_map(cluster_env):
    from engine import supervisor

    cluster_env["configure"]("asa-lc", on_start="silent")
    j = supervisor.enqueue("start", "lc")["job"]
    end = time.time() + 10
    from engine import store
    while store.job(j["id"])["stage"] != "loading" and time.time() < end:
        time.sleep(0.05)
    assert supervisor.cancel(j["id"]) == {"ok": True, "was": "running"}
    done = wait_job(j["id"])
    assert done["state"] == "cancelled"
    assert ("stop", "asa-lc") in [(c[0], c[-1]) for c in cluster_env["calls"]()]
    assert "Cancelled: stopping" in log_of(j["id"])


def test_cancelling_queued_starts(cluster_env):
    from engine import supervisor

    cluster_env["configure"]("asa-lc", on_start="silent")
    first = supervisor.enqueue("start", "lc")["job"]
    second = supervisor.enqueue("start", "ast")["job"]
    assert supervisor.cancel(second["id"]) == {"ok": True, "was": "queued"}
    assert supervisor.cancel_queue() == 0
    supervisor.cancel(first["id"])
    assert wait_job(first["id"])["state"] == "cancelled"
    from engine import store
    assert store.job(second["id"])["state"] == "cancelled"
