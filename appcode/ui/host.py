"""Host rules: the memory budget and the start gate's timings."""
from __future__ import annotations

import js

from services.host_api import HostApi
from ui.common import Listeners, closest, esc, failed, spawn, toast

GiB = 1024 ** 3

# (key, label, unit, help, is_gib)
FIELDS = (
    ("swap_allowance", "Swap allowance", "GiB",
     "How much swap the budget may count on. ASA tolerates a little swap, not a lot.", True),
    ("reserve", "Reserve", "GiB", "Never handed to maps: the desktop, browser and this app.", True),
    ("max_running", "Maps at once", "", "A hard ceiling on top of the budget.", False),
    ("default_need", "Minimum need per map", "GiB",
     "The least a map is assumed to need, whatever it has used before.", True),
    ("need_margin", "Margin over the learned peak", "×", "A map's need is its recent peak times this.", False),
    ("settle_s", "Settle time", "s", "After a map is ready, wait this long before anything else starts.", False),
    ("ready_timeout_s", "Ready timeout", "s", "A start that is not advertising by then fails and is stopped.", False),
    ("rcon_timeout_s", "RCON timeout", "s", "After it advertises, RCON must answer within this.", False),
    ("save_timeout_s", "Save timeout", "s", "How long SaveWorld may take before stopping anyway.", False),
    ("stop_timeout_s", "Stop grace", "s", "How long ASA gets to exit before it is killed.", False),
)


class HostView:
    def __init__(self, container) -> None:
        self.el = container
        self.listeners = Listeners()

    def mount(self) -> None:
        self.el.innerHTML = '<div class="ca-page" id="ca-host"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        spawn(self.load(), "host load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        result = await HostApi().settings_async()
        if failed(result, "load the host rules"):
            return
        s = result["settings"]
        rows = []
        for key, label, unit, help_text, is_gib in FIELDS:
            value = s.get(key)
            if is_gib and value is not None:
                value = round(value / GiB, 2)
            rows.append(
                f'<label class="ca-field"><span class="ca-field-label">{esc(label)}</span>'
                f'<span class="ca-field-input"><input name="{key}" value="{esc(value)}" inputmode="decimal">'
                f'<span class="ca-unit">{esc(unit)}</span></span><span class="ca-help">{esc(help_text)}</span></label>')
        root = js.document.getElementById("ca-host")
        if root:
            root.innerHTML = (
                '<div class="ca-page-head"><h1>Host rules</h1><span class="ca-sub">One budget for every '
                "cluster on this machine. Nothing starts unless it fits.</span></div>"
                f'<div class="ca-card ca-form">{"".join(rows)}'
                '<div class="ca-actions"><button class="ca-btn ca-primary" data-act="save">Save</button></div></div>')

    def _on_click(self, event) -> None:
        if closest(event.target, '[data-act="save"]'):
            spawn(self.save(), "save host rules")

    async def save(self) -> None:
        values = {}
        for key, *_ in FIELDS:
            el = js.document.querySelector(f'#ca-host input[name="{key}"]')
            if el:
                values[key] = str(el.value).strip()
        result = await HostApi().save_settings_async(values)
        if not failed(result, "save the host rules"):
            toast("Host rules saved.", kind="success")
