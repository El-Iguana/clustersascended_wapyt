"""Players: sessions, history import, cluster bans, kicks, messages; the console."""
from __future__ import annotations

import json
import time

import pytest

from test_orchestrator import cluster_env, log_of, wait_job  # noqa: F401 - fixture

A = "0002" + "a" * 28
B = "0002" + "b" * 28


def test_sessions_follow_the_roster(data_dir):
    from engine import players

    players.record_roster("lc", [{"name": "Ann", "id": A}], 100)
    players.record_roster("lc", [{"name": "Ann", "id": A}, {"name": "Bob, The Builder", "id": B}], 130)
    players.record_roster("lc", None, 160)                       # RCON silent: nobody "leaves"
    players.record_roster("lc", [{"name": "Bob, The Builder", "id": B}], 190)
    hist = players.history(["lc"])
    ann = next(h for h in hist if h["eos_id"] == A)
    assert (ann["joined_at"], ann["left_at"]) == (100, 190)
    players.close_all("lc", 250)
    known = {p["id"]: p for p in players.known_players(["lc"])}
    assert known[A]["seconds"] == 90 and known[B]["seconds"] == 120
    assert known[B]["name"] == "Bob, The Builder"


def test_old_webadmin_log_is_imported_once(data_dir, tmp_path):
    from engine import players

    log = tmp_path / "logins.jsonl"
    log.write_text("\n".join(json.dumps(e) for e in [
        {"ts": "2026-07-15T16:53:36+00:00", "service": "asa-gen", "event": "join", "name": "Fishdog", "id": A},
        {"ts": "2026-07-15T17:53:36+00:00", "service": "asa-gen", "event": "leave", "name": "Fishdog", "id": A},
        {"ts": "2026-07-16T10:00:00+00:00", "service": "asa-other", "event": "join", "name": "X", "id": B},
    ]))
    assert players.import_webadmin_log(str(log), {"asa-gen": "gen"}) == 1
    assert players.import_webadmin_log(str(log), {"asa-gen": "gen"}) == 0
    (p,) = players.known_players(["gen"])
    assert (p["name"], p["seconds"]) == ("Fishdog", 3600)


def test_a_cluster_ban_reaches_running_maps_now_and_stopped_ones_at_start(cluster_env):
    from engine import players, store, supervisor

    assert wait_job(supervisor.enqueue("start", "lc")["job"]["id"])["state"] == "done"
    result = players.ban("main", A, "Griefer", "spam", "admin")
    assert result["done"] == ["LC"] and set(result["later"]) == {"AST", "GEN"}
    assert f"BanPlayer {A}" in cluster_env["rcons"]["lc"].commands
    j = wait_job(supervisor.enqueue("start", "ast")["job"]["id"])
    assert j["state"] == "done", log_of(j["id"])
    assert f"BanPlayer {A}" in cluster_env["rcons"]["ast"].commands
    assert "Applied 1 cluster ban" in log_of(j["id"])
    (ban,) = players.bans("main")
    assert set(ban["applied"]) == {"lc", "ast"}
    result = players.unban("main", A)
    assert set(result["done"]) == {"LC", "AST"} and result["later"] == ["GEN"]
    assert players.bans("main") == []
    with pytest.raises(players.Invalid):
        players.ban("main", "not-an-id", "", "", "admin")


def test_kick_and_messages(cluster_env):
    from engine import players, supervisor

    assert wait_job(supervisor.enqueue("start", "lc")["job"]["id"])["state"] == "done"
    players.kick("lc", A)
    assert f"KickPlayer {A}" in cluster_env["rcons"]["lc"].commands
    r = players.message(["lc", "ast"], "Dinner time!", big=True)
    assert r == {"sent": ["LC"], "skipped": ["AST"]}
    assert "Broadcast Dinner time!" in cluster_env["rcons"]["lc"].commands
    with pytest.raises(players.Invalid):
        players.message(["lc"], "two\nlines")


def test_console_tail_is_incremental_and_restarts_on_a_new_log(cluster_env, tmp_path):
    from engine import console, store

    s = store.server("lc")
    log = tmp_path / "Instance_lc" / "Saved" / "Logs" / "ShooterGame.log"
    log.write_text("one\ntwo\n")
    first = console.tail(s, None, None)
    assert first["text"] == "one\ntwo\n" and first["reset"]
    with open(log, "a") as fh:
        fh.write("three\n")
    more = console.tail(s, first["inode"], first["offset"])
    assert more["text"] == "three\n" and not more["reset"]
    log.unlink()
    log.write_text("fresh boot\n")
    again = console.tail(s, more["inode"], more["offset"])
    assert again["reset"] and again["text"] == "fresh boot\n"


def test_console_command(cluster_env):
    from engine import console, store, supervisor

    assert wait_job(supervisor.enqueue("start", "lc")["job"]["id"])["state"] == "done"
    assert console.command(store.server("lc"), "SaveWorld") == "World Saved"
    with pytest.raises(ValueError):
        console.command(store.server("lc"), "")
