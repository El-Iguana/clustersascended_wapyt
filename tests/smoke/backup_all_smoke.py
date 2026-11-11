"""Back up every map of the cluster now (REAL backups; nothing is stopped)."""
import sys
from playwright.sync_api import sync_playwright
URL = "https://192.168.1.97:8099"; SHOTS = sys.argv[1]
errors = []
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 1300}); page = ctx.new_page()
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{URL}/clusters_ascended/login"); page.fill('input[name="email"]', "admin")
    page.fill('input[name="password"]', "changeme"); page.keyboard.press("Enter")
    page.wait_for_selector("#ca-dash .ca-server", timeout=120_000)
    page.evaluate("location.hash = '#/cluster/oldmangan/backups'")
    page.wait_for_selector('#ca-backups [data-act="backup-all"]', timeout=60_000)
    page.screenshot(path=f"{SHOTS}/backups-tab.png", full_page=True)
    page.click('#ca-backups [data-act="backup-all"]')
    page.wait_for_selector(".wapyt-toast-text", timeout=15_000)
    print("toast:", page.locator(".wapyt-toast-text").first.inner_text())
    b.close()
print("page errors:", errors)
