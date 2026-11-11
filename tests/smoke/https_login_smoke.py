"""
Sign in over HTTPS (Caddy) with the form and load the dashboard:

    CA_URL=https://192.168.1.97:8099 CA_PASSWORD=... uv run --with playwright python tests/smoke/https_login_smoke.py [shots]
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

URL = os.getenv("CA_URL", "https://192.168.1.97:8099").rstrip("/")
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).parent)


def main() -> int:
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1000})
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{URL}/clusters_ascended/login")
        page.fill('input[name="email"]', os.getenv("CA_USER", "admin"))
        page.fill('input[name="password"]', os.environ["CA_PASSWORD"])
        page.keyboard.press("Enter")
        page.wait_for_selector("#ca-dash .ca-server", timeout=120_000)
        page.wait_for_timeout(2000)
        page.screenshot(path=str(OUT / "https-dashboard.png"), full_page=True)
        browser.close()
    for e in errors:
        print("error:", e)
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
