"""Game settings: the group buttons scroll to their group (run against the live app)."""
from playwright.sync_api import sync_playwright
URL = "https://192.168.1.97:8099"
with sync_playwright() as p:
    b = p.chromium.launch(); ctx = b.new_context(ignore_https_errors=True, viewport={"width": 1400, "height": 900}); page = ctx.new_page()
    page.goto(f"{URL}/clusters_ascended/login"); page.fill('input[name="email"]', "admin")
    page.fill('input[name="password"]', "changeme"); page.keyboard.press("Enter")
    page.wait_for_selector("#ca-dash .ca-server", timeout=120_000)
    page.evaluate("location.hash = '#/cluster/oldmangan/settings'")
    page.wait_for_selector("#ca-game-form fieldset", timeout=60_000)
    page.wait_for_timeout(1000)
    for gid in ("gathering", "rules", "difficulty"):
        page.click(f'[data-jump="g-{gid}"]')
        page.wait_for_timeout(600)
        r = page.evaluate(f"""() => {{ const f = document.querySelector('[data-fieldset-id="g-{gid}"]');
            const bar = document.querySelector('.ca-game-bar').getBoundingClientRect();
            const t = f.getBoundingClientRect(); return [Math.round(t.top), Math.round(bar.bottom), document.getElementById('ca-view').scrollTop]; }}""")
        print(gid, "fieldset top", r[0], "bar bottom", r[1], "scrollTop", r[2], "VISIBLE" if r[0] >= r[1] - 2 and r[0] < 500 else "NOT AT TOP")
    page.click('[data-jump="g-rules"]'); page.wait_for_timeout(600)
    page.screenshot(path="/tmp/claude-1000/-home-oldmangan-development/e3da13e3-3740-4eeb-a0e1-52df2182f3a7/scratchpad/shots/jump-rules.png")
    b.close()
