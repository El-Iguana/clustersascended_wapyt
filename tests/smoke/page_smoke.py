"""
Smoke test against a running app (scripts/dev.sh start):

    uv run --with playwright python tests/smoke/page_smoke.py [screenshot-dir]

Opens every view, fails on any console error or page error, and saves a
screenshot of each. Read-only: it does not start or stop anything.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8098/clusters_ascended/"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(BASE)   # local sign-in on this machine
        page.wait_for_selector("#ca-dash .ca-card, #ca-dash .ca-cluster", timeout=120_000)
        page.screenshot(path=str(OUT / "dashboard.png"), full_page=True)
        for route, selector in (("#/jobs", "#ca-jobs .ca-table, #ca-jobs .ca-empty"),
                                ("#/host", "#ca-host .ca-form"),
                                ("#/import", "#ca-import .ca-form")):
            page.evaluate(f"location.hash = '{route}'")
            page.wait_for_selector(selector, timeout=30_000)
            page.wait_for_timeout(800)
            page.screenshot(path=str(OUT / f"{route.strip('#/')}.png"), full_page=True)
        page.evaluate("location.hash = '#/'")
        page.wait_for_selector("#ca-dash .ca-host", timeout=30_000)
        browser.close()
    for e in errors:
        print(e)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
