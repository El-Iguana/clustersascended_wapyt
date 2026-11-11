"""
Click an action on a map's card (a REAL action against the real server):

    uv run --with playwright python tests/smoke/act_smoke.py <server-id> <start|stop|restart> [screenshot-dir]

Confirms the dialog if one appears, waits for the "queued" toast, and saves
a screenshot. It does not wait for the job: follow it on the Jobs page.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8098/clusters_ascended/"


def main() -> int:
    server, action = sys.argv[1], sys.argv[2]
    out = Path(sys.argv[3] if len(sys.argv) > 3 else Path(__file__).parent)
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1400, "height": 1000})
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(BASE)
        button = f'#ca-dash [data-act="{action}"][data-id="{server}"]'
        page.wait_for_selector(button, timeout=120_000)
        page.click(button)
        dialog = page.locator(".wapyt-msg-dialog .wapyt-msg-btn-primary")
        try:
            dialog.wait_for(timeout=3000)
            dialog.click()
        except Exception:  # noqa: BLE001 - start has no confirm
            pass
        page.wait_for_selector(".wapyt-toast-text", timeout=15_000)
        print("toast:", page.locator(".wapyt-toast-text").first.inner_text())
        page.wait_for_timeout(3500)
        page.screenshot(path=str(out / f"{action}-{server}.png"), full_page=True)
        browser.close()
    for e in errors:
        print("error:", e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
