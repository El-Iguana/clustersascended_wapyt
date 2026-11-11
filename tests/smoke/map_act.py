"""
Click an action on a map's page (REAL), confirm, print the toast:

    uv run --with playwright python tests/smoke/map_act.py <server-id> <action>
"""
from __future__ import annotations

import sys

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8098/clusters_ascended/"


def main() -> int:
    server, action = sys.argv[1], sys.argv[2]
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE + f"#/map/{server}")
        button = f'#ca-map-status [data-act="{action}"]'
        page.wait_for_selector(button, timeout=120_000)
        page.click(button)
        dialog = page.locator(".wapyt-msg-dialog .wapyt-msg-btn-primary")
        try:
            dialog.wait_for(timeout=3000)
            dialog.click()
        except Exception:  # noqa: BLE001 - no confirm for this action
            pass
        page.wait_for_selector(".wapyt-toast-text", timeout=15_000)
        print("toast:", page.locator(".wapyt-toast-text").first.inner_text())
        browser.close()
    for e in errors:
        print("error:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
