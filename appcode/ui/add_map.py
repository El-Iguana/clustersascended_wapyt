"""
The add-a-map wizard (``#/cluster/<id>/add``). Only runs with every map on
this machine stopped; it offers to stop them (saving first).
"""
from __future__ import annotations

import js

from services.ark_maps import MAPS
from services.cluster_api import ClusterApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast
from ui.stop_first import banner, stop_all

READY = "Every map is stopped: ready to add one."

GiB = 1024 ** 3


class AddMapView:
    def __init__(self, container, cluster_id: str) -> None:
        self.el = container
        self.cluster_id = cluster_id
        self.listeners = Listeners()
        self.info: dict = {}
        self.choice = ""

    def mount(self) -> None:
        self.el.innerHTML = '<div id="ca-add"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.on(self.el, "input", self._on_input)
        self.listeners.every(4000, lambda: spawn(self._refresh_busy(), "add map busy"))
        spawn(self.load(), "add map load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        result = await ClusterApi().add_map_info_async(self.cluster_id)
        if failed(result, "open the wizard"):
            return
        self.info = result
        root = js.document.getElementById("ca-add")
        if root:
            root.innerHTML = self._render(result)
        self._check()

    async def _refresh_busy(self) -> None:
        result = await ClusterApi().stop_first_async()
        if result.get("ok"):
            self.info["why"] = result["why"]
            box = js.document.getElementById("ca-add-busy")
            if box:
                box.innerHTML = banner(result, READY)
            self._check()

    def _render(self, d: dict) -> str:
        taken = set(d["taken_maps"])
        cards = "".join(
            f'<button class="ca-mapchoice" data-map="{esc(m)}"{" disabled" if m in taken else ""}>'
            f'<b>{esc(name)}</b><span>{"Already in this cluster" if m in taken else esc(desc)}</span></button>'
            for m, name, _f, desc in MAPS)
        sources = "".join(
            f'<label class="ca-radio"><input type="radio" name="source" value="{esc(s["id"])}"'
            f'{" checked" if i == 0 else ""}> Copy from <b>{esc(s["name"])}</b>'
            f'{" (build " + esc(s["build"]) + ")" if s.get("build") else ""}: fast, nothing to download</label>'
            for i, s in enumerate(d["sources"]))
        sources += ('<label class="ca-radio"><input type="radio" name="source" value="download"'
                    f'{" checked" if not d["sources"] else ""}> Download a fresh copy with SteamCMD '
                    '(about 13 GiB; slower)</label>')
        return f"""
<section class="ca-card" id="ca-add-busy">{banner(d, READY)}</section>
<section class="ca-card"><h2>1. Pick a map</h2>
  <div class="ca-mapchoices">{cards}</div>
  <label class="ca-field"><span class="ca-field-label">Or a mod map</span><span class="ca-field-input">
    <input name="custom_map" placeholder="MAP_NAME, e.g. Amissa_WP (add its mod on the Mods tab too)" spellcheck="false"></span></label>
</section>
<section class="ca-card ca-form"><h2>2. Name it</h2>
  <label class="ca-field"><span class="ca-field-label">Name in this app</span><span class="ca-field-input"><input name="name" placeholder="e.g. The Island"></span></label>
  <label class="ca-field"><span class="ca-field-label">In the server list as</span><span class="ca-field-input"><input name="session" value="{esc(d["session"])}"></span>
    <span class="ca-help">What players see when they look for the server.</span></label>
  <details class="ca-more"><summary>Ports (picked for you)</summary>
    <label class="ca-field"><span class="ca-field-label">Game / RCON port</span><span class="ca-field-input">
      <input name="game_port" value="{d["game_port"]}"><input name="rcon_port" value="{d["rcon_port"]}"></span>
      <span class="ca-help">Free on this machine and not used by any map. Players connecting from outside your
        home need the game port forwarded on your router (UDP).</span></label>
  </details>
</section>
<section class="ca-card ca-form"><h2>3. Game files</h2>
  <div class="ca-sub">Each map has its own copy of ASA (about 14 GiB). {(d["disk_free"] / GiB):.0f} GiB free on the disk.</div>
  <div class="ca-radios">{sources}</div>
</section>
<section class="ca-card ca-form"><h2>4. Add it</h2>
  <div class="ca-sub">It joins <b>{esc(d["cluster"]["name"])}</b> with the cluster's game settings, mods and
    travel between maps, comes last in priority ({d["priority"]}), and does not start on its own after a reboot
    (change both on its page).</div>
  <label class="ca-radio"><input type="checkbox" name="start_after"> Start it when it is ready</label>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-act="create" disabled><span class="mdi mdi-plus"></span>
    Add the map</button><span class="ca-sub" id="ca-add-why"></span></div>
</section>"""

    # -- events --------------------------------------------------------------

    def _val(self, name: str) -> str:
        el = js.document.querySelector(f'#ca-add [name="{name}"]')
        return str(el.value).strip() if el else ""

    def _map_name(self) -> str:
        return self._val("custom_map") or self.choice

    def _check(self) -> None:
        btn = js.document.querySelector('#ca-add [data-act="create"]')
        why = js.document.getElementById("ca-add-why")
        reason = ""
        if self.info.get("why"):
            reason = "Stop every map first."
        elif not self._map_name():
            reason = "Pick a map."
        elif not self._val("session"):
            reason = "Name it for the server list."
        if btn:
            btn.disabled = bool(reason)
        if why:
            why.textContent = reason

    def _on_input(self, _event) -> None:
        self._check()

    def _on_click(self, event) -> None:
        card = closest(event.target, "[data-map]")
        if card and not card.disabled:
            self.choice = attr(card, "data-map")
            for el in js.document.querySelectorAll("#ca-add [data-map]"):
                el.classList.toggle("ca-picked", attr(el, "data-map") == self.choice)
            name = js.document.querySelector('#ca-add [name="name"]')
            label = next((n for m, n, _f, _d in MAPS if m == self.choice), "")
            if name and not str(name.value).strip():
                name.value = label
            custom = js.document.querySelector('#ca-add [name="custom_map"]')
            if custom:
                custom.value = ""
            self._check()
            return
        btn = closest(event.target, "[data-act]")
        if not btn or btn.disabled:
            return
        act = attr(btn, "data-act")
        if act == "stop-all":
            spawn(stop_all(), "stop all")
        elif act == "create":
            spawn(self._create(), "add map")

    async def _create(self) -> None:
        source = js.document.querySelector('#ca-add input[name="source"]:checked')
        start_after = js.document.querySelector('#ca-add input[name="start_after"]')
        values = {
            "map_name": self._map_name(), "name": self._val("name"), "session": self._val("session"),
            "game_port": self._val("game_port"), "rcon_port": self._val("rcon_port"),
            "source": str(source.value) if source else "download",
            "start_after": bool(start_after and start_after.checked),
        }
        result = await ClusterApi().add_map_async(self.cluster_id, values)
        if failed(result, "add the map"):
            return
        toast(f"Adding {result['plan']['name']}. Follow it in the start queue.", kind="success")
        js.window.location.hash = "#/"
