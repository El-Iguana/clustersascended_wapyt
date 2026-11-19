"""
Colour themes, picked from swatches behind the toolbar's Theme button, as in
the AI Ecosystem dashboard (ai-stack's stack-ui) and Whimsy.

Five dark colourways share Whimsy's names and palettes (Bone, Ember, Moss,
Dusk, Harbor); Daylight is the light one. Every colour in the page's CSS is a
variable set by ``<html data-catheme="…">``, and wapyt's own tokens point at
the same variables, so one attribute restyles the page.

The choice is a per-browser convenience kept in localStorage (guarded: storage
can be missing or throw in private windows). With nothing stored the page
follows the OS: Harbor when it prefers dark, Daylight when light.
"""
from __future__ import annotations

import js

# (id, label, swatch colour, dark?)
THEMES = [
    ("bone", "Bone", "#f5f1e8", True),
    ("ember", "Ember", "#e8a33d", True),
    ("moss", "Moss", "#9ec978", True),
    ("dusk", "Dusk", "#b79df0", True),
    ("harbor", "Harbor", "#7fb4d8", True),
    ("daylight", "Daylight", "#2563eb", False),
]
KEY = "clustersascended.theme"
_BY_ID = {t[0]: t for t in THEMES}


def _stored() -> str:
    try:
        v = js.window.localStorage.getItem(KEY)
        return str(v) if v else ""
    except Exception:  # noqa: BLE001 - storage blocked or unavailable
        return ""


def _store(theme: str) -> None:
    try:
        js.window.localStorage.setItem(KEY, theme)
    except Exception:  # noqa: BLE001
        pass


def default() -> str:
    try:
        dark = bool(js.window.matchMedia("(prefers-color-scheme: dark)").matches)
    except Exception:  # noqa: BLE001
        dark = True
    return "harbor" if dark else "daylight"


def current() -> str:
    t = _stored()
    return t if t in _BY_ID else default()


def is_dark() -> bool:
    """Whether the theme on the page now is a dark one (charts and map colours ask)."""
    t = str(js.document.documentElement.getAttribute("data-catheme") or "")
    return _BY_ID.get(t, _BY_ID[default()])[3]


def label(theme: str) -> str:
    return _BY_ID.get(theme, _BY_ID[default()])[1]


def apply(app, theme: str, remember: bool = False) -> str:
    """Paint ``theme`` (falls back to the default) and tell wapyt whether it is dark."""
    theme = theme if theme in _BY_ID else default()
    js.document.documentElement.setAttribute("data-catheme", theme)
    app.set_theme("dark" if _BY_ID[theme][3] else "light")
    if remember:
        _store(theme)
    for el in js.document.querySelectorAll(".ca-swatch"):
        el.setAttribute("aria-pressed", "true" if str(el.getAttribute("data-theme")) == theme else "false")
    name = js.document.getElementById("ca-theme-name")
    if name:
        name.textContent = label(theme)
    return theme


def picker_html() -> str:
    swatches = "".join(
        f'<button class="ca-swatch" data-theme="{t}" aria-pressed="false" title="{name}">'
        f'<i style="background:{dot}"></i><span>{name}</span></button>'
        for t, name, dot, _ in THEMES)
    return (f'<div class="ca-themes" id="ca-themes" role="dialog" aria-label="Theme" hidden>'
            f'<div class="ca-themes-head"><span>Theme</span><b id="ca-theme-name"></b></div>'
            f'<div class="ca-swatches">{swatches}</div>'
            f'<div class="ca-themes-foot">Kept in this browser.</div></div>')
