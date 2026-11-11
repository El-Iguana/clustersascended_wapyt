"""
A map's page, in tabs:

* ``#/map/<id>``           Overview: status, actions (start, stop, adopt, update, back up), runs
* ``#/map/<id>/players``   Players: online, kick, ban, messages, history
* ``#/map/<id>/console``   Console: the game log and admin commands
* ``#/map/<id>/settings``  Settings: the map's own settings, game settings and mods
* ``#/map/<id>/backups``   Backups
"""
from __future__ import annotations

import js

from wapyt import message

from services.fmt import duration, gib, phase_label
from services.server_api import ServerApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast
from ui.console_tab import ConsoleTab
from ui.players_tab import PlayersTab
from ui.map_game import MapGame, html as map_game_html
from ui.mods import handle as mods_handle, html as mods_html, read as mods_read

GiB = 1024 ** 3

TABS = (("", "Overview", "mdi-view-dashboard-outline"), ("players", "Players", "mdi-account-multiple"),
        ("console", "Console", "mdi-console"), ("settings", "Settings", "mdi-tune"),
        ("backups", "Backups", "mdi-backup-restore"))


def _clock(ts) -> str:
    return str(js.Date.new(float(ts) * 1000).toLocaleString()) if ts else "–"


def _val(name: str) -> str:
    el = js.document.querySelector(f'#ca-map [name="{name}"]')
    return str(el.value).strip() if el else ""


