"""
Players and Console tabs of a running map (read-only: runs ListPlayers only):

    CA_PASSWORD=... uv run --with playwright python tests/smoke/players_console_smoke.py <map-id> [shots]
"""
from __future__ import annotations

import os
import sys

from playwright.sync_api import sync_playwright

URL = os.getenv("CA_URL", "https://192.168.1.97:8099")


def main() -> int:
    server = sys.argv[1]
    out = sys.argv[2] if len(sys.argv) > 2 else "."
    errors: list[str] = []
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1100})
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{URL}/clusters_ascended/login")
        page.fill('input[name="email"]', "admin")
        page.fill('input[name="password"]', os.environ["CA_PASSWORD"])
        page.keyboard.press("Enter")
        page.wait_for_selector("#ca-dash .ca-server", timeout=120_000)
        page.evaluate(f"location.hash = '#/map/{server}/players'")
        page.wait_for_selector("#ca-players-body .ca-table", timeout=60_000)
        page.wait_for_timeout(1500)
        page.screenshot(path=f"{out}/players.png", full_page=True)
        page.evaluate(f"location.hash = '#/map/{server}/console'")
        page.wait_for_selector("#ca-con-log div", timeout=60_000)
        page.click('[data-cmd="ListPlayers"]')
        page.wait_for_selector(".ca-con-reply", timeout=30_000)
        print("reply:", page.locator(".ca-con-reply").last.inner_text())
        page.wait_for_timeout(2500)
        page.screenshot(path=f"{out}/console.png")
        for tab in ("", "/settings", "/backups"):
            page.evaluate(f"location.hash = '#/map/{server}{tab}'")
            page.wait_for_selector("#ca-map-body .ca-card", timeout=30_000)
            page.wait_for_timeout(800)
        b.close()
    print("page errors:", errors or "none")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
