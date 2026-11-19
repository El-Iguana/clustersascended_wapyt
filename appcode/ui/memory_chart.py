"""
Memory charts (Chart.js, through wapyt). Browser only.

A map keeps its dashboard colour. Reference lines (RAM, budget, a map's
need) are neutral greys, never a map colour. Values are GiB; each point is the
highest reading in its slice of time.
"""
from __future__ import annotations

from wapyt import Chart, ChartConfig, ChartDataset

from ui.common import OTHER_COLOUR, esc, map_colour
from ui.themes import is_dark

RANGES = (("6h", "6 hours"), ("24h", "24 hours"), ("7d", "7 days"), ("14d", "14 days"))


def ram_colour() -> str:
    return "#94a3b8" if is_dark() else "#64748b"


def limit_colour() -> str:
    """The budget / running-need line: near white on the dark themes, slate on Daylight."""
    return "#e2e8f0" if is_dark() else "#334155"

# 180-340 time labels: show about a dozen, level.
X_TICKS = {"scales": {"x": {"ticks": {"autoSkip": True, "maxTicksLimit": 12, "maxRotation": 0}}}}


def ranges_html(active: str) -> str:
    return ('<div class="ca-ranges" role="group" aria-label="Time range">' + "".join(
        f'<button class="ca-btn" data-range="{key}" aria-pressed="{"true" if key == active else "false"}">'
        f"{esc(label)}</button>" for key, label in RANGES) + "</div>")


def set_pressed(root, active: str) -> None:
    for el in root.querySelectorAll("[data-range]"):
        el.setAttribute("aria-pressed", "true" if el.getAttribute("data-range") == active else "false")


def _line(label: str, value, n: int, colour: str, dashed: bool) -> ChartDataset:
    return ChartDataset(label, [value] * n, color=colour, type="line", stack=label, dashed=dashed, point_radius=0)


def host_chart(el, d: dict) -> Chart:
    n = len(d["labels"])
    sets = [ChartDataset(m["name"], m["values"], color=map_colour(m["colour"])) for m in d["maps"]]
    sets.append(ChartDataset("Everything else", d["other"], color=OTHER_COLOUR))
    sets += [_line("RAM", d["ram"], n, ram_colour(), False), _line("Budget", d["budget"], n, limit_colour(), True)]
    return Chart(ChartConfig(
        type="area", stacked=True, labels=d["labels"], datasets=sets, legend="top",
        value_suffix=" GiB", decimals=1, table_toggle=True, animation=False, options=X_TICKS,
        aria_label="Memory over time: each map stacked, then everything else, against RAM and the budget",
    ), root=el)


def map_chart(el, d: dict) -> Chart:
    n = len(d["labels"])
    sets = [ChartDataset(d["name"], d["values"], color=map_colour(d["colour"]), fill=True),
            _line("Running need", d["need"], n, limit_colour(), True)]
    return Chart(ChartConfig(
        type="line", labels=d["labels"], datasets=sets, legend="top",
        value_suffix=" GiB", decimals=1, table_toggle=True, animation=False, options=X_TICKS,
        aria_label=f"{d['name']}'s memory over time, against its running need",
    ), root=el)


def no_readings(d: dict) -> bool:
    return not any(v is not None for v in (d.get("other") or d.get("values") or []))
