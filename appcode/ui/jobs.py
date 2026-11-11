"""
Jobs: everything the app did or refused, newest first, and a job's log
(``#/jobs/<id>``), which follows a running job live.
"""
from __future__ import annotations

import js

from services.fmt import duration
from services.jobs_api import JobsApi
from ui.common import Listeners, attr, closest, esc, failed, spawn

_STATE_KIND = {"done": "ok", "running": "warn", "queued": "warn", "failed": "off",
               "refused": "off", "cancelled": "off", "interrupted": "off"}


def _clock(ts) -> str:
    if not ts:
        return "–"
    return str(js.Date.new(float(ts) * 1000).toLocaleString())


class JobsView:
    def __init__(self, container, job_id: int | None = None) -> None:
        self.el = container
        self.job_id = job_id
        self.listeners = Listeners()

    def mount(self) -> None:
        self.el.innerHTML = '<div class="ca-page" id="ca-jobs"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.every(2500, lambda: spawn(self.refresh(), "jobs refresh"))
        spawn(self.refresh(), "jobs load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def refresh(self) -> None:
        root = js.document.getElementById("ca-jobs")
        if self.job_id is not None:
            result = await JobsApi().log_async(self.job_id)
            if failed(result, "load the job"):
                return
            if root:
                pre = js.document.getElementById("ca-joblog")
                follow = (not pre) or (pre.scrollTop + pre.clientHeight >= pre.scrollHeight - 30)
                root.innerHTML = self._log(result)
                pre = js.document.getElementById("ca-joblog")
                if pre and follow:
                    pre.scrollTop = pre.scrollHeight
            return
        result = await JobsApi().list_async(150)
        if failed(result, "load the jobs"):
            return
        if root:
            root.innerHTML = self._table(result["jobs"])

    def _table(self, jobs: list) -> str:
        rows = []
        for j in jobs:
            took = (j["finished"] - j["started"]) if j.get("finished") and j.get("started") else None
            rows.append(
                f'<tr data-job="{j["id"]}"><td>{j["id"]}</td><td>{_clock(j["created"])}</td>'
                f'<td>{esc(j["kind"].replace("_", " "))}</td><td>{esc(j.get("server_name") or "")}</td>'
                f'<td><span class="ca-badge" data-kind="{_STATE_KIND.get(j["state"], "off")}">{esc(j["state"])}</span></td>'
                f'<td>{duration(took) if took is not None else ""}</td>'
                f'<td class="ca-summary">{esc(j.get("summary") or "")}</td><td>{esc(j["requested_by"])}</td></tr>')
        body = "".join(rows) or '<tr><td colspan="8" class="ca-empty">No jobs yet.</td></tr>'
        return ('<div class="ca-page-head"><h1>Jobs</h1><span class="ca-sub">Starts, stops and refusals. '
                'Click one for its log.</span></div>'
                '<div class="ca-card ca-table-wrap"><table class="ca-table"><thead><tr><th>#</th><th>When</th>'
                '<th>What</th><th>Map</th><th>State</th><th>Took</th><th>Result</th><th>By</th></tr></thead>'
                f"<tbody>{body}</tbody></table></div>")

    def _log(self, result: dict) -> str:
        j = result["job"]
        kind = _STATE_KIND.get(j["state"], "off")
        cancel = (f'<button class="ca-btn" data-act="cancel" data-job="{j["id"]}">Cancel</button>'
                  if j["state"] == "queued" else "")
        return (f'<div class="ca-page-head"><a class="ca-back" href="#/jobs">←</a><h1>Job {j["id"]}: '
                f'{esc(j["kind"].replace("_", " "))}</h1><span class="ca-badge" data-kind="{kind}">'
                f'{esc(j["state"])}</span>{cancel}</div>'
                f'<div class="ca-sub">{esc(j.get("summary") or "")}</div>'
                f'<pre class="ca-log" id="ca-joblog">{esc(result.get("log") or "(no output yet)")}</pre>')

    def _on_click(self, event) -> None:
        btn = closest(event.target, '[data-act="cancel"]')
        if btn:
            spawn(self._cancel(int(attr(btn, "data-job"))), "cancel")
            return
        row = closest(event.target, "tr[data-job]")
        if row:
            js.window.location.hash = f"#/jobs/{attr(row, 'data-job')}"

    async def _cancel(self, job_id: int) -> None:
        result = await JobsApi().cancel_async(job_id)
        failed(result, "cancel the job")
        await self.refresh()
