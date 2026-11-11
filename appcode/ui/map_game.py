"""
A map's own game settings (the map page's "Game settings for this map"):
the few settings where this map differs from its cluster. Everything else
follows the cluster's Game settings tab.
"""
from __future__ import annotations

import js

from services.ark_settings import GROUPS, ORDER, SETTINGS
from services.settings_api import SettingsApi
from ui.common import attr, esc, failed, toast

GROUP_TITLES = {g[0]: g[1] for g in GROUPS}


def fmt(key: str, value) -> str:
    s = SETTINGS[key]
    if s["kind"] == "bool":
        return "On" if value else "Off"
    return f"{float(value):g}{'×' if s['kind'] == 'mult' else ''}"


def row(key: str, value, cluster_value) -> str:
    s = SETTINGS[key]
    if s["kind"] == "bool":
        control = (f'<label class="ca-switch"><input type="checkbox" data-own="{key}"'
                   f'{" checked" if value else ""}> <span>{"On" if value else "Off"}</span></label>')
        out = ""
    else:
        control = (f'<input type="range" data-own="{key}" min="{s["min"]}" max="{s["max"]}" step="{s["step"]}" '
                   f'value="{float(value)}">')
        out = f'<output data-out="{key}">{fmt(key, value)}</output>'
    return (f'<div class="ca-override" data-row="{key}"><div><b>{esc(s["label"])}</b>'
            f'<div class="ca-sub">{esc(GROUP_TITLES.get(s["group"], ""))} · cluster: {fmt(key, cluster_value)}</div></div>'
            f'<div>{control}</div>{out or "<span></span>"}'
            f'<button class="ca-btn" data-own-act="drop" data-key="{key}">Use cluster value</button></div>')


def html(game: dict, map_name: str) -> str:
    if not game.get("managed"):
        return (f'<section class="ca-card"><h2>Game settings for {esc(map_name)}</h2>'
                '<div class="ca-sub">The cluster\'s game settings are not managed by the app yet. '
                'Open the cluster\'s <b>Game settings</b> tab and bring them in first.</div></section>')
    own = game.get("own") or {}
    rows = "".join(row(k, own[k], game["cluster"].get(k)) for k in ORDER if k in own)
    options = "".join(f'<option value="{k}">{esc(GROUP_TITLES.get(SETTINGS[k]["group"], ""))}: '
                      f'{esc(SETTINGS[k]["label"])}</option>' for k in ORDER if k not in own)
    empty = "" if rows else '<div class="ca-sub">This map uses all of the cluster\'s settings.</div>'
    return f"""
<section class="ca-card" id="ca-own-game">
  <h2>Game settings for {esc(map_name)}</h2>
  <div class="ca-sub">Change a setting for this map only (for example, a harder map). Everything else follows the cluster.</div>
  <div id="ca-own-rows">{rows}{empty}</div>
  <div class="ca-modadd"><select id="ca-own-add"><option value="">Change a setting for this map…</option>{options}</select></div>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-own-act="save">Save map settings</button></div>
</section>"""


class MapGame:
    """State and events for the card; the map page forwards clicks and inputs."""

    def __init__(self, server_id: str) -> None:
        self.server_id = server_id
        self.cluster: dict = {}

    def set_cluster(self, values: dict) -> None:
        self.cluster = dict(values or {})

    def handle_input(self, event) -> bool:
        key = attr(event.target, "data-own")
        if not key:
            return False
        if SETTINGS[key]["kind"] == "bool":
            label = event.target.parentElement.querySelector("span")
            if label:
                label.textContent = "On" if event.target.checked else "Off"
        else:
            out = js.document.querySelector(f'[data-out="{key}"]')
            if out:
                out.textContent = fmt(key, float(event.target.value))
        return True

    def handle_change(self, event) -> bool:
        if getattr(event.target, "id", "") != "ca-own-add":
            return False
        key = str(event.target.value)
        event.target.value = ""
        rows = js.document.getElementById("ca-own-rows")
        if key and rows and not js.document.querySelector(f'[data-row="{key}"]'):
            if not rows.querySelector("[data-row]"):
                rows.innerHTML = ""
            value = self.cluster.get(key, SETTINGS[key]["default"])
            rows.insertAdjacentHTML("beforeend", row(key, value, value))
            option = js.document.querySelector(f'#ca-own-add option[value="{key}"]')
            if option:
                option.remove()
        return True

    def handle_click(self, event, closest) -> bool:
        btn = closest(event.target, "[data-own-act]")
        if not btn:
            return False
        if attr(btn, "data-own-act") == "drop":
            r = closest(btn, "[data-row]")
            if r:
                r.remove()
            return True
        return attr(btn, "data-own-act") == "save"

    def values(self) -> dict:
        out = {}
        for el in js.document.querySelectorAll("#ca-own-rows [data-own]"):
            key = attr(el, "data-own")
            out[key] = bool(el.checked) if SETTINGS[key]["kind"] == "bool" else float(el.value)
        return out

    async def save(self) -> bool:
        result = await SettingsApi().save_map_async(self.server_id, self.values())
        if failed(result, "save the map's settings"):
            return False
        toast("Saved." + (" Restart the map to use them." if result.get("restart_needed") else
                          " The map uses them from its next start."), kind="success")
        return True
