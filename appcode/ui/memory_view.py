"""
Memory history (``#/memory``): every map's memory over time, stacked, with
everything else on the host, against RAM and the budget admission control uses.
"""
from __future__ import annotations

import js

from services.host_api import HostApi
from ui.common import Listeners, attr, closest, esc, failed, spawn
from ui.memory_chart import host_chart, no_readings, ranges_html, set_pressed


class MemoryView:
    def __init__(self, container) -> None:
        self.el = container
        self.listeners = Listeners()
        self.range = "24h"
        self.chart = None

    def mount(self) -> None:
        self.el.innerHTML = f"""
<div class="ca-page" id="ca-memory">
  <div class="ca-page-head"><a class="ca-back" href="#/">←</a><h1>Memory history</h1>
    <span class="ca-sub">What each map used, and everything else on this machine.</span></div>
  <section class="ca-card">
    <div class="ca-host-head"><h2>Memory over time</h2>{ranges_html(self.range)}</div>
    <div class="ca-sub" id="ca-memory-sum"></div>
    <div class="ca-chart" id="ca-memory-chart" data-wapyt-theme="dark"></div>
    <div class="ca-sub">Each point is the highest reading in its stretch of time. Above the RAM line the
      machine is using swap; admission control keeps starts under the budget. <a href="#/host">Change the rules</a></div>
  </section>
</div>"""
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.every(60000, lambda: spawn(self.load(), "memory refresh"))
        spawn(self.load(), "memory load")

    def unmount(self) -> None:
        self.listeners.clear()
        self._drop()

    def _drop(self) -> None:
        if self.chart is not None:
            self.chart.destroy()
            self.chart = None

    def _on_click(self, event) -> None:
        btn = closest(event.target, "[data-range]")
        if btn:
            self.range = attr(btn, "data-range")
            set_pressed(self.el, self.range)
            spawn(self.load(), "memory range")

    async def load(self) -> None:
        d = await HostApi().memory_history_async(self.range)
        if failed(d, "load the memory history"):
            return
        box = js.document.getElementById("ca-memory-chart")
        summary = js.document.getElementById("ca-memory-sum")
        if not box:
            return
        self._drop()
        if no_readings(d):
            box.innerHTML = ('<div class="ca-empty">No readings yet. The app takes one a minute; '
                             "the chart fills in from now on.</div>")
            return
        box.innerHTML = ""
        self.chart = host_chart(box, d)
        if summary:
            parts = []
            if d.get("peak"):
                parts.append(f'Highest: <b>{d["peak"]["gib"]:.1f} GiB</b> at {esc(d["peak"]["label"])}, '
                             f'of a {d["budget"]:.1f} GiB budget and {d["ram"]:.1f} GiB of RAM.')
            if d.get("partial") and d.get("since"):
                parts.append(f"Readings start {esc(d['since'])}.")
            summary.innerHTML = " ".join(parts)
