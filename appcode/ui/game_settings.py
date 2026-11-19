"""
A cluster's game settings as sliders and switches (``#/cluster/<id>/settings``).

Presets on top (Official, Casual, Boosted, Solo), then one group per topic.
Every map in the cluster uses these unless its own page changes a setting.
Saved settings are written into each map's files the next time it starts.
"""
from __future__ import annotations

import js

from wapyt import FieldConfig, Form, FormConfig, FormFieldset, message

from services.ark_settings import GROUPS, ORDER, PRESETS, SETTINGS, differs, preset, wild_level
from services.settings_api import SettingsApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast
from ui.stacks_card import StacksCard


def official() -> dict:
    return {k: SETTINGS[k]["default"] for k in ORDER}


def help_text(key: str) -> str:
    s = SETTINGS[key]
    if s["kind"] == "bool":
        return s["help"] or ("Official: on." if s["default"] else "Official: off.")
    unit = "×" if s["kind"] == "mult" else ""
    hint = " Lower is faster." if s["lower_is_faster"] else ""
    extra = f" {s['help']}" if s["help"] else ""
    return f"Official: {s['default']:g}{unit}.{hint}{extra}"


def field(key: str) -> FieldConfig:
    s = SETTINGS[key]
    if s["kind"] == "bool":
        return FieldConfig(id=key, label=s["label"], type="toggle", help=help_text(key))
    span = s["max"] - s["min"]
    major = None
    for candidate in (1, 2, 5, 10, 20, 25, 50):
        if span / candidate <= 10:
            major = candidate
            break
    if span <= 2:
        major = 0.5 if span > 1 else 0.25
    return FieldConfig(id=key, label=s["label"], type="range", min=s["min"], max=s["max"], step=s["step"],
                       show_value=True, major_ticks=major, help=help_text(key))


