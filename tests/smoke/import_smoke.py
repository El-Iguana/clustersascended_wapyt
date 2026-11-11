"""
Import the real compose cluster through the UI (database only), then show
the dashboard:

    uv run --with playwright python tests/smoke/import_smoke.py [screenshot-dir]
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
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.goto(BASE + "#/import")
        page.wait_for_selector("#ca-import .ca-form", timeout=120_000)
        page.fill('#ca-import input[name="name"]', "OldManGan")
        page.click('#ca-import [data-act="preview"]')
        page.wait_for_selector("#ca-import-plan .ca-table", timeout=30_000)
        page.screenshot(path=str(OUT / "import-preview.png"), full_page=True)
        page.click('#ca-import [data-act="apply"]')
        page.wait_for_selector("#ca-dash .ca-server", timeout=30_000)
        page.wait_for_timeout(4000)
        page.screenshot(path=str(OUT / "dashboard-imported.png"), full_page=True)
        browser.close()
    for e in errors:
        print(e)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
