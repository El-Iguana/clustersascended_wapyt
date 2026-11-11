"""Open the per-item stack sizes on the Game settings tab and filter it (read-only)."""
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
        ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{URL}/clusters_ascended/login")
        page.fill('input[name="email"]', "admin")
        page.fill('input[name="password"]', os.environ["CA_PASSWORD"])
        page.keyboard.press("Enter")
        page.wait_for_selector("#ca-dash .ca-server", timeout=120_000)
        page.evaluate("location.hash = '#/cluster/oldmangan/settings'")
        page.wait_for_selector("#ca-stacks .ca-stacks-table", timeout=60_000)
        page.click('[data-jump="stacks"]')
        page.wait_for_timeout(800)
        page.screenshot(path=f"{out}/stacks.png")
        page.fill("#ca-stack-filter", "prime")
        page.wait_for_timeout(300)
        shown = page.locator("#ca-stacks tr[data-row]:not([hidden])").count()
        print("rows shown for 'prime':", shown)
        b.close()
    print("page errors:", errors or "none")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
