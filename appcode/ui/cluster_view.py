"""
A cluster's page, in tabs:

* ``#/cluster/<id>``           Maps & basics: its maps, name, players, passwords
* ``#/cluster/<id>/settings``  Game settings: sliders, switches, presets
* ``#/cluster/<id>/mods``      Mods: in load order, with names
"""
from __future__ import annotations

import js

from services.cluster_api import ClusterApi
from ui.common import Listeners, closest, esc, failed, spawn, toast
from ui.game_settings import GameSettingsTab
from ui.mods_tab import ModsTab

GiB = 1024 ** 3

TABS = (("", "Maps & basics", "mdi-map-outline"), ("settings", "Game settings", "mdi-tune"),
        ("mods", "Mods", "mdi-puzzle-outline"))


def _val(name: str) -> str:
    el = js.document.querySelector(f'#ca-cluster [name="{name}"]')
    return str(el.value).strip() if el else ""


class ClusterView:
    def __init__(self, container, cluster_id: str, tab: str = "") -> None:
        self.el = container
        self.cluster_id = cluster_id
        self.tab = tab if tab in ("settings", "mods") else ""
        self.listeners = Listeners()
        self.child = None

    def mount(self) -> None:
        tabs = "".join(
            f'<a class="ca-tab{" ca-tab-on" if t == self.tab else ""}" href="#/cluster/{esc(self.cluster_id)}'
            f'{("/" + t) if t else ""}"><span class="mdi {icon}"></span> {esc(title)}</a>'
            for t, title, icon in TABS)
        self.el.innerHTML = (
            '<div class="ca-page" id="ca-cluster">'
            '<div class="ca-page-head"><a class="ca-back" href="#/">←</a><h1 id="ca-cluster-name">…</h1>'
            '<span class="ca-sub" id="ca-cluster-sub"></span></div>'
            f'<nav class="ca-tabs">{tabs}</nav><div id="ca-cluster-body"><div class="ca-empty">Loading…</div></div></div>')
        spawn(self._head(), "cluster head")
        body = js.document.getElementById("ca-cluster-body")
        if self.tab == "settings":
            self.child = GameSettingsTab(body, self.cluster_id)
            self.child.mount()
        elif self.tab == "mods":
            self.child = ModsTab(body, self.cluster_id)
            self.child.mount()
        else:
            self.listeners.on(self.el, "click", self._on_click)
            spawn(self.load(), "cluster load")

    def unmount(self) -> None:
        if self.child is not None:
            self.child.unmount()
        self.listeners.clear()

    async def _head(self) -> None:
        result = await ClusterApi().detail_async(self.cluster_id)
        if not result.get("ok"):
            return
        c = result["cluster"]
        name = js.document.getElementById("ca-cluster-name")
        sub = js.document.getElementById("ca-cluster-sub")
        if name:
            name.textContent = c["name"]
        if sub:
            sub.textContent = f"{len(result['servers'])} maps sharing ClusterId {c['cluster_id']}"

    async def load(self) -> None:
        result = await ClusterApi().detail_async(self.cluster_id)
        if failed(result, "load the cluster"):
            return
        c = result["cluster"]
        d = c["defaults"]
        cap = round(c["memory_cap"] / GiB, 1) if c.get("memory_cap") else ""
        maps = "".join(
            f'<a class="ca-mapcard" href="#/map/{esc(s["id"])}"><b>{esc(s["name"])}</b>'
            f'<span class="ca-sub">priority {s["priority"]} · '
            f'{"its own settings for " + esc(", ".join(k for k in s["overrides"])) if s["overrides"] else "uses the cluster settings"}'
            f"</span></a>" for s in result["servers"])
        body = js.document.getElementById("ca-cluster-body")
        if not body:
            return
        body.innerHTML = f"""
<div class="ca-two">
<section class="ca-card"><h2>Maps in this cluster</h2>
  <div class="ca-sub">Survivors, creatures and items can travel between them through obelisks and terminals.</div>
  <div class="ca-mapcards">{maps}</div></section>
<section class="ca-card ca-form">
  <h2>Basics</h2>
  <div class="ca-sub">Shared by every map. A change applies to a map the next time it starts.</div>
  <label class="ca-field"><span class="ca-field-label">Cluster name</span><span class="ca-field-input"><input name="name" value="{esc(c["name"])}"></span></label>
  <label class="ca-field"><span class="ca-field-label">Players per map</span><span class="ca-field-input"><input name="max_players" type="number" min="1" max="200" value="{esc(d.get("max_players", 70))}"></span></label>
  <label class="ca-field"><span class="ca-field-label">Join password</span><span class="ca-field-input"><input name="server_password" value="{esc(d.get("server_password", ""))}" placeholder="none: anyone can join"></span>
    <span class="ca-help">Players type it when joining. Letters, digits, _ and -.</span></label>
  <label class="ca-field"><span class="ca-field-label">Admin password</span><span class="ca-field-input"><input name="admin_password" value="{esc(d.get("admin_password", ""))}"></span>
    <span class="ca-help">For in-game admin commands. The app also uses it to save worlds and list players.</span></label>
  <details class="ca-more"><summary>More</summary>
  <label class="ca-field"><span class="ca-field-label">BattlEye</span><span class="ca-field-input"><select name="battleye">
    <option value="off"{" selected" if not d.get("battleye") else ""}>Off (players pick "Join without BattlEye")</option>
    <option value="on"{" selected" if d.get("battleye") else ""}>On</option></select></span></label>
  <label class="ca-field"><span class="ca-field-label">Launch flags</span><span class="ca-field-input"><input name="custom_args" value="{esc(" ".join(d.get("custom_args") or []))}" spellcheck="false"></span>
    <span class="ca-help">-newsaveformat and -usestore keep the migrated save working; leave them.</span></label>
  <label class="ca-field"><span class="ca-field-label">Maps at once</span><span class="ca-field-input"><input name="max_running" value="{esc(c.get("max_running") or "")}" placeholder="the host's rule"></span>
    <span class="ca-help">Can only be stricter than the host's rule.</span></label>
  <label class="ca-field"><span class="ca-field-label">Memory cap</span><span class="ca-field-input"><input name="memory_cap" value="{esc(cap)}" placeholder="none"><span class="ca-unit">GiB</span></span>
    <span class="ca-help">The most this cluster's running maps may need together.</span></label>
  </details>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-act="save">Save</button></div>
</section>
</div>"""

    def _on_click(self, event) -> None:
        if closest(event.target, '[data-act="save"]'):
            spawn(self.save(), "save cluster")

    async def save(self) -> None:
        values = {
            "name": _val("name"), "max_players": _val("max_players"),
            "battleye": _val("battleye") == "on", "custom_args": _val("custom_args"),
            "admin_password": _val("admin_password"), "server_password": _val("server_password"),
            "max_running": _val("max_running"), "memory_cap": _val("memory_cap"),
        }
        result = await ClusterApi().save_async(self.cluster_id, values)
        if failed(result, "save the cluster"):
            return
        running = result.get("restart_needed") or []
        toast("Saved." + (f" Restart {', '.join(running)} to apply." if running else ""), kind="success")
        await self.load()
        await self._head()
