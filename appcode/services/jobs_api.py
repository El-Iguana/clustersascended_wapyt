"""BFF: the job history and each job's log."""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy

MAX_LOG = 256 * 1024


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class JobsApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def list(self, limit: int = 100) -> dict:
        from engine import store

        names = {s.id: s.name for s in store.servers()}
        jobs = store.jobs(limit=max(1, min(int(limit), 500)))
        for j in jobs:
            j["server_name"] = names.get(j["server"], j["server"] or "")
        return {"ok": True, "jobs": jobs}

    def log(self, job_id: int) -> dict:
        from engine import store

        j = store.job(int(job_id))
        if j is None:
            return {"ok": False, "error": "No such job."}
        path = store.job_log_path(int(job_id))
        text = ""
        if path.exists():
            size = path.stat().st_size
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                if size > MAX_LOG:
                    fh.seek(size - MAX_LOG)
                    fh.readline()
                text = fh.read()
        return {"ok": True, "job": j, "log": text}

    def cancel(self, job_id: int) -> dict:
        from engine import supervisor

        return supervisor.cancel(int(job_id))
