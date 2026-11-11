"""
SQLite storage (``<data>/clusters.db``), one connection per thread, WAL.

Clusters and servers keep their settings as JSON columns; runs, jobs and the
audit log are rows. After every change to the model ``export_config()``
writes ``config-export.json`` so changes can be diffed and backed up.
"""
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

from engine import paths
from engine.models import Cluster, HostSettings, Server

_local = threading.local()
_init_lock = threading.Lock()

MIGRATIONS = [
    # 1
    """
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    CREATE TABLE clusters (id TEXT PRIMARY KEY, data TEXT NOT NULL, created REAL NOT NULL);
    CREATE TABLE servers (
        id TEXT PRIMARY KEY, cluster TEXT NOT NULL REFERENCES clusters(id),
        container_name TEXT NOT NULL UNIQUE, game_port INTEGER NOT NULL UNIQUE,
        rcon_port INTEGER NOT NULL UNIQUE, data TEXT NOT NULL, created REAL NOT NULL);
    CREATE TABLE server_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, server TEXT NOT NULL, started_at REAL NOT NULL,
        ready_at REAL, stopped_at REAL, ready_mem INTEGER, peak_total INTEGER,
        end_reason TEXT, source TEXT NOT NULL DEFAULT 'app');
    CREATE INDEX server_runs_server ON server_runs(server, started_at DESC);
    CREATE TABLE jobs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, lane TEXT NOT NULL, kind TEXT NOT NULL,
        server TEXT, params TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL,
        requested_by TEXT NOT NULL, created REAL NOT NULL, started REAL, finished REAL,
        summary TEXT NOT NULL DEFAULT '');
    CREATE INDEX jobs_state ON jobs(state, lane, id);
    CREATE TABLE audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, actor TEXT NOT NULL,
        action TEXT NOT NULL, target TEXT NOT NULL DEFAULT '', detail TEXT NOT NULL DEFAULT '{}');
    """,
    # 2
    """
    CREATE TABLE backups (
        id INTEGER PRIMARY KEY AUTOINCREMENT, server TEXT NOT NULL, cluster TEXT NOT NULL,
        kind TEXT NOT NULL, path TEXT NOT NULL, size INTEGER NOT NULL, created REAL NOT NULL,
        build_id TEXT, pinned INTEGER NOT NULL DEFAULT 0, note TEXT NOT NULL DEFAULT '');
    CREATE INDEX backups_server ON backups(server, created DESC);
    """,
    # 3
    """
    CREATE TABLE mods (id TEXT PRIMARY KEY, name TEXT NOT NULL DEFAULT '', note TEXT NOT NULL DEFAULT '',
        updated REAL NOT NULL);
    """,
    # 4: startup and running memory are measured apart; jobs carry a stage
    """
    ALTER TABLE server_runs ADD COLUMN peak_start INTEGER;
    ALTER TABLE server_runs ADD COLUMN settled INTEGER;
    ALTER TABLE server_runs ADD COLUMN peak_run INTEGER;
    ALTER TABLE jobs ADD COLUMN stage TEXT NOT NULL DEFAULT '';
    ALTER TABLE jobs ADD COLUMN detail TEXT NOT NULL DEFAULT '';
    ALTER TABLE jobs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0;
    """,
    # 5: schedules (nightly backups, daily restarts)
    """
    CREATE TABLE schedules (
        id INTEGER PRIMARY KEY AUTOINCREMENT, cluster TEXT NOT NULL, kind TEXT NOT NULL,
        at TEXT NOT NULL, days TEXT NOT NULL DEFAULT '[0,1,2,3,4,5,6]', params TEXT NOT NULL DEFAULT '{}',
        enabled INTEGER NOT NULL DEFAULT 1, last_run REAL, next_run REAL, last_result TEXT NOT NULL DEFAULT '',
        UNIQUE(cluster, kind));
    """,
]


def db_path() -> Path:
    return paths.data_dir() / "clusters.db"


def conn() -> sqlite3.Connection:
    """This thread's connection (created and migrated on first use)."""
    c = getattr(_local, "conn", None)
    path = str(db_path())
    if c is not None and getattr(_local, "path", None) == path:
        return c
    paths.data_dir().mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    c.execute("PRAGMA busy_timeout=30000")
    with _init_lock:
        version = c.execute("PRAGMA user_version").fetchone()[0]
        for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            c.executescript("BEGIN;" + sql + f"PRAGMA user_version={number};COMMIT;")
    _local.conn, _local.path = c, path
    return c


def reset_thread_connection() -> None:
    """Tests switch data dirs between cases."""
    c = getattr(_local, "conn", None)
    if c is not None:
        c.close()
    _local.conn = None


# -- settings -----------------------------------------------------------------

def host_settings() -> HostSettings:
    row = conn().execute("SELECT value FROM settings WHERE key='host'").fetchone()
    return HostSettings.from_dict(json.loads(row[0]) if row else {})


