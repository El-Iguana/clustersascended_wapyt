"""
Browser-side helpers shared by the views. Runs in Pyodide only.

pytincture ships every local module the page imports, so the views live in
``ui/`` and import names from here.
"""
from __future__ import annotations

import asyncio
import html
import traceback

import js
from pyodide.ffi import create_proxy

from wapyt import message

APPLICATION = "clusters_ascended"


def spawn(coro, label: str):
    """Run a coroutine detached, but never silently."""

    async def _guarded():
        try:
            await coro
        except Exception:  # noqa: BLE001 - last line of defence
            js.console.error(f"[clusters-ascended] {label} failed:\n{traceback.format_exc()}")

    return asyncio.ensure_future(_guarded())


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def toast(text: str, kind: str = "info") -> None:
    message.toast(text, kind=kind)


def by_id(element_id: str):
    """``getElementById``; a miss is ``JsNull`` (falsy, but not ``None``)."""
    return js.document.getElementById(element_id)


def failed(result, what: str) -> bool:
    """True when a BFF result is an error; toasts it."""
    if isinstance(result, dict) and result.get("ok"):
        return False
    if isinstance(result, dict) and result.get("status") == 401:
        js.window.location.assign(f"/{APPLICATION}/login")
        return True
    error = result.get("error") if isinstance(result, dict) else None
    toast(error or f"Could not {what}.", kind="error")
    return True


def attr(element, name: str) -> str:
    if not element:
        return ""
    value = element.getAttribute(name)
    return str(value) if value else ""


def closest(element, selector: str):
    if not element or not hasattr(element, "closest"):
        return None
    found = element.closest(selector)
    return found if found else None


class Listeners:
    """Owns DOM listeners and timers, so a view drops them all at once."""

    def __init__(self) -> None:
        self._items: list = []
        self._timers: list = []

    def on(self, target, name: str, handler) -> None:
        if not target:
            return
        proxy = create_proxy(handler)
        target.addEventListener(name, proxy)
        self._items.append((target, name, proxy))

    def every(self, ms: int, handler) -> None:
        proxy = create_proxy(handler)
        self._timers.append((js.setInterval(proxy, ms), proxy))

    def clear(self) -> None:
        for timer, proxy in self._timers:
            js.clearInterval(timer)
            try:
                proxy.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._timers = []
        for target, name, proxy in self._items:
            try:
                target.removeEventListener(name, proxy)
                proxy.destroy()
            except Exception:  # noqa: BLE001
                pass
        self._items = []