class ServerView:
    def __init__(self, container, server_id: str, tab: str = "") -> None:
        self.el = container
        self.server_id = server_id
        self.tab = tab if tab in {t[0] for t in TABS} else ""
        self.listeners = Listeners()
        self.data: dict = {}
        self.game = MapGame(server_id)
        self.child = None

    def mount(self) -> None:
        tabs = "".join(
            f'<a class="ca-tab{" ca-tab-on" if t == self.tab else ""}" href="#/map/{esc(self.server_id)}'
            f'{("/" + t) if t else ""}"><span class="mdi {icon}"></span> {esc(title)}</a>' for t, title, icon in TABS)
        self.el.innerHTML = (
            '<div class="ca-page" id="ca-map"><div class="ca-page-head"><a class="ca-back" href="#/">←</a>'
            '<h1 id="ca-map-name">…</h1><span class="ca-sub" id="ca-map-in"></span>'
            '<span class="ca-badge" id="ca-map-badge" data-kind="off"></span></div>'
            f'<nav class="ca-tabs">{tabs}</nav><div id="ca-map-body" class="ca-map-body">'
            '<div class="ca-empty">Loading…</div></div></div>')
        body = js.document.getElementById("ca-map-body")
        if self.tab in ("players", "console"):
            self.child = (PlayersTab if self.tab == "players" else ConsoleTab)(body, self.server_id)
            self.child.mount()
        else:
            self.listeners.on(self.el, "click", self._on_click)
            self.listeners.on(self.el, "change", self._on_change)
            self.listeners.on(self.el, "input", self._on_input)
        self.listeners.every(5000, lambda: spawn(self.refresh_status(), "map status"))
        spawn(self.load(), "map load")

    def unmount(self) -> None:
        if self.child is not None:
            self.child.unmount()
        self.listeners.clear()

    def _head(self, d: dict) -> None:
        s = d["server"]
        name = js.document.getElementById("ca-map-name")
        where = js.document.getElementById("ca-map-in")
        badge = js.document.getElementById("ca-map-badge")
        if name:
            name.textContent = s["name"]
        if where:
            where.innerHTML = f'in <a href="#/cluster/{esc(d["cluster"]["id"])}">{esc(d["cluster"]["name"])}</a>'
        if badge:
            label, kind = phase_label(d["phase"])
            badge.textContent = label
            badge.setAttribute("data-kind", kind)

    async def load(self) -> None:
        result = await ServerApi().detail_async(self.server_id)
        if failed(result, "load the map"):
            return
        self.data = result
        self.game.set_cluster(result["game"]["cluster"])
        self._head(result)
        if self.tab in ("players", "console"):
            return
        body = js.document.getElementById("ca-map-body")
        if body:
            body.innerHTML = self._render(result)

    async def refresh_status(self) -> None:
        result = await ServerApi().detail_async(self.server_id)
        if not result.get("ok"):
            return
        self.data = result
        self._head(result)
        box = js.document.getElementById("ca-map-status")
        if box:
            box.innerHTML = self._status(result)

    # -- rendering -----------------------------------------------------------

    def _render(self, d: dict) -> str:
        s = d["server"]
        if self.tab == "settings":
            return f"""{map_game_html(d["game"], s["name"])}
<div class="ca-two">{self._settings(d)}{self._mods(d)}</div>"""
        if self.tab == "backups":
            return self._backups(d)
        return f"""<section class="ca-card" id="ca-map-status">{self._status(d)}</section>
{self._runs(d)}"""

    def _status(self, d: dict) -> str:
        s, live, cont, build = d["server"], d["live"], d["container"], d["build"]
        label, kind = phase_label(d["phase"])
        notes = []
        if cont.get("known"):
            if not cont.get("exists"):
                notes.append("No container yet: the app creates it on the first start.")
            elif not cont.get("managed"):
                notes.append("Runs in the container podman-compose made. <b>Adopt</b> it to let the app own it "
                             "(no podman auto-restart; the app restarts it after a crash, through admission).")
            elif cont.get("drift"):
                notes.append("Settings changed since its container was made: they apply at the next start.")
            if cont.get("old_exists"):
                notes.append("The old compose container is kept (rollback puts it back).")
        if s.get("held"):
            notes.append(f'<span class="ca-warn">{esc(s["held"])}</span>')
        if build.get("update_available"):
            notes.append(f'<span class="ca-warn">Update available: build {esc(build["installed"])} → '
                         f'{esc(build["latest"])}.</span>')
        mem = f"{gib(live['mem']['total'])} now · peak {gib(live['peak'])}" if live.get("mem") else "not running"
        players = live.get("players")
        who = (f"{len(players)} online" + (": " + ", ".join(esc(p['name']) for p in players) if players else "")
               if players is not None else "–")
        up = duration(js.Date.now() / 1000 - live["ready_at"]) if live.get("ready_at") else "–"
        phase = d["phase"]
        b = []
        if phase in ("stopped", "absent", "unknown"):
            b.append('<button class="ca-btn ca-primary" data-act="start"><span class="mdi mdi-play"></span> Start</button>')
        if phase == "ready":
            b.append('<button class="ca-btn" data-act="restart"><span class="mdi mdi-restart"></span> Restart</button>')
        if phase in ("ready", "starting"):
            b.append('<button class="ca-btn ca-danger" data-act="stop"><span class="mdi mdi-stop"></span> Stop</button>')
        if cont.get("known") and cont.get("exists") and not cont.get("managed"):
            b.append('<button class="ca-btn" data-act="adopt"><span class="mdi mdi-hand-heart-outline"></span> Adopt</button>')
        if cont.get("old_exists"):
            b.append('<button class="ca-btn" data-act="rollback"><span class="mdi mdi-undo"></span> Roll back</button>')
        if cont.get("managed"):
            b.append('<button class="ca-btn" data-act="update"><span class="mdi mdi-update"></span> Update</button>')
        b.append('<button class="ca-btn" data-act="backup"><span class="mdi mdi-archive-arrow-down-outline"></span> Back up</button>')
        if s.get("held"):
            b.append('<button class="ca-btn" data-act="clear_hold">Clear hold</button>')
        return f"""
<div class="ca-status-head"><span class="ca-badge" data-kind="{kind}">{esc(label)}</span>
  <span class="ca-sub">In the server list as <b>{esc(s["session_name"])}</b></span>
  <details class="ca-more ca-sub"><summary>Details</summary>{esc(s["map_name"])} · port {s["game_port"]} · RCON {s["rcon_port"]}
  · container <code>{esc(s["container_name"])}</code>{" (app)" if cont.get("managed") else " (compose)" if cont.get("exists") else ""}</details></div>
<div class="ca-stats">
  <div><span class="ca-k">Needs</span> {gib(d["need"])} running · {gib(d.get("need_start"))} to start</div><div><span class="ca-k">Memory</span> {mem}</div>
  <div><span class="ca-k">Players</span> {who}</div><div><span class="ca-k">Up</span> {up}</div>
  <div><span class="ca-k">Build</span> {esc(build.get("installed") or "?")}</div>
</div>
{"".join(f'<div class="ca-note">{n}</div>' for n in notes)}
<div class="ca-actions">{"".join(b)}</div>"""

    def _settings(self, d: dict) -> str:
        s, o, cd = d["server"], d["server"]["overrides"], d["cluster"]["defaults"]
        need = round(s["need_override"] / GiB, 1) if s.get("need_override") else ""
        be = o.get("battleye")
        be_sel = "inherit" if be is None else ("on" if be else "off")

        def opt(value, label):
            return f'<option value="{value}"{" selected" if be_sel == value else ""}>{label}</option>'

        inherit_be = "on" if cd.get("battleye") else "off"
        args = o.get("custom_args")
        return f"""
<section class="ca-card ca-form">
  <h2>Map settings</h2>
  <div class="ca-sub">Empty fields use the cluster's value. Changes apply at the next start.</div>
  <label class="ca-field"><span class="ca-field-label">Name</span><span class="ca-field-input"><input name="name" value="{esc(s["name"])}"></span></label>
  <label class="ca-field"><span class="ca-field-label">Session name</span><span class="ca-field-input"><input name="session_name" value="{esc(s["session_name"])}"></span>
    <span class="ca-help">What players see in the server list.</span></label>
  <label class="ca-field"><span class="ca-field-label">MAP_NAME</span><span class="ca-field-input"><input name="map_name" value="{esc(s["map_name"])}" spellcheck="false"></span></label>
  <label class="ca-field"><span class="ca-field-label">Game / RCON port</span><span class="ca-field-input">
    <input name="game_port" value="{s["game_port"]}"><input name="rcon_port" value="{s["rcon_port"]}"></span></label>
  <label class="ca-field"><span class="ca-field-label">Priority</span><span class="ca-field-input"><input name="priority" value="{s["priority"]}"></span>
    <span class="ca-help">1 is the most important. The least important map is stopped first to make room.</span></label>
  <label class="ca-field"><span class="ca-field-label">Autostart</span><span class="ca-field-input"><select name="autostart">
    <option value="on"{" selected" if s.get("autostart") else ""}>On: start after a reboot, restart after a crash</option>
    <option value="off"{"" if s.get("autostart") else " selected"}>Off</option></select></span></label>
  <label class="ca-field"><span class="ca-field-label">Memory need</span><span class="ca-field-input"><input name="need_override" value="{esc(need)}" placeholder="learned: {gib(d["need"])}"><span class="ca-unit">GiB</span></span>
    <span class="ca-help">Leave empty to use what it has needed recently.</span></label>
  <label class="ca-field"><span class="ca-field-label">Max players</span><span class="ca-field-input"><input name="max_players" value="{esc(o.get("max_players", ""))}" placeholder="cluster: {esc(cd.get("max_players", 70))}"></span></label>
  <label class="ca-field"><span class="ca-field-label">BattlEye</span><span class="ca-field-input"><select name="battleye">
    {opt("inherit", f"Cluster ({inherit_be})")}{opt("off", "Off")}{opt("on", "On")}</select></span></label>
  <label class="ca-field"><span class="ca-field-label">Launch flags</span><span class="ca-field-input"><input name="custom_args" value="{esc(" ".join(args) if args is not None else "")}" placeholder="cluster: {esc(" ".join(cd.get("custom_args") or []))}" spellcheck="false"></span></label>
  <label class="ca-field"><span class="ca-field-label">Admin password</span><span class="ca-field-input"><input name="admin_password" value="{esc(o.get("admin_password", ""))}" placeholder="cluster's"></span></label>
  <label class="ca-field"><span class="ca-field-label">Join password</span><span class="ca-field-input"><input name="server_password" value="{esc(o.get("server_password", ""))}" placeholder="cluster's"></span></label>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-act="save">Save</button></div>
</section>"""

    def _mods(self, d: dict) -> str:
        o, cd = d["server"]["overrides"], d["cluster"]["defaults"]
        mode = o.get("mods_mode") or "inherit"
        own = o.get("mod_ids") or []
        effective = d["effective"].get("mod_ids") or []

        def opt(value, label):
            return f'<option value="{value}"{" selected" if mode == value else ""}>{label}</option>'

        return f"""
<section class="ca-card ca-form">
  <h2>Mods</h2>
  <label class="ca-field"><span class="ca-field-label">This map</span><span class="ca-field-input"><select name="mods_mode">
    {opt("inherit", f"Use the cluster's ({len(cd.get('mod_ids') or [])})")}{opt("append", "Cluster's, plus these")}
    {opt("replace", "Only these, in this order")}</select></span></label>
  <div id="ca-own-mods"{" hidden" if mode == "inherit" else ""}>{mods_html("map", own if mode != "inherit" else list(effective), names=d.get("mod_names"))}</div>
  <div class="ca-sub">Loads: {esc(", ".join(d.get("mod_names", {}).get(m) or m for m in effective)) or "no mods"}</div>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-act="save">Save</button></div>
</section>"""

    def _backups(self, d: dict) -> str:
        rows = "".join(
            f"<tr><td>{_clock(b['created'])}</td><td>{esc(b['kind'])}</td><td>{b['size'] / GiB:.2f} GiB</td>"
            f"<td>{esc(b.get('build_id') or '')}</td><td class='ca-summary'><code>{esc(b['path'])}</code></td></tr>"
            for b in d["backups"]) or '<tr><td colspan="5" class="ca-empty">No backups yet.</td></tr>'
        link = (f'<a class="ca-pad ca-sub" href="#/cluster/{esc(d["cluster"]["id"])}/backups">'
                f'Restore, keep or delete backups, and set the nightly backup →</a>')
        return (f'<section class="ca-card ca-table-wrap"><h2 class="ca-pad">Backups</h2>{link}<table class="ca-table">'
                f"<thead><tr><th>When</th><th>Kind</th><th>Size</th><th>Build</th><th>File</th></tr></thead>"
                f"<tbody>{rows}</tbody></table></section>")

    def _runs(self, d: dict) -> str:
        rows = []
        for r in d["runs"]:
            took = (r["ready_at"] - r["started_at"]) if r.get("ready_at") else None
            rows.append(f"<tr><td>{_clock(r['started_at'])}</td><td>{duration(took) if took else '–'}</td>"
                        f"<td>{gib(r.get('ready_mem'))}</td><td>{gib(r.get('peak_total'))}</td>"
                        f"<td>{esc(r.get('end_reason') or ('running' if not r.get('stopped_at') else ''))}</td>"
                        f"<td>{esc(r.get('source'))}</td></tr>")
        body = "".join(rows) or '<tr><td colspan="6" class="ca-empty">No runs recorded yet.</td></tr>'
        return (f'<section class="ca-card ca-table-wrap"><h2 class="ca-pad">Recent runs</h2><table class="ca-table">'
                f"<thead><tr><th>Started</th><th>Ready after</th><th>At ready</th><th>Peak</th><th>Ended</th>"
                f"<th>By</th></tr></thead><tbody>{body}</tbody></table></section>")

    # -- events --------------------------------------------------------------

    def _on_input(self, event) -> None:
        self.game.handle_input(event)

    def _on_change(self, event) -> None:
        if self.game.handle_change(event):
            return
        if attr(event.target, "name") == "mods_mode":
            box = js.document.getElementById("ca-own-mods")
            if box:
                box.hidden = str(event.target.value) == "inherit"

    def _on_click(self, event) -> None:
        if mods_handle(event, "map"):
            return
        if self.game.handle_click(event, closest):
            own = closest(event.target, '[data-own-act="save"]')
            if own:
                spawn(self._save_game(), "save map game")
            return
        btn = closest(event.target, "[data-act]")
        if not btn or btn.disabled:
            return
        act = attr(btn, "data-act")
        if act == "save":
            spawn(self.save(), "save map")
        elif act == "clear_hold":
            spawn(self._clear_hold(), "clear hold")
        else:
            spawn(self._act(act), act)

    async def _act(self, act: str) -> None:
        name = self.data.get("server", {}).get("name", "this map")
        params = {}
        confirms = {
            "stop": (f"Save the world and stop {name}?", "Save and stop", True),
            "restart": (f"Save the world and restart {name}?", "Restart", False),
            "adopt": (f"Let the app own {name}'s container?\n\nIf it runs, it is saved and stopped, the compose "
                      "container is kept aside (rollback puts it back), and the app's container starts.",
                      "Adopt", False),
            "rollback": (f"Put {name}'s compose container back?\n\nThe app's container is removed (saves stay).",
                         "Roll back", True),
            "update": (f"Update {name} to the latest ASA build?\n\nIt is backed up, booted once to download the "
                       "update, and brought back if it was running.", "Update", False),
        }
        if act in confirms:
            text, ok, danger = confirms[act]
            if not await message.confirm(text, title=act.capitalize(), ok_text=ok, danger=danger):
                return
        if act == "update":
            build = self.data.get("build") or {}
            params = {"force": not build.get("update_available"), "make_room": True}
        result = await ServerApi().act_async(self.server_id, act, params)
        if result.get("ok"):
            job = result.get("job") or {}
            toast(f"{name}: {act} queued (job {job.get('id', '')}).", kind="success")
        else:
            failed(result, f"{act} {name}")
        await self.refresh_status()

    async def save(self) -> None:
        battleye = _val("battleye")
        mode = _val("mods_mode")
        args = _val("custom_args")
        values = {
            "name": _val("name"), "session_name": _val("session_name"), "map_name": _val("map_name"),
            "game_port": _val("game_port"), "rcon_port": _val("rcon_port"), "priority": _val("priority"),
            "autostart": _val("autostart") == "on", "need_override": _val("need_override"),
            "max_players": _val("max_players"),
            "battleye": "inherit" if battleye == "inherit" else battleye,
            "custom_args": args if args else None,
            "admin_password": _val("admin_password"), "server_password": _val("server_password"),
            "mods_mode": mode, "mod_ids": mods_read("map") if mode != "inherit" else [],
        }
        result = await ServerApi().save_async(self.server_id, values)
        if failed(result, "save the map"):
            return
        toast("Saved." + (" Restart it to apply." if result.get("restart_needed") else ""), kind="success")
        await self.load()

    async def _save_game(self) -> None:
        if await self.game.save():
            await self.load()

    async def _clear_hold(self) -> None:
        result = await ServerApi().clear_hold_async(self.server_id)
        if not failed(result, "clear the hold"):
            toast("Hold cleared.", kind="success")
        await self.refresh_status()
