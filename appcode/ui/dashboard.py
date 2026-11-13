"""
The dashboard: the host's memory against its budget, then each cluster with
its maps. Every start shows whether it would be admitted, and why not.
Refreshes every 3 s.
"""
from __future__ import annotations

import js

from wapyt import message

from services.fmt import duration, gib, phase_label
from services.host_api import HostApi
from services.jobs_api import JobsApi
from services.server_api import ServerApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast

REFRESH_MS = 3000

# What a start (or restart, update) is doing, in words.
STAGES = {
    "queued": "Waiting its turn", "running": "Starting", "waiting_memory": "Waiting for memory",
    "preparing": "Getting ready", "loading": "Loading the world", "verifying": "Checking it answers",
    "settling": "Settling", "measuring": "Measuring memory", "retrying": "Retrying",
    "stopping": "Saving and stopping", "making_room": "Making room", "backing_up": "Backing up",
    "restoring": "Unpacking the backup", "updating": "Updating", "warning": "Counting down in chat", "copying": "Copying game files",
}
CANCELLABLE = ("start", "restart", "make_room_start", "update", "warned_restart", "add_map", "update_cluster")

# Map colours for the memory bar, by position in the cluster list.
_COLOURS = ("#38bdf8", "#a78bfa", "#34d399", "#f472b6", "#fbbf24", "#60a5fa", "#fb923c", "#2dd4bf")


