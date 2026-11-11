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
    monkeypatch.setattr(poller, "TICK_S", 0.05)
    monkeypatch.setattr(poller, "LIST_EVERY", 1)
    # Fresh live state per test.
    state._live.clear()
    state._overrides.clear()
    poller._followers.clear()

    rcons = {sid: FakeRcon() for sid in ("lc", "ast", "gen")}
    containers = {}
    store.save_host_settings(models.HostSettings(settle_s=0, ready_timeout_s=5, rcon_timeout_s=3,
                                                 stop_timeout_s=5))
    store.save_cluster(models.Cluster("main", "Main", "maincluster", str(tmp_path / "Cluster"),
                                      str(tmp_path), defaults={"admin_password": "adminsecret"}))
    for prio, sid in enumerate(("lc", "ast", "gen"), start=1):
        saved = tmp_path / f"Instance_{sid}" / "Saved"
        (saved / "Logs").mkdir(parents=True)
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
    supervisor.stop_workers()
    poller.stop()
    for t in list(supervisor._threads.values()) + [poller._thread]:
        if t:
            t.join(timeout=5)
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


def test_starts_are_sequential_and_a_third_is_refused(cluster_env):
    from engine import supervisor

    a = supervisor.enqueue("start", "lc")
    b = supervisor.enqueue("start", "ast")
    assert a["ok"] and b["ok"]
    c = supervisor.enqueue("start", "gen")
    assert not c["ok"]
    assert "Not enough room to start GEN" in c["error"]
    assert c["job"]["state"] == "refused"

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
    assert wait_job(supervisor.enqueue("start", "lc")["job"]["id"])["state"] == "done"
    time.sleep(0.3)
    peaks = store.recent_peaks("lc")
    assert peaks and peaks[0] == int(17.0 * GiB)
    s = store.server("lc")
    assert state.need_of(s, store.host_settings()) == int(17.0 * GiB * 1.10)
