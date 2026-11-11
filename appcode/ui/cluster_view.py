"""
A cluster's page (``#/cluster/<id>``): the settings every map in it starts
from. Maps override what differs on their own pages.
"""
from __future__ import annotations

import js

from services.cluster_api import ClusterApi
from ui.mods import handle as mods_handle, html as mods_html, read as mods_read
from ui.common import Listeners, closest, esc, failed, spawn, toast

GiB = 1024 ** 3


def _val(name: str) -> str:
    el = js.document.querySelector(f'#ca-cluster [name="{name}"]')
    return str(el.value).strip() if el else ""


class ClusterView:
    def __init__(self, container, cluster_id: str) -> None:
        self.el = container
        self.cluster_id = cluster_id
        self.listeners = Listeners()

    def mount(self) -> None:
        self.el.innerHTML = '<div class="ca-page" id="ca-cluster"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        spawn(self.load(), "cluster load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        result = await ClusterApi().detail_async(self.cluster_id)
        if failed(result, "load the cluster"):
            return
        c = result["cluster"]
        d = c["defaults"]
        cap = round(c["memory_cap"] / GiB, 1) if c.get("memory_cap") else ""
        overrides = "".join(
            f'<li><a href="#/map/{esc(s["id"])}">{esc(s["name"])}</a>: '
            f'{esc(", ".join(k for k in s["overrides"]) or "uses the cluster settings")}</li>'
            for s in result["servers"])
        root = js.document.getElementById("ca-cluster")
        if not root:
            return
        root.innerHTML = f"""
<div class="ca-page-head"><a class="ca-back" href="#/">←</a><h1>{esc(c["name"])}</h1>
  <span class="ca-sub">ClusterId <code>{esc(c["cluster_id"])}</code> · transfer folder <code>{esc(c["transfer_dir"])}</code></span></div>
<div class="ca-two">
<section class="ca-card ca-form">
  <h2>Shared settings</h2>
  <div class="ca-sub">Every map in the cluster starts from these. A change applies to a map the next time it starts.</div>
  <label class="ca-field"><span class="ca-field-label">Name</span><span class="ca-field-input"><input name="name" value="{esc(c["name"])}"></span></label>
  <label class="ca-field"><span class="ca-field-label">Max players</span><span class="ca-field-input"><input name="max_players" value="{esc(d.get("max_players", 70))}"></span></label>
  <label class="ca-field"><span class="ca-field-label">BattlEye</span><span class="ca-field-input"><select name="battleye">
    <option value="off"{" selected" if not d.get("battleye") else ""}>Off (players join with "No BattlEye")</option>
    <option value="on"{" selected" if d.get("battleye") else ""}>On</option></select></span></label>
  <label class="ca-field"><span class="ca-field-label">Launch flags</span><span class="ca-field-input"><input name="custom_args" value="{esc(" ".join(d.get("custom_args") or []))}" spellcheck="false"></span>
    <span class="ca-help">-newsaveformat and -usestore keep the migrated save working; leave them.</span></label>
  <label class="ca-field"><span class="ca-field-label">Admin password</span><span class="ca-field-input"><input name="admin_password" value="{esc(d.get("admin_password", ""))}"></span>
    <span class="ca-help">Also the RCON password. Letters, digits, _ and -.</span></label>
  <label class="ca-field"><span class="ca-field-label">Join password</span><span class="ca-field-input"><input name="server_password" value="{esc(d.get("server_password", ""))}" placeholder="none: open to join"></span></label>
  <label class="ca-field"><span class="ca-field-label">Maps at once</span><span class="ca-field-input"><input name="max_running" value="{esc(c.get("max_running") or "")}" placeholder="host rule"></span>
    <span class="ca-help">Only stricter than the host's rule.</span></label>
  <label class="ca-field"><span class="ca-field-label">Memory cap</span><span class="ca-field-input"><input name="memory_cap" value="{esc(cap)}" placeholder="none"><span class="ca-unit">GiB</span></span>
    <span class="ca-help">The most this cluster's running maps may need together.</span></label>
  <div class="ca-field ca-field-top"><span class="ca-field-label">Mods</span><div>{mods_html("cluster", d.get("mod_ids") or [], "In load order. Maps can add their own or replace the list.")}</div></div>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-act="save">Save</button></div>
</section>
<section class="ca-card"><h2>Maps</h2><ul class="ca-list">{overrides}</ul></section>
</div>"""

    def _on_click(self, event) -> None:
        if mods_handle(event, "cluster"):
            return
        if closest(event.target, '[data-act="save"]'):
            spawn(self.save(), "save cluster")

    async def save(self) -> None:
        values = {
            "name": _val("name"), "max_players": _val("max_players"),
            "battleye": _val("battleye") == "on", "custom_args": _val("custom_args"),
            "admin_password": _val("admin_password"), "server_password": _val("server_password"),
            "max_running": _val("max_running"), "memory_cap": _val("memory_cap"),
            "mod_ids": mods_read("cluster"),
        }
        result = await ClusterApi().save_async(self.cluster_id, values)
        if failed(result, "save the cluster"):
            return
        running = result.get("restart_needed") or []
        toast("Saved." + (f" Restart {', '.join(running)} to apply." if running else ""), kind="success")
        await self.load()
