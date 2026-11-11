"""
An ordered mod list editor: ASA loads mods in order, so they can be moved
up and down, removed, and added (one id or several, comma or space separated).

Use ``html(prefix, mods)`` to draw it inside a container that already has a
click listener, ``handle(event, prefix)`` from that listener, and
``read(prefix)`` to get the list back.
"""
from __future__ import annotations

import js

from ui.common import attr, closest, esc


def html(prefix: str, mods: list, note: str = "", names: dict | None = None) -> str:
    names = names or {}
    _names[prefix] = names
    rows = "".join(
        f'<li data-mod="{esc(m)}"><span class="ca-mod-n">{i}.</span>'
        f'{"<b>" + esc(names[m]) + "</b> " if names.get(m) else ""}<code>{esc(m)}</code>'
        f'<span class="ca-mod-btns">'
        f'<button class="ca-icon" data-mods="{prefix}" data-mod-act="up" title="Move up">▲</button>'
        f'<button class="ca-icon" data-mods="{prefix}" data-mod-act="down" title="Move down">▼</button>'
        f'<button class="ca-icon" data-mods="{prefix}" data-mod-act="remove" title="Remove">✕</button>'
        f"</span></li>"
        for i, m in enumerate(mods, start=1))
    empty = '<li class="ca-sub ca-mod-empty">No mods.</li>' if not mods else ""
    return (f'<div class="ca-mods" id="ca-mods-{prefix}">'
            f'<ol class="ca-mod-list">{rows}{empty}</ol>'
            f'<div class="ca-mod-add"><input placeholder="Mod id(s), e.g. 928548, 950970" '
            f'data-mods-input="{prefix}"><button class="ca-btn" data-mods="{prefix}" data-mod-act="add">Add</button></div>'
            f'{f"<div class=ca-sub>{esc(note)}</div>" if note else ""}</div>')


_names: dict = {}


def read(prefix: str) -> list:
    box = js.document.getElementById(f"ca-mods-{prefix}")
    if not box:
        return []
    return [attr(li, "data-mod") for li in box.querySelectorAll("li[data-mod]")]


def _redraw(prefix: str, mods: list) -> None:
    box = js.document.getElementById(f"ca-mods-{prefix}")
    if box:
        box.outerHTML = html(prefix, mods, names=_names.get(prefix))


def handle(event, prefix: str) -> bool:
    """Handle a click on this editor's buttons. True if it was one."""
    btn = closest(event.target, f'[data-mods="{prefix}"]')
    if not btn:
        return False
    act = attr(btn, "data-mod-act")
    mods = read(prefix)
    if act == "add":
        field = js.document.querySelector(f'[data-mods-input="{prefix}"]')
        text = str(field.value if field else "")
        for part in text.replace(",", " ").split():
            if part.isdigit() and part not in mods:
                mods.append(part)
        _redraw(prefix, mods)
        return True
    li = closest(btn, "li[data-mod]")
    mod = attr(li, "data-mod")
    if mod in mods:
        i = mods.index(mod)
        if act == "remove":
            mods.pop(i)
        elif act == "up" and i > 0:
            mods[i - 1], mods[i] = mods[i], mods[i - 1]
        elif act == "down" and i < len(mods) - 1:
            mods[i + 1], mods[i] = mods[i], mods[i + 1]
        _redraw(prefix, mods)
    return True