class GameSettingsTab:
    def __init__(self, container, cluster_id: str) -> None:
        self.el = container
        self.cluster_id = cluster_id
        self.listeners = Listeners()
        self.form = None
        self.data: dict = {}
        self.dirty = False
        self.stacks = None

    def mount(self) -> None:
        self.el.innerHTML = '<div id="ca-game"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        spawn(self.load(), "game settings load")

    def unmount(self) -> None:
        if self.stacks is not None:
            self.stacks.unmount()
        self.listeners.clear()
        self.form = None

    async def load(self) -> None:
        result = await SettingsApi().cluster_async(self.cluster_id)
        if failed(result, "load the game settings"):
            return
        self.data = result
        root = js.document.getElementById("ca-game")
        if not root:
            return
        root.innerHTML = self._shell(result)
        holder = js.document.getElementById("ca-game-form")
        fieldsets = []
        for gid, title, _icon, blurb in GROUPS:
            keys = [k for k in ORDER if SETTINGS[k]["group"] == gid]
            if keys:
                fieldsets.append(FormFieldset(id=f"g-{gid}", label=f"{title} · {blurb}", columns=2,
                                              fields=[field(k) for k in keys]))
        self.form = Form(FormConfig(fields=fieldsets, submit_text="Save settings", cancel_text=None),
                         container=holder)
        self.form.set_values(result["values"])
        self.form.on_change(lambda _p: self._changed())
        self.form.on_submit(lambda values: spawn(self.save(values), "save game settings"))
        self._summary(result["values"])
        holder = js.document.getElementById("ca-stacks-holder")
        if holder:
            if self.stacks is not None:
                self.stacks.unmount()
            self.stacks = StacksCard(holder, self.cluster_id)
            self.stacks.mount()

    # -- rendering -----------------------------------------------------------

    def _shell(self, d: dict) -> str:
        cards = "".join(
            f'<button class="ca-preset" data-preset="{pid}"><b>{esc(name)}</b><span>{esc(blurb)}</span></button>'
            for pid, name, blurb, _v in PRESETS)
        intro = ""
        if not d["managed"]:
            intro = ('<div class="ca-banner" data-kind="warning"><span class="mdi mdi-information-outline"></span>'
                     "<div>The app does not manage these maps' game settings yet, so it leaves their files alone. "
                     "<b>Bring in the settings your maps use now</b> to start from what you play with today, or "
                     "pick a preset and save."
                     '<div class="ca-actions"><button class="ca-btn ca-primary" data-act="import">'
                     '<span class="mdi mdi-import"></span> Bring in current settings</button></div></div></div>')
        own = [m for m in d["maps"] if m["overrides"]]
        notes = "".join(
            f'<li><a href="#/map/{esc(m["id"])}">{esc(m["name"])}</a> changes {len(m["overrides"])} '
            f'setting{"s" if len(m["overrides"]) != 1 else ""} for itself.</li>' for m in own)
        jump = "".join(f'<a href="#" data-jump="g-{gid}"><span class="mdi {icon}"></span> {esc(title)}</a>'
                       for gid, title, icon, _b in GROUPS)
        jump += '<a href="#" data-jump="stacks"><span class="mdi mdi-package-variant-closed"></span> Stack sizes per item</a>'
        return f"""
{intro}
<section class="ca-card">
  <h2>Start from a preset</h2>
  <div class="ca-sub">A preset fills in the sliders below. Nothing changes until you save.</div>
  <div class="ca-presets">{cards}</div>
</section>
<div class="ca-game-bar"><span id="ca-game-summary" class="ca-sub"></span><nav class="ca-jump">{jump}</nav></div>
{f'<ul class="ca-list ca-sub">{notes}</ul>' if notes else ''}
<section class="ca-card ca-game-form"><div id="ca-game-form"></div></section>
<div id="ca-game-after"></div>
<div id="ca-stacks-holder"></div>"""

    def _summary(self, values: dict) -> None:
        el = js.document.getElementById("ca-game-summary")
        if not el:
            return
        n = len(differs(values))
        level = wild_level(values)
        wild = f" · wild creatures up to level {level}"
        saved = " · <b>not saved yet</b>" if self.dirty else ""
        el.innerHTML = (f"{n} setting{'s' if n != 1 else ''} differ from Official{wild}{saved}")

    def _changed(self) -> None:
        self.dirty = True
        if self.form:
            self._summary(self.form.get_values())

    # -- actions -------------------------------------------------------------

    def _on_click(self, event) -> None:
        jump = closest(event.target, "[data-jump]")
        if jump:
            event.preventDefault()
            # wapyt marks a fieldset with data-fieldset-id, not an id.
            target = (js.document.getElementById("ca-stacks") if attr(jump, "data-jump") == "stacks" else
                      js.document.querySelector(f'[data-fieldset-id="{attr(jump, "data-jump")}"]'))
            view = js.document.getElementById("ca-view")
            bar = js.document.querySelector(".ca-game-bar")
            if target and view:
                top = target.getBoundingClientRect().top - view.getBoundingClientRect().top
                offset = (bar.offsetHeight if bar else 0) + 12
                view.scrollTo(0, view.scrollTop + top - offset)
            return
        card = closest(event.target, "[data-preset]")
        if card:
            spawn(self._preset(attr(card, "data-preset")), "preset")
            return
        btn = closest(event.target, "[data-act]")
        if not btn:
            return
        act = attr(btn, "data-act")
        if act == "import":
            spawn(self._import(), "import settings")
        elif act == "restart":
            spawn(self._restart(), "restart running")

    async def _preset(self, pid: str) -> None:
        if not self.form:
            return
        name = next(n for p, n, _b, _v in PRESETS if p == pid)
        if not await message.confirm(f"Fill in the {name} preset? Your current choices on this page are replaced "
                                      "(nothing is saved until you press Save).", title="Use preset",
                                      ok_text="Fill in"):
            return
        values = {**official(), **preset(pid)}
        self.form.set_values(values)
        self.dirty = True
        self._summary(values)
        toast(f"{name} filled in. Adjust anything, then Save.", kind="info")

    async def _import(self) -> None:
        result = await SettingsApi().import_from_maps_async(self.cluster_id)
        if failed(result, "bring in the settings"):
            return
        per_map = sum(1 for n in (result.get("per_map") or {}).values() if n)
        toast(f"Brought in {result['shared']} shared settings"
              + (f"; {per_map} map(s) keep a few of their own." if per_map else "."), kind="success")
        self.dirty = False
        await self.load()

    async def save(self, values: dict) -> None:
        if self.form:
            self.form.set_busy(True)
        try:
            result = await SettingsApi().save_cluster_async(self.cluster_id, values)
        finally:
            if self.form:
                self.form.set_busy(False)
        if failed(result, "save the settings"):
            return
        self.dirty = False
        self._summary(values)
        running = result.get("restart_needed") or []
        after = js.document.getElementById("ca-game-after")
        if running and after:
            after.innerHTML = (
                f'<div class="ca-banner" data-kind="warning"><span class="mdi mdi-restart"></span><div>'
                f'Saved. {esc(", ".join(running))} {"is" if len(running) == 1 else "are"} running and will use the new '
                f'settings after a restart. <div class="ca-actions"><button class="ca-btn ca-primary" '
                f'data-act="restart">Restart {"it" if len(running) == 1 else "them"} now (one at a time)</button>'
                f"</div></div></div>")
        elif after:
            after.innerHTML = ""
        toast("Settings saved. Maps use them from their next start.", kind="success")

    async def _restart(self) -> None:
        if not await message.confirm("Save the world and restart the running maps one after another?",
                                     title="Restart maps", ok_text="Restart"):
            return
        result = await SettingsApi().restart_running_async(self.cluster_id)
        if not failed(result, "restart the maps"):
            toast(f"Restarting {', '.join(result['queued'])}.", kind="success")
            after = js.document.getElementById("ca-game-after")
            if after:
                after.innerHTML = ""