class DashboardView:
    def __init__(self, container) -> None:
        self.el = container
        self.listeners = Listeners()
        self.data: dict = {}
        self._busy = False

    def mount(self) -> None:
        self.el.innerHTML = '<div class="ca-page" id="ca-dash"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.every(REFRESH_MS, lambda: spawn(self.refresh(), "dashboard refresh"))
        spawn(self.refresh(), "dashboard load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def refresh(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            result = await HostApi().overview_async()
        finally:
            self._busy = False
        if failed(result, "load the dashboard"):
            return
        self.data = result
        root = js.document.getElementById("ca-dash")
        if root:
            root.innerHTML = self._render(result)

    # -- rendering -----------------------------------------------------------

    def _render(self, d: dict) -> str:
        parts = []
        if d.get("podman_degraded"):
            parts.append('<div class="ca-banner" data-kind="error"><span class="mdi mdi-alert"></span>'
                         "podman is not answering (Podman Desktop may be holding its database). "
                         "Readings come from the kernel; actions wait until podman answers.</div>")
        wd = d.get("watchdog") or {}
        if wd.get("level") in ("warn", "critical"):
            kind = "error" if wd["level"] == "critical" else "warning"
            parts.append(f'<div class="ca-banner" data-kind="{kind}"><span class="mdi mdi-memory"></span>'
                         f'{esc(wd.get("message"))}</div>')
        parts.append(self._memory(d))
        if d.get("queue"):
            parts.append(self._queue(d["queue"]))
        if not d.get("clusters"):
            parts.append('<div class="ca-card ca-empty">No clusters yet. '
                         '<a href="#/import">Import your existing compose cluster</a> to begin.</div>')
        colours = {}
        n = 0
        for c in d.get("clusters", []):
            for s in c["servers"]:
                colours[s["id"]] = _COLOURS[n % len(_COLOURS)]
                n += 1
        for c in d.get("clusters", []):
            parts.append(self._cluster(c, colours, d["host"]))
        return "".join(parts)

    def _memory(self, d: dict) -> str:
        h = d["host"]
        total = max(h["budget"], h["used_total"], 1)
        segs = []
        n = 0
        for c in d.get("clusters", []):
            for s in c["servers"]:
                colour = _COLOURS[n % len(_COLOURS)]
                n += 1
                if s.get("actual"):
                    pct = 100 * s["actual"] / total
                    segs.append(f'<div class="ca-seg" style="width:{pct:.2f}%;background:{colour}" '
                                f'title="{esc(s["name"])}: {gib(s["actual"])}"></div>')
        ext = 100 * h["external"] / total
        segs.append(f'<div class="ca-seg ca-seg-ext" style="width:{ext:.2f}%" '
                    f'title="Everything else on the host: {gib(h["external"])}"></div>')
        ram_mark = 100 * h["mem_total"] / total
        budget_mark = 100 * h["budget"] / total
        swap_used = h["swap_total"] - h["swap_free"]
        legend = []
        n = 0
        for c in d.get("clusters", []):
            for s in c["servers"]:
                colour = _COLOURS[n % len(_COLOURS)]
                n += 1
                if s.get("actual"):
                    legend.append(f'<span><i style="background:{colour}"></i>{esc(s["name"])} '
                                  f'{gib(s["actual"])}</span>')
        legend.append(f'<span><i class="ca-ext"></i>Other {gib(h["external"])}</span>')
        psi = h.get("psi_full10") or 0
        pressure = (f'<span class="ca-warn">memory pressure {psi:.0f}%</span>' if psi >= 5 else "")
        return f"""
<section class="ca-card ca-host">
  <div class="ca-host-head">
    <h2>Host memory</h2>
    <div class="ca-sub">RAM {gib(h["mem_total"])} · swap used {gib(swap_used)} of {gib(h["swap_total"])}
      · committed {gib(h["committed"])} of a {gib(h["budget"])} budget · at most {h["max_running"]} maps {pressure}</div>
  </div>
  <div class="ca-bar">{''.join(segs)}
    <div class="ca-mark" style="left:{ram_mark:.2f}%" title="End of RAM"><span>RAM</span></div>
    <div class="ca-mark ca-mark-budget" style="left:{min(budget_mark, 99.8):.2f}%" title="Budget"><span>budget</span></div>
  </div>
  <div class="ca-legend">{''.join(legend)}</div>
  <div class="ca-sub">Budget = RAM + {gib(h["swap_allowance"])} swap allowance − {gib(h["reserve"])} reserve.
    <a href="#/host">Change the rules</a></div>
</section>"""

    def _queue(self, queue: list) -> str:
        rows = []
        for i, j in enumerate(queue, start=1):
            what = (j["kind"].replace("make_room_start", "make room and start")
                    .replace("warned_restart", "restart with a countdown")
                    .replace("add_map", "add a map").replace("_", " "))
            stage = STAGES.get(j.get("stage") or j["state"], j.get("stage") or j["state"])
            tries = f" · try {j['attempt']}" if (j.get("attempt") or 0) > 1 else ""
            detail = esc(j.get("detail") or "")
            cancel = (f'<button class="ca-btn" data-act="cancel" data-job="{j["id"]}">'
                      f'{"Cancel" if j["state"] == "queued" else "Stop this start"}</button>'
                      if j["kind"] in CANCELLABLE or j["state"] == "queued" else "")
            rows.append(
                f'<li class="ca-qrow" data-state="{esc(j["state"])}"><span class="ca-qpos">{i}</span>'
                f'<div class="ca-qbody"><b>{esc(j["name"])}</b> <span class="ca-sub">{esc(what)}</span>'
                f'<div><span class="ca-badge" data-kind="warn">{esc(stage)}</span>{tries} '
                f'<span class="ca-sub">{detail}</span></div></div>'
                f'<div class="ca-qbtns">{cancel}<a class="ca-btn" href="#/jobs/{j["id"]}">Log</a></div></li>')
        more = ('<button class="ca-btn" data-act="cancel_queue">Cancel everything waiting</button>'
                if sum(1 for j in queue if j["state"] == "queued") > 1 else "")
        return f"""
<section class="ca-card">
  <div class="ca-host-head"><h2><span class="mdi mdi-format-list-numbered"></span> Start queue</h2>{more}</div>
  <div class="ca-sub">One map at a time: each waits its turn, checks memory, starts fully and is measured
    before the next one begins.</div>
  <ol class="ca-queue">{"".join(rows)}</ol>
</section>"""

    def _cluster(self, c: dict, colours: dict, host: dict) -> str:
        limits = []
        if c.get("max_running"):
            limits.append(f"at most {c['max_running']} running")
        if c.get("memory_cap"):
            limits.append(f"capped at {gib(c['memory_cap'])}")
        mods = len((c.get("defaults") or {}).get("mod_ids") or [])
        cards = "".join(self._server(s, colours.get(s["id"], "#64748b")) for s in c["servers"])
        behind = " ca-primary" if any((s.get("flags") or {}).get("update_available") for s in c["servers"]) else ""
        return f"""
<section class="ca-cluster">
  <div class="ca-cluster-head">
    <div>
      <h2><span class="mdi mdi-hexagon-multiple-outline"></span> <a class="ca-plain" href="#/cluster/{esc(c["id"])}">{esc(c["name"])}</a></h2>
      <div class="ca-sub">ClusterId <code>{esc(c["cluster_id"])}</code> · transfer folder <code>{esc(c["transfer_dir"])}</code>
        · {mods} shared mods{(' · ' + ', '.join(limits)) if limits else ''}</div>
    </div>
    <div class="ca-sub"><button class="ca-btn" data-act="start_cluster" data-cluster="{esc(c["id"])}">
        <span class="mdi mdi-play-box-multiple-outline"></span> Start all</button>
      <button class="ca-btn{behind}" data-act="update_cluster" data-cluster="{esc(c["id"])}">
        <span class="mdi mdi-update"></span> Update all</button>
      <a href="#/cluster/{esc(c["id"])}/add"><span class="mdi mdi-map-plus"></span> Add a map</a> ·
      <a href="#/cluster/{esc(c["id"])}/settings"><span class="mdi mdi-tune"></span> Game settings</a>
      · <a href="#/cluster/{esc(c["id"])}/mods"><span class="mdi mdi-puzzle-outline"></span> Mods</a>
      · {len(c["servers"])} maps · committed {gib(c["committed"])}</div>
  </div>
  <div class="ca-grid">{cards}</div>
</section>"""

    def _server(self, s: dict, colour: str) -> str:
        label, kind = phase_label(s["phase"])
        job = s.get("job")
        if job:
            stage = job.get("stage") or job["state"]
            label = "In the queue" if job["state"] == "queued" else STAGES.get(stage, label)
            kind = "warn"
        mem = (f"{gib(s['actual'])} now · peak {gib(s['peak'])}" if s.get("actual")
               else "not running")
        players = s.get("players")
        who = ""
        if s["phase"] == "ready":
            if players is None:
                who = '<span class="ca-sub">players: –</span>'
            else:
                names = ", ".join(esc(p["name"]) for p in players[:6])
                who = (f'<a class="ca-players ca-plain" href="#/map/{esc(s["id"])}/players"><span class="mdi mdi-account-multiple"></span> '
                       f'{len(players)}{": " + names if names else ""}</a>')
        uptime = ""
        if s.get("ready_at"):
            uptime = f" · up {duration(js.Date.now() / 1000 - s['ready_at'])}"
        elif s["phase"] == "starting" and s.get("started_at"):
            uptime = f" · loading {duration(js.Date.now() / 1000 - s['started_at'])}"
        if job and job.get("detail"):
            uptime = f" · {esc(job['detail'])}"
        if s.get("hang"):
            uptime += ' · <span class="ca-warn">hang detected</span>'

        flags = s.get("flags") or {}
        chips = []
        if flags.get("exists") and not flags.get("managed"):
            chips.append('<span class="ca-chip" title="Adopt it on the map page">not managed yet</span>')
        if flags.get("drift"):
            chips.append('<span class="ca-chip ca-chip-warn">settings change at next start</span>')
        if flags.get("update_available"):
            chips.append('<span class="ca-chip ca-chip-warn">update available</span>')
        if flags.get("autostart"):
            chips.append('<span class="ca-chip">autostart</span>')
        held = (f'<div class="ca-refusal"><span class="mdi mdi-pause-octagon-outline"></span> {esc(flags["held"])}</div>'
                if flags.get("held") else "")
        preview = ""
        p = s.get("preview")
        if p and not p["ok"]:
            preview = (f'<div class="ca-refusal"><span class="mdi mdi-memory"></span> {esc(p["reason"])} '
                       f"Starting it now puts it in the queue, where it waits for memory."
                       f'<details><summary>Why</summary>{"".join(f"<div>{esc(x)}</div>" for x in p["lines"])}'
                       f"</details></div>")
        elif p and p["ok"]:
            preview = (f'<div class="ca-fits"><span class="mdi mdi-check"></span> Enough memory to start '
                       f'(needs about {gib(s["need_start"])} while loading)</div>')

        buttons = []
        phase = s["phase"]
        sid = esc(s["id"])
        if job and (job["state"] == "queued" or job["kind"] in CANCELLABLE):
            buttons.append(f'<button class="ca-btn" data-act="cancel" data-job="{job["id"]}">'
                           f'{"Cancel" if job["state"] == "queued" else "Stop this start"}</button>')
        elif phase in ("stopped", "absent", "unknown") and not job:
            if p and not p["ok"] and p.get("victims"):
                buttons.append(f'<button class="ca-btn ca-primary" data-act="make_room_start" data-id="{sid}">'
                               f'<span class="mdi mdi-swap-horizontal"></span> Make room and start</button>')
            buttons.append(f'<button class="ca-btn{" ca-primary" if not p or p["ok"] else ""}" data-act="start" '
                           f'data-id="{sid}"><span class="mdi mdi-play"></span> '
                           f'{"Start" if not p or p["ok"] else "Queue start"}</button>')
        if phase in ("ready", "starting") and not job:
            if phase == "ready":
                buttons.append(f'<button class="ca-btn" data-act="restart" data-id="{sid}">'
                               f'<span class="mdi mdi-restart"></span> Restart</button>')
            buttons.append(f'<button class="ca-btn ca-danger" data-act="stop" data-id="{sid}">'
                           f'<span class="mdi mdi-stop"></span> Stop</button>')
        if job:
            buttons.append(f'<a class="ca-btn" href="#/jobs/{job["id"]}"><span class="mdi mdi-text-long"></span> Job</a>')

        return f"""
<article class="ca-server" style="--ca-colour:{colour}">
  <header>
    <div>
      <h3><a class="ca-plain" href="#/map/{esc(s["id"])}">{esc(s["name"])}</a></h3>
      <div class="ca-sub">In the server list as <b>{esc(s["session"])}</b></div>
    </div>
    <span class="ca-badge" data-kind="{kind}">{esc(label)}</span>
  </header>
  <div class="ca-stats">
    <div><span class="ca-k">Needs</span> {gib(s["need"])} running · {gib(s["need_start"])} to start</div>
    <div><span class="ca-k">Memory</span> {mem}</div>
  </div>
  <div class="ca-sub">{uptime.lstrip(" ·")}</div>
  <details class="ca-more ca-sub"><summary>Details</summary>{esc(s["map"])} · port {s["game_port"]} · RCON {s["rcon_port"]}
    · priority {s["priority"]} · container {esc(s["container"])}</details>
  <div class="ca-chips">{''.join(chips)}</div>
  {who}
  {held}
  {preview}
  <div class="ca-actions">{''.join(buttons)}</div>
</article>"""

    # -- actions -------------------------------------------------------------

    def _on_click(self, event) -> None:
        btn = closest(event.target, "[data-act]")
        if not btn or btn.disabled:
            return
        act = attr(btn, "data-act")
        if act == "cancel":
            spawn(self._cancel(int(attr(btn, "data-job"))), "cancel job")
        elif act == "cancel_queue":
            spawn(self._cancel_queue(), "cancel queue")
        elif act == "start_cluster":
            spawn(self._start_cluster(attr(btn, "data-cluster")), "start cluster")
        elif act == "update_cluster":
            spawn(self._update_cluster(attr(btn, "data-cluster")), "update cluster")
        else:
            spawn(self._act(attr(btn, "data-id"), act), act)

    def _server_by_id(self, sid: str) -> dict:
        for c in self.data.get("clusters", []):
            for s in c["servers"]:
                if s["id"] == sid:
                    return s
        return {}

    async def _act(self, sid: str, act: str) -> None:
        s = self._server_by_id(sid)
        name = s.get("name", sid)
        if act == "stop":
            players = len(s.get("players") or [])
            extra = f"\n\n{players} player(s) are online." if players else ""
            if not await message.confirm(f"Save the world and stop {name}?{extra}", title="Stop map",
                                         ok_text="Save and stop", danger=True):
                return
        elif act == "restart":
            if not await message.confirm(f"Save the world and restart {name}?", title="Restart map",
                                         ok_text="Restart"):
                return
        elif act == "make_room_start":
            victims = [self._server_by_id(v).get("name", v) for v in (s.get("preview") or {}).get("victims", [])]
            if not await message.confirm(
                    f"Save and stop {', '.join(victims)}, then start {name}?", title="Make room",
                    ok_text="Stop and start", danger=True):
                return
        result = await ServerApi().act_async(sid, act)
        if result.get("ok"):
            pos = result.get("position")
            where = f" (number {pos} in the queue)" if pos and pos > 1 else ""
            wait = " It will wait for memory." if result.get("waits") else ""
            toast(f"{name}: {act.replace('_', ' ')} queued{where}.{wait}", kind="success")
        else:
            failed(result, f"{act} {name}")
        await self.refresh()

    async def _start_cluster(self, cluster_id: str) -> None:
        if not await message.confirm(
                "Queue a start for every stopped map in this cluster, most important first?\n\n"
                "Each one waits its turn, checks memory, starts fully and is measured before the next. "
                "A map that never gets enough memory gives up after the wait limit.",
                title="Start all", ok_text="Queue them"):
            return
        result = await ServerApi().start_cluster_async(cluster_id)
        if not failed(result, "queue the starts"):
            toast(f"Queued: {', '.join(result['queued']) or 'nothing (all running)'}.", kind="success")
        await self.refresh()

    async def _update_cluster(self, cluster_id: str) -> None:
        if not await message.confirm(
                "Update every map in this cluster to the latest ASA build?\n\n"
                "Every running map is saved and stopped at once (forced down if it is slow to quit), "
                "then each map that is behind is backed up and updated, one at a time. "
                "Maps are NOT started again afterwards: use Start all when it is done.",
                title="Update all", ok_text="Stop and update", danger=True):
            return
        result = await ServerApi().update_cluster_async(cluster_id)
        if not failed(result, "queue the cluster update"):
            pos = result.get("position")
            where = f" (number {pos} in the queue)" if pos and pos > 1 else ""
            toast(f"Cluster update queued{where}.", kind="success")
        await self.refresh()

    async def _cancel_queue(self) -> None:
        if not await message.confirm("Cancel every start that is still waiting its turn?",
                                     title="Cancel the queue", ok_text="Cancel them", danger=True):
            return
        result = await ServerApi().cancel_queue_async()
        if not failed(result, "cancel the queue"):
            toast(f"Cancelled {result['cancelled']}.", kind="success")
        await self.refresh()

    async def _cancel(self, job_id: int) -> None:
        job = next((j for j in self.data.get("queue", []) if j["id"] == job_id), None)
        if job and job["state"] == "running":
            if not await message.confirm(f"Stop {job['name']}'s start? If it is already running it is "
                                         "stopped again.", title="Stop this start", ok_text="Stop it",
                                         danger=True):
                return
        result = await JobsApi().cancel_async(job_id)
        if not failed(result, "cancel the job"):
            toast("Cancelled." if result.get("was") == "queued" else "Stopping the start…", kind="success")
        await self.refresh()
