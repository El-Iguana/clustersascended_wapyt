"""
A cluster's mods (``#/cluster/<id>/mods``): cards in load order with the
name players know them by, add by CurseForge id (or a link containing it),
reorder, remove. Every map loads these unless its own page says otherwise.
"""
from __future__ import annotations

import re

import js

from wapyt import message

from services.mods_api import ModsApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast

CURSEFORGE = "https://www.curseforge.com/projects/"


def parse_id(text: str) -> str:
    """A bare id, or the last long number in a pasted link."""
    found = re.findall(r"\d{4,10}", str(text or ""))
    return found[-1] if found else ""


class ModsTab:
    def __init__(self, container, cluster_id: str) -> None:
        self.el = container
        self.cluster_id = cluster_id
        self.listeners = Listeners()
        self.mods: list = []
        self.names: dict = {}
        self.maps: list = []
        self.dirty = False

    def mount(self) -> None:
        self.el.innerHTML = '<div id="ca-mods-tab"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.on(self.el, "change", self._on_change)
        spawn(self.load(), "mods load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        result = await ModsApi().cluster_async(self.cluster_id)
        if failed(result, "load the mods"):
            return
        self.mods = list(result["mods"])
        self.names = dict(result["names"])
        self.maps = list(result["maps"])
        self.dirty = False
        self._draw()

    def _name(self, mod_id: str) -> str:
        return self.names.get(mod_id) or ""

    def _draw(self) -> None:
        root = js.document.getElementById("ca-mods-tab")
        if not root:
            return
        cards = []
        for i, m in enumerate(self.mods):
            name = self._name(m)
            cards.append(f"""
<li class="ca-modcard" data-mod="{esc(m)}">
  <span class="ca-modnum">{i + 1}</span>
  <div class="ca-modbody">
    <input class="ca-modname" data-name-for="{esc(m)}" value="{esc(name)}" placeholder="Give it a name (e.g. Dino Depot)">
    <div class="ca-sub">CurseForge id {esc(m)} · <a href="{CURSEFORGE}{esc(m)}" target="_blank" rel="noopener">open on CurseForge</a></div>
  </div>
  <div class="ca-modbtns">
    <button class="ca-icon" data-mod-act="up" title="Load earlier">▲</button>
    <button class="ca-icon" data-mod-act="down" title="Load later">▼</button>
    <button class="ca-icon" data-mod-act="remove" title="Remove">✕</button>
  </div>
</li>""")
        empty = '<li class="ca-empty">No mods yet.</li>' if not self.mods else ""
        maps = []
        for mp in self.maps:
            if mp["mode"] == "inherit":
                what = "loads these"
            elif mp["mode"] == "append":
                extra = ", ".join(self._name(m) or m for m in mp["own"])
                what = f"loads these plus {esc(extra)}"
            else:
                what = f"loads its own list ({len(mp['own'])} mods)"
            maps.append(f'<li><a href="#/map/{esc(mp["id"])}">{esc(mp["name"])}</a> {what}.</li>')
        root.innerHTML = f"""
<section class="ca-card">
  <h2>Mods for every map</h2>
  <div class="ca-sub">ASA loads mods in this order. Players download them automatically when they join.
    A change applies to each map at its next start.</div>
  <ol class="ca-modlist">{"".join(cards)}{empty}</ol>
  <div class="ca-modadd">
    <input id="ca-mod-new-id" placeholder="CurseForge id, or paste the mod's link" spellcheck="false">
    <input id="ca-mod-new-name" placeholder="Name (optional)">
    <button class="ca-btn" data-act="add"><span class="mdi mdi-plus"></span> Add mod</button>
  </div>
  <div class="ca-sub">Find a mod's id on its CurseForge page, under "About Project": Project ID.</div>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-act="save"{"" if self.dirty else " disabled"}>Save mods</button>
    {'<span class="ca-sub"><b>not saved yet</b></span>' if self.dirty else ''}</div>
</section>
<section class="ca-card"><h2>Maps</h2><ul class="ca-list">{"".join(maps)}</ul></section>"""

    def _touch(self) -> None:
        self.dirty = True
        self._draw()

    def _on_change(self, event) -> None:
        mod_id = attr(event.target, "data-name-for")
        if mod_id:
            self.names[mod_id] = str(event.target.value).strip()
            self.dirty = True
            btn = js.document.querySelector('#ca-mods-tab [data-act="save"]')
            if btn:
                btn.disabled = False

    def _on_click(self, event) -> None:
        btn = closest(event.target, "[data-mod-act]")
        if btn:
            card = closest(btn, "[data-mod]")
            mod_id = attr(card, "data-mod")
            if mod_id in self.mods:
                i = self.mods.index(mod_id)
                act = attr(btn, "data-mod-act")
                if act == "remove":
                    spawn(self._remove(mod_id), "remove mod")
                    return
                if act == "up" and i > 0:
                    self.mods[i - 1], self.mods[i] = self.mods[i], self.mods[i - 1]
                elif act == "down" and i < len(self.mods) - 1:
                    self.mods[i + 1], self.mods[i] = self.mods[i], self.mods[i + 1]
                self._touch()
            return
        act_btn = closest(event.target, "[data-act]")
        if not act_btn or act_btn.disabled:
            return
        act = attr(act_btn, "data-act")
        if act == "add":
            self._add()
        elif act == "save":
            spawn(self.save(), "save mods")

    def _add(self) -> None:
        id_el = js.document.getElementById("ca-mod-new-id")
        name_el = js.document.getElementById("ca-mod-new-name")
        mod_id = parse_id(id_el.value if id_el else "")
        if not mod_id:
            toast("Paste the mod's CurseForge Project ID (a number) or a link that contains it.", kind="error")
            return
        if mod_id in self.mods:
            toast("That mod is already in the list.", kind="info")
            return
        self.mods.append(mod_id)
        name = str(name_el.value).strip() if name_el else ""
        if name:
            self.names[mod_id] = name
        self._touch()

    async def _remove(self, mod_id: str) -> None:
        label = self._name(mod_id) or mod_id
        if not await message.confirm(f"Remove {label} from every map that uses the cluster's mods?\n\n"
                                     "Anything players built or tamed with it may disappear from those maps.",
                                     title="Remove mod", ok_text="Remove", danger=True):
            return
        self.mods.remove(mod_id)
        self._touch()

    async def save(self) -> None:
        names = {m: self.names.get(m, "") for m in self.mods}
        result = await ModsApi().save_cluster_mods_async(self.cluster_id, self.mods, names)
        if failed(result, "save the mods"):
            return
        running = result.get("restart_needed") or []
        toast("Mods saved." + (f" Restart {', '.join(running)} to load them." if running else ""), kind="success")
        await self.load()
