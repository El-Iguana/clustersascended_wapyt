"""
A new cluster (``#/new-cluster``): a name is enough. The rest is picked for
the player and sits behind "Details"; the next step is adding its first map.
Like adding a map, it needs every map on this machine stopped first.
"""
from __future__ import annotations

import js

from services.cluster_api import ClusterApi
from ui.common import Listeners, closest, esc, failed, spawn, toast
from ui.stop_first import banner, stop_all

READY = "Every map is stopped: ready to create a cluster."


class NewClusterView:
    def __init__(self, container) -> None:
        self.el = container
        self.listeners = Listeners()
        self.why = "…"

    def mount(self) -> None:
        self.el.innerHTML = '<div class="ca-page" id="ca-new-cluster"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.every(4000, lambda: spawn(self._refresh_busy(), "new cluster busy"))
        spawn(self.load(), "new cluster load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        d = await ClusterApi().new_cluster_info_async()
        if failed(d, "open the form"):
            return
        status = await ClusterApi().stop_first_async()
        self.why = status.get("why", "")
        root = js.document.getElementById("ca-new-cluster")
        if not root:
            return
        root.innerHTML = f"""
<div class="ca-page-head"><a class="ca-back" href="#/">←</a><h1>New cluster</h1>
  <span class="ca-sub">A separate group of maps. Players travel between maps in a cluster, never to another cluster.</span></div>
<section class="ca-card" id="ca-new-busy">{banner(status, READY)}</section>
<section class="ca-card ca-form">
  <label class="ca-field"><span class="ca-field-label">Cluster name</span><span class="ca-field-input"><input name="name" maxlength="40" placeholder="e.g. Friends PvE"></span></label>
  <label class="ca-field"><span class="ca-field-label">Players per map</span><span class="ca-field-input"><input name="max_players" type="number" min="1" max="200" value="{esc(d["max_players"])}"></span></label>
  <label class="ca-field"><span class="ca-field-label">Join password</span><span class="ca-field-input"><input name="server_password" placeholder="none: anyone can join"></span>
    <span class="ca-help">Players type it when joining. Letters, digits, _ and -.</span></label>
  <details class="ca-more"><summary>Details</summary>
  <label class="ca-field"><span class="ca-field-label">Admin password</span><span class="ca-field-input"><input name="admin_password" placeholder="picked for you"></span>
    <span class="ca-help">For in-game admin commands. Type your own to know it; you can change it later on the cluster's page.</span></label>
  <label class="ca-field"><span class="ca-field-label">ClusterId</span><span class="ca-field-input"><input name="cluster_id" placeholder="made from the name" spellcheck="false"></span></label>
  <label class="ca-field"><span class="ca-field-label">Folder</span><span class="ca-field-input"><input name="root_dir" placeholder="{esc(d["base_dir"])}/&lt;cluster name&gt;" spellcheck="false"></span>
    <span class="ca-help">Its maps, worlds and backups go here. Each map needs about 14 GiB.</span></label>
  </details>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-act="create"{" disabled" if self.why else ""}><span class="mdi mdi-plus"></span> Create and add a map</button>
    <span class="ca-sub" id="ca-new-why">{"Stop every map first." if self.why else ""}</span></div>
</section>"""

    async def _refresh_busy(self) -> None:
        status = await ClusterApi().stop_first_async()
        if not status.get("ok"):
            return
        self.why = status["why"]
        box = js.document.getElementById("ca-new-busy")
        if box:
            box.innerHTML = banner(status, READY)
        btn = js.document.querySelector('#ca-new-cluster [data-act="create"]')
        if btn:
            btn.disabled = bool(self.why)
        why = js.document.getElementById("ca-new-why")
        if why:
            why.textContent = "Stop every map first." if self.why else ""

    @staticmethod
    def _val(name: str) -> str:
        el = js.document.querySelector(f'#ca-new-cluster [name="{name}"]')
        return str(el.value).strip() if el else ""

    def _on_click(self, event) -> None:
        btn = closest(event.target, "[data-act]")
        if not btn or btn.disabled:
            return
        act = btn.getAttribute("data-act")
        if act == "create":
            spawn(self.create(), "new cluster")
        elif act == "stop-all":
            spawn(stop_all(), "stop all")

    async def create(self) -> None:
        values = {k: self._val(k) for k in ("name", "max_players", "server_password", "admin_password",
                                            "cluster_id", "root_dir")}
        result = await ClusterApi().create_async(values)
        if failed(result, "create the cluster"):
            return
        toast(f"{result['name']} is ready. Now add its first map.", kind="success")
        js.window.location.hash = f"#/cluster/{result['cluster']}/add"
