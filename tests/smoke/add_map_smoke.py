"""Open the add-a-map wizard and pick a map (read-only: does not add anything)."""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

URL = os.getenv("CA_URL", "https://192.168.1.97:8099")


def main() -> int:
    out = sys.argv[1] if len(sys.argv) > 1 else "."
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1300})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{URL}/clusters_ascended/login")
        page.fill('input[name="email"]', "admin")
        page.fill('input[name="password"]', os.environ["CA_PASSWORD"])
        page.keyboard.press("Enter")
        page.wait_for_selector("#ca-dash .ca-server", timeout=120_000)
        page.evaluate("location.hash = '#/cluster/oldmangan/add'")
        page.wait_for_selector("#ca-add .ca-mapchoice", timeout=60_000)
        page.click('#ca-add [data-map="TheIsland_WP"]')
        page.wait_for_timeout(800)
        print("create button disabled:", page.locator('#ca-add [data-act="create"]').is_disabled(),
              "| why:", page.locator("#ca-add-why").inner_text())
        page.screenshot(path=f"{out}/add-map.png", full_page=True)
        b.close()
    print("page errors:", errors or "none")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
