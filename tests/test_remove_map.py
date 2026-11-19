"""Removing a map: only stopped, only the app's container, files kept unless asked, shared folders never."""
from __future__ import annotations

from pathlib import Path

from test_orchestrator import cluster_env, log_of, podman_state, wait_job  # noqa: F401 - fixture


def adopt(sid):
    from engine import supervisor

    j = wait_job(supervisor.enqueue("adopt", sid)["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])


def remove(sid, **params):
    from engine import supervisor

    r = supervisor.enqueue("remove_map", sid, params=params)
    assert r["ok"], r
    return wait_job(r["job"]["id"])


def test_remove_keeps_files_by_default(cluster_env, tmp_path):
    from engine import state, store

    adopt("gen")
    saved = Path(store.server("gen").saved_dir)
    (saved / "world.ark").write_text("the world")
    store.open_run("gen", 0)
    j = remove("gen")
    assert j["state"] == "done", log_of(j["id"])
    assert store.server("gen") is None and store.recent_runs("gen", 0) == []
    containers = podman_state(tmp_path)["containers"]
    assert "asa-gen" not in containers and "asa-gen.compose-old" in containers
    assert (saved / "world.ark").read_text() == "the world"
    assert "gen" not in state._live and "gen" not in state.snapshot().servers
    assert [s.id for s in store.servers("main")] == ["lc", "ast"]


def test_remove_with_files_spares_shared_folders(cluster_env, tmp_path):
    from engine import backups, store

    adopt("ast")
    s = store.server("ast")
    s.install_dir = store.server("lc").install_dir           # shared install (like Lost Colony's was)
    store.save_server(s)
    (Path(s.install_dir) / "game.exe").write_text("x")
    saved = Path(s.saved_dir)
    (saved / "world.ark").write_text("the world")
    bdir = backups.backup_dir(store.cluster("main"), s)
    bdir.mkdir(parents=True)
    (bdir / "a.tar.zst").write_text("x")
    j = remove("ast", delete_files=True)
    assert j["state"] == "done", log_of(j["id"])
    assert not saved.exists() and not saved.parent.exists()   # Instance_ast went too (it was empty)
    assert not bdir.exists()
    assert (Path(s.install_dir) / "game.exe").exists()
    assert "also used by LC" in log_of(j["id"])
    assert (tmp_path / "Cluster").is_dir()


def test_remove_is_refused_while_running_or_on_a_compose_container(cluster_env):
    from engine import store, supervisor

    assert wait_job(supervisor.enqueue("start", "ast")["job"]["id"])["state"] == "done"
    r = supervisor.enqueue("remove_map", "ast")
    assert not r["ok"] and "Stop AST" in r["error"]
    j = remove("lc")                                          # never adopted: the compose container
    assert j["state"] == "refused" and "compose container" in j["summary"]
    assert store.server("lc") is not None


def test_retiring_the_old_compose_container_leaves_the_running_map_alone(cluster_env, tmp_path):
    from engine import state, supervisor

    adopt("gen")
    assert wait_job(supervisor.enqueue("start", "gen")["job"]["id"])["state"] == "done"
    j = wait_job(supervisor.enqueue("retire_old", "gen")["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    containers = podman_state(tmp_path)["containers"]
    assert "asa-gen.compose-old" not in containers and containers["asa-gen"]["state"] == "running"
    assert state.phase("gen") == "ready"
    assert "no old compose container" in wait_job(supervisor.enqueue("retire_old", "gen")["job"]["id"])["summary"]
    assert "no old compose container" in wait_job(supervisor.enqueue("retire_old", "lc")["job"]["id"])["summary"]


def test_the_last_map_out_lets_the_cluster_be_deleted(cluster_env, monkeypatch):
    from engine import addmap, clusters, store

    monkeypatch.setattr(addmap, "_mkdirs", lambda paths, _o: [Path(p).mkdir(parents=True) for p in paths])
    for sid in ("lc", "ast", "gen"):
        adopt(sid)
        assert remove(sid)["state"] == "done"
    assert store.servers("main") == []
    assert clusters.delete("main")["name"] == "Main"
