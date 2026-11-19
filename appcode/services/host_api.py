"""
BFF: the dashboard's data and the host's memory rules.

pytincture re-executes this module on every call: nothing lives here, and
the engine is imported inside the methods (it is never shipped to the
browser).
"""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class HostApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def memory_history(self, range_key: str = "24h") -> dict:
        """Every map's memory over time, stacked, with the host's RAM and budget."""
        from engine import history

        return {"ok": True, **history.overview(str(range_key))}

    def overview(self) -> dict:
        """Host memory, every cluster with its maps, previews, and active jobs."""
        from engine import admission, podman, state, store, supervisor, views

        snap = state.snapshot()
        s = snap.settings
        host = snap.host
        managed = sum(v.actual or 0 for v in snap.servers.values())
        budget = host.mem_total + s.swap_allowance - s.reserve
        pending = supervisor.pending()
        active = store.jobs(limit=50, states=("queued", "running"))
        job_by_server = {}
        for j in reversed(active):
            if j["lane"] != "io":          # a backup does not change what the map can do
                job_by_server[j["server"]] = j
        names = {x.id: x.name for x in store.servers()}
        clusters = []
        for c in store.clusters():
            rows = []
            for srv in store.servers(c.id):
                v = snap.servers[srv.id]
                lv = state.live(srv.id)
                preview = None
                if v.phase in ("stopped", "absent", "unknown") and srv.id not in job_by_server:
                    preview = admission.admit(snap, "start", srv.id, pending).to_dict()
                job = job_by_server.get(srv.id)
                rows.append({
                    "need_start": v.start_need,
                    "id": srv.id, "name": srv.name, "map": srv.map_name, "session": srv.session_name,
                    "container": srv.container_name, "game_port": srv.game_port, "rcon_port": srv.rcon_port,
                    "priority": srv.priority, "phase": v.phase, "container_state": lv.container_state,
                    "need": v.need, "actual": v.actual, "peak": lv.peak,
                    "mem": lv.mem, "players": lv.players, "started_at": lv.started_at or None,
                    "ready_at": lv.ready_at, "hang": lv.hang,
                    "flags": views.quick_flags(srv, c, s),
                    "preview": preview,
                    "job": {"id": job["id"], "kind": job["kind"], "state": job["state"], "stage": job.get("stage"),
                            "detail": job.get("detail"), "attempt": job.get("attempt")} if job else None,
                })
            committed = sum(admission.hold(snap.servers[r["id"]], r["phase"]) for r in rows
                            if r["phase"] in ("starting", "ready", "stopping", "updating"))
            clusters.append({**_cluster_dict(c), "servers": rows, "committed": committed})
        return {
            "ok": True,
            "podman_degraded": podman.degraded,
            "watchdog": views.watchdog_status(),
            "updated_at": state.updated_at(),
            "host": {
                "mem_total": host.mem_total, "mem_available": host.mem_available,
                "swap_total": host.swap_total, "swap_free": host.swap_free,
                "used_total": host.used_total, "psi_some10": host.psi_some10, "psi_full10": host.psi_full10,
                "managed": managed, "external": max(0, host.used_total - managed),
                "budget": budget, "reserve": s.reserve, "swap_allowance": s.swap_allowance,
                "max_running": s.max_running,
                "committed": sum(admission.hold(v, v.phase) for v in snap.servers.values()
                                 if v.phase in ("starting", "ready", "stopping", "updating")),
            },
            "clusters": clusters,
            "jobs": active,
            "queue": [{"id": j["id"], "kind": j["kind"], "server": j["server"],
                       "name": names.get(j["server"]) or j["params"].get("name") or j["server"], "state": j["state"],
                       "stage": j.get("stage"), "detail": j.get("detail"), "attempt": j.get("attempt"),
                       "requested_by": j["requested_by"]} for j in supervisor.queue()],
        }

    def settings(self) -> dict:
        from engine import store

        return {"ok": True, "settings": store.host_settings().to_dict()}

    def save_settings(self, values: dict) -> dict:
        """Change the host rules. Sizes come in GiB from the form."""
        from engine import store
        from engine.models import GiB, HostSettings
        from services.auth import actor

        current = store.host_settings().to_dict()
        gib_fields = ("swap_allowance", "reserve", "default_need", "warn_free", "crit_free")
        int_fields = ("max_running", "settle_s", "ready_timeout_s", "rcon_timeout_s",
                      "stop_timeout_s", "exit_wait_s", "save_timeout_s", "update_timeout_s", "crit_sustain_s",
                      "watchdog_cooldown_s", "memory_recheck_s", "memory_wait_s", "start_attempts",
                      "retry_delay_s")
        float_fields = ("need_margin", "psi_crit", "startup_factor")
        bool_fields = ("watchdog_enabled", "crash_restart")
        try:
            for key in gib_fields:
                if key in values and values[key] not in (None, ""):
                    current[key] = int(float(values[key]) * GiB)
            for key in int_fields:
                if key in values and values[key] not in (None, ""):
                    current[key] = int(values[key])
            for key in float_fields:
                if key in values and values[key] not in (None, ""):
                    current[key] = float(values[key])
            for key in bool_fields:
                if key in values:
                    current[key] = values[key] in (True, "true", "on", "1", 1)
        except (TypeError, ValueError):
            return {"ok": False, "error": "Numbers only, please."}
        if not 1.0 <= current["startup_factor"] <= 3.0:
            return {"ok": False, "error": "The startup factor must be between 1.0 and 3.0."}
        if not 1 <= current["start_attempts"] <= 5:
            return {"ok": False, "error": "Tries per start: 1 to 5."}
        if current["memory_recheck_s"] < 5:
            return {"ok": False, "error": "Check memory at most every 5 seconds."}
        if current["crit_free"] >= current["warn_free"]:
            return {"ok": False, "error": "The critical level must be below the warning level."}
        if current["max_running"] < 1 or current["reserve"] < 0 or current["swap_allowance"] < 0:
            return {"ok": False, "error": "Max running must be at least 1 and sizes cannot be negative."}
        if not 1.0 <= current["need_margin"] <= 2.0:
            return {"ok": False, "error": "The margin must be between 1.0 and 2.0."}
        store.save_host_settings(HostSettings.from_dict(current))
        store.audit(actor(self._user), "host settings", "", current)
        return {"ok": True, "settings": current}


def _cluster_dict(c) -> dict:
    d = c.to_dict()
    defaults = dict(d.get("defaults") or {})
    for key in ("admin_password", "server_password"):
        if defaults.get(key):
            defaults[key] = "••••••"
    d["defaults"] = defaults
    return d