def save_host_settings(settings: HostSettings) -> None:
    conn().execute("INSERT INTO settings(key, value) VALUES('host', ?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (json.dumps(settings.to_dict()),))
    export_config()


# -- clusters and servers ------------------------------------------------------

def clusters() -> list[Cluster]:
    rows = conn().execute("SELECT data FROM clusters ORDER BY created, id").fetchall()
    return [Cluster(**json.loads(r[0])) for r in rows]


def cluster(cluster_id: str) -> Optional[Cluster]:
    row = conn().execute("SELECT data FROM clusters WHERE id=?", (cluster_id,)).fetchone()
    return Cluster(**json.loads(row[0])) if row else None


def save_cluster(c: Cluster) -> None:
    conn().execute(
        "INSERT INTO clusters(id, data, created) VALUES(?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET data=excluded.data",
        (c.id, json.dumps(c.to_dict()), time.time()))
    export_config()


def servers(cluster_id: Optional[str] = None) -> list[Server]:
    sql = "SELECT data FROM servers"
    args: tuple = ()
    if cluster_id:
        sql += " WHERE cluster=?"
        args = (cluster_id,)
    rows = conn().execute(sql + " ORDER BY created, id", args).fetchall()
    out = [Server(**json.loads(r[0])) for r in rows]
    return sorted(out, key=lambda s: (s.cluster, s.priority, s.name))


def server(server_id: str) -> Optional[Server]:
    row = conn().execute("SELECT data FROM servers WHERE id=?", (server_id,)).fetchone()
    return Server(**json.loads(row[0])) if row else None


def save_server(s: Server) -> None:
    conn().execute(
        "INSERT INTO servers(id, cluster, container_name, game_port, rcon_port, data, created) "
        "VALUES(?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET cluster=excluded.cluster, "
        "container_name=excluded.container_name, game_port=excluded.game_port, "
        "rcon_port=excluded.rcon_port, data=excluded.data",
        (s.id, s.cluster, s.container_name, s.game_port, s.rcon_port,
         json.dumps(s.to_dict()), time.time()))
    export_config()


# -- runs ---------------------------------------------------------------------

def open_run(server_id: str, started_at: float, source: str = "app") -> int:
    cur = conn().execute("INSERT INTO server_runs(server, started_at, source) VALUES(?, ?, ?)",
                         (server_id, started_at, source))
    return int(cur.lastrowid)


def open_run_for(server_id: str) -> Optional[sqlite3.Row]:
    return conn().execute(
        "SELECT * FROM server_runs WHERE server=? AND stopped_at IS NULL "
        "ORDER BY started_at DESC LIMIT 1", (server_id,)).fetchone()


def update_run(run_id: int, **fields) -> None:
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE server_runs SET {cols} WHERE id=?", (*fields.values(), run_id))


def bump_peak(run_id: int, total: int) -> None:
    conn().execute("UPDATE server_runs SET peak_total=MAX(COALESCE(peak_total, 0), ?) WHERE id=?",
                   (int(total), run_id))


def need_samples(server_id: str, limit: int = 5) -> tuple[list[int], list[int]]:
    """
    Measured memory of the last runs: (running samples, startup samples).
    Running: the most a map held once fully started (settled or later, or the
    "(xx GB Mem)" its log printed at ready). Startup: the most it held while
    loading, up to the end of its settle period.
    """
    rows = conn().execute(
        "SELECT ready_mem, settled, peak_run, peak_start FROM server_runs WHERE server=? "
        "ORDER BY started_at DESC LIMIT ?", (server_id, limit)).fetchall()
    running = [max(r[0] or 0, r[1] or 0, r[2] or 0) for r in rows]
    startup = [r[3] for r in rows if r[3]]
    return [v for v in running if v], startup


def bump_run_peak(run_id: int, total: int) -> None:
    conn().execute("UPDATE server_runs SET peak_run=MAX(COALESCE(peak_run, 0), ?) WHERE id=?",
                   (int(total), run_id))


def recent_peaks(server_id: str, limit: int = 5) -> list[int]:
    rows = conn().execute(
        "SELECT MAX(COALESCE(peak_total, 0), COALESCE(ready_mem, 0)) FROM server_runs "
        "WHERE server=? ORDER BY started_at DESC LIMIT ?", (server_id, limit)).fetchall()
    return [int(r[0]) for r in rows if r[0]]


# -- jobs ---------------------------------------------------------------------

def add_job(lane: str, kind: str, server_id: Optional[str], params: dict, requested_by: str,
            state: str = "queued", summary: str = "") -> int:
    cur = conn().execute(
        "INSERT INTO jobs(lane, kind, server, params, state, requested_by, created, summary) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (lane, kind, server_id, json.dumps(params or {}), state, requested_by, time.time(), summary))
    return int(cur.lastrowid)


def job(job_id: int) -> Optional[dict]:
    row = conn().execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    return _job(row) if row else None


def jobs(limit: int = 100, states: Optional[tuple] = None) -> list[dict]:
    sql, args = "SELECT * FROM jobs", []
    if states:
        sql += f" WHERE state IN ({','.join('?' * len(states))})"
        args = list(states)
    rows = conn().execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
    return [_job(r) for r in rows]


def next_queued(lane: str) -> Optional[dict]:
    row = conn().execute("SELECT * FROM jobs WHERE lane=? AND state='queued' ORDER BY id LIMIT 1",
                         (lane,)).fetchone()
    return _job(row) if row else None


def set_job(job_id: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def _job(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["params"] = json.loads(d.get("params") or "{}")
    return d


def job_log_path(job_id: int) -> Path:
    d = paths.data_dir() / "jobs"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{job_id}.log"


# -- backups ------------------------------------------------------------------

def add_backup(server_id: str, cluster_id: str, kind: str, path: str, size: int,
               build_id: Optional[str], note: str = "") -> int:
    cur = conn().execute(
        "INSERT INTO backups(server, cluster, kind, path, size, created, build_id, note) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (server_id, cluster_id, kind, path, size, time.time(), build_id, note))
    return int(cur.lastrowid)


def backups(server_id: Optional[str] = None, limit: int = 100) -> list[dict]:
    sql, args = "SELECT * FROM backups", []
    if server_id:
        sql += " WHERE server=?"
        args.append(server_id)
    rows = conn().execute(sql + " ORDER BY created DESC LIMIT ?", (*args, limit)).fetchall()
    return [dict(r) for r in rows]


def backup(backup_id: int) -> Optional[dict]:
    row = conn().execute("SELECT * FROM backups WHERE id=?", (backup_id,)).fetchone()
    return dict(row) if row else None


def set_backup(backup_id: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE backups SET {cols} WHERE id=?", (*fields.values(), backup_id))


def delete_backup_row(backup_id: int) -> None:
    conn().execute("DELETE FROM backups WHERE id=?", (backup_id,))


# -- schedules ----------------------------------------------------------------

def schedules(cluster_id: Optional[str] = None) -> list[dict]:
    sql, args = "SELECT * FROM schedules", []
    if cluster_id:
        sql += " WHERE cluster=?"
        args.append(cluster_id)
    out = []
    for r in conn().execute(sql + " ORDER BY cluster, kind", args).fetchall():
        d = dict(r)
        d["days"] = json.loads(d["days"])
        d["params"] = json.loads(d["params"])
        d["enabled"] = bool(d["enabled"])
        out.append(d)
    return out


def save_schedule(cluster_id: str, kind: str, at: str, days: list, params: dict, enabled: bool,
                  next_run: Optional[float]) -> None:
    conn().execute(
        "INSERT INTO schedules(cluster, kind, at, days, params, enabled, next_run) VALUES(?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(cluster, kind) DO UPDATE SET at=excluded.at, days=excluded.days, params=excluded.params, "
        "enabled=excluded.enabled, next_run=excluded.next_run",
        (cluster_id, kind, at, json.dumps(days), json.dumps(params), int(enabled), next_run))


def set_schedule(schedule_id: int, **fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    conn().execute(f"UPDATE schedules SET {cols} WHERE id=?", (*fields.values(), schedule_id))


def recent_runs(server_id: str, since: float) -> list[dict]:
    rows = conn().execute(
        "SELECT * FROM server_runs WHERE server=? AND started_at>=? AND source!='import' "
        "ORDER BY started_at DESC", (server_id, since)).fetchall()
    return [dict(r) for r in rows]


# -- mod names ----------------------------------------------------------------

def mod_names(ids=None) -> dict:
    rows = conn().execute("SELECT id, name FROM mods").fetchall()
    names = {r[0]: r[1] for r in rows if r[1]}
    return names if ids is None else {i: names[i] for i in ids if i in names}


def set_mod_name(mod_id: str, name: str) -> None:
    conn().execute("INSERT INTO mods(id, name, updated) VALUES(?, ?, ?) "
                   "ON CONFLICT(id) DO UPDATE SET name=excluded.name, updated=excluded.updated",
                   (mod_id, name, time.time()))


# -- audit --------------------------------------------------------------------

def audit(actor: str, action: str, target: str = "", detail: Optional[dict] = None) -> None:
    conn().execute("INSERT INTO audit(ts, actor, action, target, detail) VALUES(?, ?, ?, ?, ?)",
                   (time.time(), actor, action, target, json.dumps(detail or {})))


# -- export -------------------------------------------------------------------

def export_config() -> None:
    """Write the model to ``config-export.json`` atomically (secrets included: it is local)."""
    data = {
        "host": host_settings().to_dict(),
        "clusters": [c.to_dict() for c in clusters()],
        "servers": [s.to_dict() for s in servers()],
    }
    target = paths.data_dir() / "config-export.json"
    fd, tmp = tempfile.mkstemp(dir=str(target.parent), prefix=".export-")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
