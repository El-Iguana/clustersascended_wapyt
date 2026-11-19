"""The add-a-map wizard: suggestions, the all-stopped rule, the copy, clean-up on failure."""
from __future__ import annotations

from pathlib import Path

import pytest

from test_orchestrator import cluster_env, log_of, wait_job  # noqa: F401 - fixture


def fake_install(tmp_path, sid="lc"):
    install = tmp_path / sid
    (install / "ShooterGame" / "Binaries" / "Win64").mkdir(parents=True, exist_ok=True)
    (install / "ShooterGame" / "Binaries" / "Win64" / "ArkAscendedServer.exe").write_text("x" * 1000)
    (install / "steamapps").mkdir(exist_ok=True)
    (install / "steamapps" / "appmanifest_2430930.acf").write_text('"buildid"\t\t"26564816"\n')
    return install


def values(**over):
    v = {"map_name": "TheIsland_WP", "name": "The Island", "session": "Isle4", "game_port": "7811",
         "rcon_port": "27811", "source": "lc", "start_after": False}
    v.update(over)
    return v


def test_suggestions(cluster_env, tmp_path):
    from engine import addmap, store

    fake_install(tmp_path)
    for i, sid in enumerate(("lc", "ast", "gen"), start=1):
        s = store.server(sid)
        s.session_name = f"OldManGan{i}"
        store.save_server(s)
    d = addmap.suggest("main")
    assert d["session"] == "OldManGan4"
    assert d["game_port"] > max(s.game_port for s in store.servers())
    assert d["priority"] == 4 and d["busy"] == []
    assert [s["id"] for s in d["sources"]] == ["lc"] and d["sources"][0]["build"] == "26564816"


def test_plan_refusals(cluster_env, tmp_path):
    from engine import addmap

    fake_install(tmp_path)
    with pytest.raises(addmap.Invalid, match="already in this cluster"):
        addmap.plan("main", values(map_name="lc_WP"))
    with pytest.raises(addmap.Invalid, match="belongs to LC"):
        addmap.plan("main", values(game_port="7701"))
    (tmp_path / "ServerFiles_TheIsland").mkdir()
    (tmp_path / "ServerFiles_TheIsland" / "leftover").write_text("x")
    with pytest.raises(addmap.Invalid, match="not empty"):
        addmap.plan("main", values())


def test_refused_while_a_map_runs(cluster_env, tmp_path):
    from engine import supervisor

    fake_install(tmp_path)
    assert wait_job(supervisor.enqueue("start", "ast")["job"]["id"])["state"] == "done"
    r = supervisor.enqueue_add_map("main", values())
    assert not r["ok"] and "Stop every map first" in r["error"] and "AST" in r["error"]


def test_add_by_copy_then_start(cluster_env, tmp_path, monkeypatch):
    from engine import addmap, state, store, supervisor

    # The fake RCON for the new map listens on its port; the real check would refuse it.
    monkeypatch.setattr(addmap, "ports_in_use", lambda: set())

    from fakes.rcon_server import FakeRcon

    fake_install(tmp_path)
    rcon = FakeRcon()
    r = supervisor.enqueue_add_map("main", values(start_after=True, rcon_port=str(rcon.port)))
    assert r["ok"], r
    j = wait_job(r["job"]["id"], timeout=60)
    rcon.close()
    assert j["state"] == "done", log_of(j["id"])
    s = store.server("theisland")
    assert (s.map_name, s.session_name, s.game_port, s.priority, s.autostart) == \
        ("TheIsland_WP", "Isle4", 7811, 4, False)
    assert Path(s.install_dir, "ShooterGame/Binaries/Win64/ArkAscendedServer.exe").read_text() == "x" * 1000
    assert Path(s.saved_dir).is_dir()
    assert s.container_name == "asa-theisland"
    assert state.phase("theisland") == "ready"
    assert "Game files copied" in log_of(j["id"])


def test_a_failed_copy_leaves_nothing_behind(cluster_env, tmp_path):
    import shutil

    from engine import store, supervisor

    install = fake_install(tmp_path)
    r = supervisor.enqueue_add_map("main", values())
    assert r["ok"], r
    shutil.rmtree(install)                  # the source disappears before the copy
    j = wait_job(r["job"]["id"], timeout=60)
    assert j["state"] == "failed"
    assert store.server("theisland") is None
    assert not (tmp_path / "ServerFiles_TheIsland").exists()
    assert not (tmp_path / "Instance_TheIsland").exists()


def test_download_makes_shootergame_first_and_a_failed_boot_says_why(cluster_env, tmp_path, monkeypatch):
    """
    Live-found 2026-11-19: with an empty install, podman made ShooterGame (the parent of the Saved
    mount) as root, so POK's first-boot download couldn't write there and the container exited 1.
    """
    from engine import addmap, lifecycle, paths, store, supervisor

    made: list[str] = []
    real = addmap._mkdirs
    monkeypatch.setattr(addmap, "_mkdirs", lambda ps, owner: (made.extend(ps), real(ps, owner)))
    log = paths.data_dir() / "logs" / "theisland.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("2026-11-19T09:49:38.36-05:00 stdout F [ERROR] Staged installation failed\n"
                   "2026-11-19T09:49:38.70-05:00 stdout F ❌ Server install/update helper exited with status 1\n"
                   "2026-11-19T09:49:38.71-05:00 stdout F    Aborting startup\n")

    def boot_fails(server_id, log_fn, **kw):
        assert kw.get("update_pass")
        assert Path(store.server(server_id).install_dir, "ShooterGame").is_dir()   # before the first boot
        raise RuntimeError("The Island exited while loading (state exited, exit code 1). Its last errors: "
                           + " | ".join(lifecycle.container_errors(server_id)))
    monkeypatch.setattr(lifecycle, "start", boot_fails)

    r = supervisor.enqueue_add_map("main", values(source="download"))
    assert r["ok"], r
    j = wait_job(r["job"]["id"], timeout=60)
    assert j["state"] == "failed"
    install = str(tmp_path / "ServerFiles_TheIsland" / "arkserver")
    assert made[:2] == [install, install + "/ShooterGame"]
    assert "Staged installation failed | ❌ Server install/update helper exited with status 1" in j["summary"]
    assert store.server("theisland") is None


def test_container_errors_picks_the_last_distinct_error_lines(cluster_env):
    from engine import lifecycle, paths

    assert lifecycle.container_errors("nobody") == []
    log = paths.data_dir() / "logs" / "lc.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("".join(f"t stdout F {m}\n" for m in
                           ["[ERROR] a", "fine", "[ERROR] b", "[ERROR] b", "[ERROR] c", "❌ d", "done"]))
    assert lifecycle.container_errors("lc") == ["[ERROR] b", "[ERROR] c", "❌ d"]
