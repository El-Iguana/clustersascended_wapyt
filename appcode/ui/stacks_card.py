"""
Per-item stack sizes (on the Game settings tab): every common item with what
a stack holds now (the global multiplier applied), and a box to set its own
size. What you type is the exact stack size. Saved settings reach each map's
Game.ini at its next start.
"""
from __future__ import annotations

import js

from services.ark_items import BY_CLASS, CATEGORIES, ITEMS, normal_stack
from services.settings_api import SettingsApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast


class StacksCard:
    def __init__(self, container, cluster_id: str) -> None:
        self.el = container
        self.cluster_id = cluster_id
        self.listeners = Listeners()
        self.data: dict = {}

    def mount(self) -> None:
        self.el.innerHTML = '<section class="ca-card" id="ca-stacks"><div class="ca-empty">Loading…</div></section>'
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.on(self.el, "input", self._on_input)
        spawn(self.load(), "stacks load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        result = await SettingsApi().stacks_async(self.cluster_id)
        if failed(result, "load the stack sizes"):
            return
        self.data = result
        box = js.document.getElementById("ca-stacks")
        if box:
            box.innerHTML = self._render(result)

    def _render(self, d: dict) -> str:
        mult = d["multiplier"]
        stacks = d["stacks"]
        groups = []
        for cat in CATEGORIES:
            rows = []
            for _c, name, item_class, default, note in (i for i in ITEMS if i[0] == cat):
                now = normal_stack(default, mult)
                own = stacks.get(item_class)
                rows.append(
                    f'<tr data-row="{esc(name.lower())}"><td><b>{esc(name)}</b>'
                    f'{f"<div class=ca-sub>{esc(note)}</div>" if note else ""}</td>'
                    f'<td>{default}</td><td>{now}</td>'
                    f'<td><input class="ca-stack-in" type="number" min="1" max="1000000" data-class="{esc(item_class)}" '
                    f'value="{own if own else ""}" placeholder="{now}"></td></tr>')
            groups.append(f'<tbody><tr class="ca-stack-cat"><th colspan="4">{esc(cat)}</th></tr>{"".join(rows)}</tbody>')
        extra = [(c, q) for c, q in stacks.items() if c not in BY_CLASS]
        extra_rows = "".join(
            f'<tr><td><code>{esc(c)}</code></td><td>–</td><td>–</td><td><input class="ca-stack-in" type="number" '
            f'min="1" data-class="{esc(c)}" value="{q}"></td></tr>' for c, q in extra)
        if extra_rows:
            groups.append(f'<tbody><tr class="ca-stack-cat"><th colspan="4">Other items</th></tr>{extra_rows}</tbody>')
        return f"""
<div class="ca-host-head"><h2><span class="mdi mdi-package-variant-closed"></span> Stack sizes per item</h2>
  <input id="ca-stack-filter" placeholder="Find an item…" class="ca-stack-filter"></div>
<div class="ca-sub">The global "Stack sizes" slider above multiplies every stack (now ×{mult:g}), except items that
  stack to 1. Type a number to give an item its own stack size: exactly that many, whatever the slider says.
  Leave it empty for the normal size. Items still weigh the same each, so big stacks are heavy.</div>
<div class="ca-table-wrap"><table class="ca-table ca-stacks-table"><thead><tr><th>Item</th><th>ARK's size</th>
  <th>Now (×{mult:g})</th><th>Its own size</th></tr></thead>{"".join(groups)}</table></div>
<details class="ca-more"><summary>An item that is not listed</summary>
  <div class="ca-modadd"><input id="ca-stack-new-class" placeholder="Item class, e.g. PrimalItemResource_Wool_C" spellcheck="false">
    <input id="ca-stack-new-qty" type="number" min="1" placeholder="Stack size">
    <button class="ca-btn" data-act="add-item">Add</button></div>
  <div class="ca-sub">The class name is on the item's ARK wiki page ("Blueprint path", ending in _C).</div></details>
<div class="ca-actions"><button class="ca-btn ca-primary" data-act="save-stacks">Save stack sizes</button>
  <span class="ca-sub" id="ca-stacks-note">{len(stacks)} item(s) have their own size.</span></div>"""

    def _on_input(self, event) -> None:
        if getattr(event.target, "id", "") == "ca-stack-filter":
            text = str(event.target.value).strip().lower()
            for row in js.document.querySelectorAll("#ca-stacks tr[data-row]"):
                row.hidden = bool(text) and text not in attr(row, "data-row")

    def _on_click(self, event) -> None:
        btn = closest(event.target, "[data-act]")
        if not btn:
            return
        act = attr(btn, "data-act")
        if act == "save-stacks":
            spawn(self.save(), "save stacks")
        elif act == "add-item":
            cls = js.document.getElementById("ca-stack-new-class")
            qty = js.document.getElementById("ca-stack-new-qty")
            item_class = str(cls.value).strip() if cls else ""
            if not item_class or not qty or not str(qty.value).strip():
                toast("Type the item's class name and a stack size.", kind="error")
                return
            stacks = dict(self.data.get("stacks") or {})
            stacks.update(self._values())
            stacks[item_class] = str(qty.value).strip()
            spawn(self._save_values(stacks), "add item")

    def _values(self) -> dict:
        out = {}
        for el in js.document.querySelectorAll("#ca-stacks .ca-stack-in"):
            out[attr(el, "data-class")] = str(el.value).strip()
        return out

    async def save(self) -> None:
        await self._save_values(self._values())

    async def _save_values(self, values: dict) -> None:
        result = await SettingsApi().save_stacks_async(self.cluster_id, values)
        if failed(result, "save the stack sizes"):
            return
        running = result.get("restart_needed") or []
        toast(f"Saved {result['count']} item size(s)."
              + (f" Restart {', '.join(running)} to use them." if running else " Maps use them at their next start."),
              kind="success")
        await self.load()
