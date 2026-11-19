"""Backups, pruning, archive checks, restore, schedules and warned restarts."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import pytest

from test_orchestrator import cluster_env, log_of, wait_job  # noqa: F401 - fixture

GiB = 1024 ** 3


def world(tmp_path, sid="lc"):
    return tmp_path / f"Instance_{sid}" / "Saved" / "SavedArks" / f"{sid}_WP"


def seed_world(tmp_path, sid="lc", text="day one"):
    w = world(tmp_path, sid)
    w.mkdir(parents=True, exist_ok=True)
    (w / f"{sid}_WP.ark").write_text(text)
    cfg = tmp_path / f"Instance_{sid}" / "Saved" / "Config" / "WindowsServer"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "Game.ini").write_text("[/Script/ShooterGame.ShooterGameMode]\r\n")


def test_next_run_is_local_and_respects_days():
    from engine import scheduler

    monday_9am = datetime(2026, 11, 9, 9, 0).timestamp()          # a Monday
    assert datetime.fromtimestamp(scheduler.next_run("04:00", [0, 1, 2, 3, 4, 5, 6], monday_9am)) == \
        datetime(2026, 11, 10, 4, 0)
    assert datetime.fromtimestamp(scheduler.next_run("10:30", [0], monday_9am)) == datetime(2026, 11, 9, 10, 30)
    assert datetime.fromtimestamp(scheduler.next_run("08:00", [5], monday_9am)) == datetime(2026, 11, 14, 8, 0)
    with pytest.raises(ValueError):
        scheduler.parse_at("25:00")


def test_backup_prune_pin_and_skip_unchanged(cluster_env, tmp_path):
    from engine import store, supervisor

    seed_world(tmp_path)
    ids = []
    for i in range(3):
        (world(tmp_path) / "lc_WP.ark").write_text(f"save {i}")
        j = wait_job(supervisor.enqueue("backup", "lc", params={"kind": "scheduled", "keep": 2})["job"]["id"])
        assert j["state"] == "done", log_of(j["id"])
        ids.append(store.backups("lc", 1)[0]["id"])
        time.sleep(1.05)          # backup files are named by the second
    rows = store.backups("lc", 10)
    assert [r["id"] for r in rows] == ids[:0:-1]          # oldest pruned, newest two kept
    assert len(list((tmp_path / "Backups" / "lc").glob("*.tar.zst"))) == 2   # files removed too
    store.set_backup(ids[1], pinned=1)
    (world(tmp_path) / "lc_WP.ark").write_text("save 3")
    wait_job(supervisor.enqueue("backup", "lc", params={"kind": "scheduled", "keep": 1})["job"]["id"])
    kept = {r["id"] for r in store.backups("lc", 10)}
    assert ids[1] in kept and len(kept) == 2              # the pinned one survives keep=1
    # Nothing saved since: a scheduled backup skips it.
    j = wait_job(supervisor.enqueue("backup", "lc", params={"kind": "scheduled", "skip_unchanged": True})["job"]["id"])
    assert "unchanged" in j["summary"]


def test_archive_check_refuses_other_files(cluster_env, tmp_path):
    import subprocess

    from engine import backups, store

    seed_world(tmp_path)
    bad = tmp_path / "bad.tar.zst"
    (tmp_path / "evil").mkdir()
    (tmp_path / "evil" / "x").write_text("x")
    subprocess.run(["tar", "--zstd", "-cf", str(bad), "-C", str(tmp_path), "evil"], check=True)
    with pytest.raises(RuntimeError, match="not LC's world"):
        backups.check_archive(store.server("lc"), str(bad))


def test_restore_puts_the_world_back_and_keeps_the_current_one(cluster_env, tmp_path):
    from engine import store, supervisor

    seed_world(tmp_path, text="the good old days")
    assert wait_job(supervisor.enqueue("start", "lc")["job"]["id"])["state"] == "done"
    j = wait_job(supervisor.enqueue("backup", "lc", params={"kind": "manual"})["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    good = store.backups("lc", 1)[0]["id"]
    (world(tmp_path) / "lc_WP.ark").write_text("the wipe")
    time.sleep(1.05)
    j = wait_job(supervisor.enqueue("restore", "lc", params={"backup_id": good})["job"]["id"], timeout=30)
    assert j["state"] == "done", log_of(j["id"])
    assert (world(tmp_path) / "lc_WP.ark").read_text() == "the good old days"
    aside = list((tmp_path / "Instance_lc" / "Saved").glob(".pre-restore-*"))
    assert len(aside) == 1
    assert (aside[0] / "SavedArks" / "lc_WP" / "lc_WP.ark").read_text() == "the wipe"
    assert store.backups("lc", 1)[0]["kind"] == "pre-restore"
    calls = [c[0] for c in cluster_env["calls"]() if c[0] in ("start", "stop")]
    assert calls[-2:] == ["stop", "start"]                 # it was running, so it runs again
    assert "SaveWorld" in cluster_env["rcons"]["lc"].commands


def test_warned_restart_counts_down_in_chat(cluster_env, monkeypatch):
    from engine import lifecycle, supervisor

    monkeypatch.setattr(lifecycle, "WARN_UNIT", 0.01)
    assert wait_job(supervisor.enqueue("start", "lc")["job"]["id"])["state"] == "done"
    j = wait_job(supervisor.enqueue("warned_restart", "lc", params={"warn": [5, 1], "message": "Nap time"})["job"]["id"],
                 timeout=30)
    assert j["state"] == "done", log_of(j["id"])
    chat = [c for c in cluster_env["rcons"]["lc"].commands if c.startswith("ServerChat")]
    assert chat[:2] == ["ServerChat Nap time in 5 minutes.", "ServerChat Nap time in 1 minute."]
    assert [c[0] for c in cluster_env["calls"]() if c[0] in ("start", "stop")][-2:] == ["stop", "start"]


def test_schedules_run_when_due(cluster_env, tmp_path):
    from engine import scheduler, store

    seed_world(tmp_path)
    scheduler.ensure_defaults()
    scheds = {s["kind"]: s for s in store.schedules("main")}
    assert scheds["backup"]["enabled"] and not scheds["restart"]["enabled"]
    now = time.time()
    store.set_schedule(scheds["backup"]["id"], next_run=now - 10)
    started = scheduler.tick(now)
    assert started and started[0][0] == "backup"
    assert store.schedules("main")[0]["next_run"] > now
    # A restart missed by more than an hour is skipped, not run late.
    store.set_schedule(scheds["restart"]["id"], enabled=1, next_run=now - 7200)
    assert scheduler.tick(now) == []
    assert "Skipped" in {s["kind"]: s for s in store.schedules("main")}["restart"]["last_result"]


def test_auto_update_only_queues_when_a_map_is_behind(cluster_env, monkeypatch):
    from engine import scheduler, steam, store, supervisor

    scheduler.ensure_defaults()
    sched = {s["kind"]: s for s in store.schedules("main")}["update"]
    assert not sched["enabled"] and sched["params"]["warn"] == [15, 5, 1]
    queued = []
    monkeypatch.setattr(supervisor, "enqueue_update_cluster",
                        lambda c, requested_by="admin", warn=None, message="": queued.append((c, warn, message))
                        or {"ok": True})
    monkeypatch.setattr(steam, "cached_latest", lambda: ("200", "", time.time()))
    monkeypatch.setattr(steam, "installed_build", lambda d: "200")
    now = time.time()
    store.set_schedule(sched["id"], enabled=1, next_run=now - 10)
    scheduler.tick(now)
    assert queued == []
    assert {s["kind"]: s for s in store.schedules("main")}["update"]["last_result"] == "Up to date (build 200)."
    monkeypatch.setattr(steam, "installed_build", lambda d: "100")
    store.set_schedule(sched["id"], next_run=now - 10)
    scheduler.tick(now)
    assert queued == [("main", [15, 5, 1], "Server update")]
    # Missed by more than an hour (the app was down): skipped, like the daily restart.
    store.set_schedule(sched["id"], next_run=now - 7200)
    scheduler.tick(now)
    assert len(queued) == 1


def test_asa_history_files_are_left_out(cluster_env, tmp_path):
    from engine import backups, store, supervisor

    seed_world(tmp_path)
    w = world(tmp_path)
    for name in ("lc_WP_11.11.2026_18.56.53.arkrbf", "lc_WP_10.11.2026_00.18.58.ark",
                 "lc_WP_AntiCorruptionBackup.bak"):
        (w / name).write_text("history")
    (w / "0002aaaa.arktributetribe").write_text("tribe")
    j = wait_job(supervisor.enqueue("backup", "lc", params={"kind": "manual"})["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    names = {n.rsplit("/", 1)[-1] for n in backups.archive_members(store.backups("lc", 1)[0]["path"])}
    assert {"lc_WP.ark", "0002aaaa.arktributetribe", "Game.ini"} <= names
    assert not names & {"lc_WP_11.11.2026_18.56.53.arkrbf", "lc_WP_10.11.2026_00.18.58.ark",
                        "lc_WP_AntiCorruptionBackup.bak"}
