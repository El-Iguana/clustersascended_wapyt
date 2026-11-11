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
        parts.append(self._memory(d))
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

    def _cluster(self, c: dict, colours: dict, host: dict) -> str:
        limits = []
        if c.get("max_running"):
            limits.append(f"at most {c['max_running']} running")
        if c.get("memory_cap"):
            limits.append(f"capped at {gib(c['memory_cap'])}")
        mods = len((c.get("defaults") or {}).get("mod_ids") or [])
        cards = "".join(self._server(s, colours.get(s["id"], "#64748b")) for s in c["servers"])
        return f"""
<section class="ca-cluster">
  <div class="ca-cluster-head">
    <div>
      <h2><span class="mdi mdi-hexagon-multiple-outline"></span> {esc(c["name"])}</h2>
      <div class="ca-sub">ClusterId <code>{esc(c["cluster_id"])}</code> · transfer folder <code>{esc(c["transfer_dir"])}</code>
        · {mods} shared mods{(' · ' + ', '.join(limits)) if limits else ''}</div>
    </div>
    <div class="ca-sub">{len(c["servers"])} maps · committed {gib(c["committed"])}</div>
  </div>
  <div class="ca-grid">{cards}</div>
</section>"""

    def _server(self, s: dict, colour: str) -> str:
        label, kind = phase_label(s["phase"])
        job = s.get("job")
        if job:
            label, kind = (f"{job['kind'].replace('_', ' ')} queued" if job["state"] == "queued"
                           else label), "warn"
        mem = (f"{gib(s['actual'])} now · peak {gib(s['peak'])}" if s.get("actual")
               else "not running")
        players = s.get("players")
        who = ""
        if s["phase"] == "ready":
            if players is None:
                who = '<span class="ca-sub">players: –</span>'
            else:
                names = ", ".join(esc(p["name"]) for p in players[:6])
                who = (f'<span class="ca-players"><span class="mdi mdi-account-multiple"></span> '
                       f'{len(players)}{": " + names if names else ""}</span>')
        uptime = ""
        if s.get("ready_at"):
            uptime = f" · up {duration(js.Date.now() / 1000 - s['ready_at'])}"
        elif s["phase"] == "starting" and s.get("started_at"):
            uptime = f" · loading {duration(js.Date.now() / 1000 - s['started_at'])}"
        if s.get("hang"):
            uptime += ' · <span class="ca-warn">hang detected</span>'

        preview = ""
        p = s.get("preview")
        if p and not p["ok"]:
            preview = (f'<div class="ca-refusal"><span class="mdi mdi-memory"></span> {esc(p["reason"])}'
                       f'<details><summary>Why</summary>{"".join(f"<div>{esc(x)}</div>" for x in p["lines"])}'
                       f"</details></div>")
        elif p and p["ok"]:
            preview = f'<div class="ca-fits"><span class="mdi mdi-check"></span> Fits: needs {gib(s["need"])}</div>'

        buttons = []
        phase = s["phase"]
        sid = esc(s["id"])
        if job and job["state"] == "queued":
            buttons.append(f'<button class="ca-btn" data-act="cancel" data-job="{job["id"]}">Cancel</button>')
        elif phase in ("stopped", "absent", "unknown") and not job:
            if p and not p["ok"] and p.get("victims"):
                buttons.append(f'<button class="ca-btn ca-primary" data-act="make_room_start" data-id="{sid}">'
                               f'<span class="mdi mdi-swap-horizontal"></span> Make room and start</button>')
            buttons.append(f'<button class="ca-btn{" ca-primary" if not p or p["ok"] else ""}" data-act="start" '
                           f'data-id="{sid}"{" disabled" if p and not p["ok"] else ""}>'
                           f'<span class="mdi mdi-play"></span> Start</button>')
        if phase in ("ready", "starting") and not (job and job["kind"] == "stop"):
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
      <h3>{esc(s["name"])}</h3>
      <div class="ca-sub">{esc(s["map"])} · {esc(s["session"])} · port {s["game_port"]} · RCON {s["rcon_port"]} · priority {s["priority"]}</div>
    </div>
    <span class="ca-badge" data-kind="{kind}">{esc(label)}</span>
  </header>
  <div class="ca-stats">
    <div><span class="ca-k">Needs</span> {gib(s["need"])}</div>
    <div><span class="ca-k">Memory</span> {mem}</div>
  </div>
  <div class="ca-sub">{esc(s["container"])}{uptime}</div>
  {who}
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
            toast(f"{name}: {act.replace('_', ' ')} queued.", kind="success")
        else:
            failed(result, f"{act} {name}")
        await self.refresh()

    async def _cancel(self, job_id: int) -> None:
        result = await JobsApi().cancel_async(job_id)
        if not failed(result, "cancel the job"):
            toast("Cancelled.", kind="success")
        await self.refresh()
