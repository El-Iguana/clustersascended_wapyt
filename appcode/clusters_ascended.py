"""
Clusters Ascended — the browser UI. Runs in Pyodide.

Conventions from the sibling wapyt apps:

* BFF calls are ``await X().method_async(...)``.
* The UI is built in ``load_ui``, never ``__init__``.
* Import *names* from service modules, never ``from services import x``.
* DOM misses come back as ``JsNull``, which is falsy but not ``None``.

Routes (URL hash): ``#/`` dashboard, ``#/cluster/<id>``, ``#/map/<id>``,
``#/jobs[/<id>]``, ``#/host``, ``#/memory``, ``#/new-cluster``, ``#/import``.
"""
from __future__ import annotations

import js

from wapyt import (
    CellConfig,
    LayoutConfig,
    MainWindow,
    ToolbarButton,
    ToolbarConfig,
    ToolbarSeparator,
    ToolbarSpacer,
    ToolbarText,
)

from ui.cluster_view import ClusterView
from ui.common import APPLICATION, Listeners, attr, by_id, closest, spawn
from ui.dashboard import DashboardView
from ui.host import HostView
from ui.import_view import ImportView
from ui.memory_view import MemoryView
from ui.new_cluster import NewClusterView
from ui.jobs import JobsView
from ui.server_view import ServerView
from ui.themes import apply as apply_theme
from ui.themes import current as current_theme
from ui.themes import picker_html

# pytincture's MainWindow detection only knows dhxpyt; without this the app
# starts with HTTP 422.
APP_ENTRYPOINT = "ClustersAscended"
APP_FAVICON = "static/logo.svg"

# The CSRF cookie under service.COOKIE_NAMESPACE: __Host- over HTTPS, -dev-
# over loopback HTTP. Matched exactly: another pytincture app has its own.
_CSRF_COOKIES = ("__Host-clustersascended-csrf", "clustersascended-dev-csrf")


def _csrf_token() -> str:
    for part in str(js.document.cookie).split(";"):
        name, _, value = part.strip().partition("=")
        if name in _CSRF_COOKIES:
            return str(js.decodeURIComponent(value))
    return ""


class ClustersAscended(MainWindow):
    # No __init__: wapyt's LoadUICaller calls load_ui() after construction.

    def load_ui(self) -> None:
        js.document.title = "Clusters Ascended"
        self._view = None
        self._window_listeners = Listeners()
        _inject_css()
        # Before any widget is built, so the first paint is already in the chosen colours.
        apply_theme(self, current_theme())
        self._build_chrome()
        self._window_listeners.on(js.window, "hashchange", lambda _e: self._route())
        self._route()

    def _build_chrome(self) -> None:
        self.toolbar = self.add_toolbar(
            "mainwindow_header",
            ToolbarConfig(
                label="Clusters Ascended",
                items=[
                    ToolbarText("brand", "Clusters Ascended"),
                    ToolbarButton("dashboard", "Dashboard", "mdi-view-dashboard-outline", group="nav"),
                    ToolbarButton("jobs", "Jobs", "mdi-format-list-checks", group="nav"),
                    ToolbarButton("host", "Host rules", "mdi-memory", group="nav"),
                    ToolbarButton("new-cluster", "New cluster", "mdi-plus-box-outline", group="nav"),
                    ToolbarButton("import", "Import", "mdi-import", group="nav"),
                    ToolbarSpacer(),
                    ToolbarButton("theme", "Theme", "mdi-palette-outline"),
                    ToolbarText("user", "admin"),
                    ToolbarSeparator(),
                    ToolbarButton("logout", "Sign out", "mdi-logout"),
                ],
            ),
        )
        self.toolbar.on_click(self._on_toolbar_click)
        # wapyt gives toolbar text no id: tag the brand (the first text) for its logo tile.
        texts = js.document.querySelectorAll(".wapyt-toolbar .wapyt-toolbar-text")
        if texts.length:
            texts[0].setAttribute("data-ca", "brand")
        holder = js.document.createElement("div")
        holder.innerHTML = picker_html()
        js.document.body.appendChild(holder.firstElementChild)
        apply_theme(self, current_theme())          # marks the active swatch
        self._window_listeners.on(js.document, "click", self._on_doc_click)
        self._window_listeners.on(js.document, "keydown", self._on_key)
        body = self.add_layout("mainwindow", LayoutConfig(type="line", rows=[CellConfig(id="main", height="100%")]))
        cell = body.get_cell("main")
        main = cell.getContainer() if hasattr(cell, "getContainer") else cell
        main.innerHTML = '<div class="ca-view" id="ca-view"></div>'

    def _on_toolbar_click(self, payload: dict) -> None:
        item = payload.get("id")
        if item == "logout":
            spawn(self._logout(), "sign out")
        elif item == "theme":
            self._toggle_themes()
        elif item in ("dashboard", "jobs", "host", "new-cluster", "import"):
            js.window.location.hash = "#/" if item == "dashboard" else f"#/{item}"

    # -- the theme picker ------------------------------------------------------

    def _toggle_themes(self, show: bool | None = None) -> None:
        box = by_id("ca-themes")
        if not box:
            return
        show = bool(box.hidden) if show is None else show
        box.hidden = not show
        if show:
            on = box.querySelector('.ca-swatch[aria-pressed="true"]')
            if on:
                on.focus()

    def _on_doc_click(self, event) -> None:
        sw = closest(event.target, ".ca-swatch")
        if sw:
            apply_theme(self, attr(sw, "data-theme"), remember=True)
            self._route()        # redraw the page: charts and map colours have light and dark steps
            return
        box = by_id("ca-themes")
        if box and not box.hidden and not closest(event.target, "#ca-themes") \
                and not closest(event.target, '[data-id="theme"]'):
            box.hidden = True

    def _on_key(self, event) -> None:
        if str(event.key) == "Escape":
            self._toggle_themes(False)

    async def _logout(self) -> None:
        options = js.Object.new()
        options.method = "POST"
        options.credentials = "same-origin"
        # Followed, the redirect to /login would sign this machine straight back in.
        options.redirect = "manual"
        headers = js.Object.new()
        setattr(headers, "X-CSRF-Token", _csrf_token())
        options.headers = headers
        try:
            await js.fetch(f"/{APPLICATION}/auth/logout", options)
        finally:
            js.window.location.assign(f"/{APPLICATION}/login?manual=1")

    def _route(self) -> None:
        parts = [p for p in str(js.window.location.hash or "").lstrip("#").split("/") if p]
        if self._view is not None:
            self._view.unmount()
        container = by_id("ca-view")
        page = parts[0] if parts else "dashboard"
        if page == "jobs":
            job = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
            self._view = JobsView(container, job)
        elif page == "host":
            self._view = HostView(container)
        elif page == "memory":
            self._view = MemoryView(container)
        elif page == "new-cluster":
            self._view = NewClusterView(container)
        elif page == "import":
            self._view = ImportView(container)
        elif page == "map" and len(parts) > 1:
            self._view = ServerView(container, parts[1], parts[2] if len(parts) > 2 else "")
        elif page == "cluster" and len(parts) > 1:
            self._view = ClusterView(container, parts[1], parts[2] if len(parts) > 2 else "")
        else:
            page = "dashboard"
            self._view = DashboardView(container)
        nav = {"map": "dashboard", "cluster": "dashboard", "memory": "dashboard"}.get(page, page)
        try:
            self.toolbar.set_active(nav, True)
        except Exception:  # noqa: BLE001 - cosmetic
            pass
        self._view.mount()


_CSS = """
/* --- palettes (ui/themes.py sets <html data-catheme>) ----------------------------------------
   bg: page · bg2: chrome · card/card2: raised surfaces · line: borders · txt/mut: text ·
   acc: accent · grad: filled buttons and the logo · ink: text on grad · accSoft/accLine/accText:
   tinted backgrounds, borders and text. The dark five are Whimsy's, as in ai-stack's dashboard. */
:root,[data-catheme="harbor"]{
  --bg:#0b0f14;--bg2:#10161d;--card:#141b24;--card2:#1a232e;--line:rgba(180,210,235,.10);
  --txt:#ecf2f8;--mut:#93a3b5;--acc:#7fb4d8;--grad:linear-gradient(135deg,#8cc4e6,#5a8fd0);
  --ink:#0e1a24;--accSoft:rgba(127,180,216,.13);--accLine:rgba(127,180,216,.45);--accText:#c4e0f2;}
[data-catheme="bone"]{
  --bg:#0e0e12;--bg2:#141419;--card:#17171e;--card2:#1e1e27;--line:rgba(245,241,232,.09);
  --txt:#f5f1e8;--mut:#a39c8f;--acc:#f5f1e8;--grad:#f5f1e8;--ink:#141419;
  --accSoft:rgba(245,241,232,.12);--accLine:rgba(245,241,232,.45);--accText:#f3ddae;}
[data-catheme="ember"]{
  --bg:#100d0b;--bg2:#161110;--card:#1a1412;--card2:#211917;--line:rgba(240,200,160,.10);
  --txt:#f5efe4;--mut:#a89a86;--acc:#e8a33d;--grad:linear-gradient(135deg,#f0b64c,#cf4f2e);
  --ink:#241505;--accSoft:rgba(232,163,61,.12);--accLine:rgba(232,163,61,.45);--accText:#f3ddae;}
[data-catheme="moss"]{
  --bg:#0c120e;--bg2:#111814;--card:#151d17;--card2:#1b241d;--line:rgba(200,230,190,.09);
  --txt:#eef3e6;--mut:#97a48f;--acc:#9ec978;--grad:#9ec978;--ink:#101a10;
  --accSoft:rgba(158,201,120,.12);--accLine:rgba(158,201,120,.45);--accText:#cfe8bd;}
[data-catheme="dusk"]{
  --bg:#100e16;--bg2:#151221;--card:#1a1626;--card2:#211c30;--line:rgba(210,190,245,.10);
  --txt:#f1ecf9;--mut:#a298b8;--acc:#b79df0;--grad:linear-gradient(135deg,#c5aef5,#e08bb8);
  --ink:#1c1530;--accSoft:rgba(183,157,240,.13);--accLine:rgba(183,157,240,.45);--accText:#dccdfb;}
[data-catheme="daylight"]{
  --bg:#f4f6f9;--bg2:#ffffff;--card:#ffffff;--card2:#f8fafc;--line:#e2e8f0;
  --txt:#0f172a;--mut:#64748b;--acc:#2563eb;--grad:linear-gradient(135deg,#3b82f6,#6366f1);
  --ink:#ffffff;--accSoft:rgba(37,99,235,.09);--accLine:rgba(37,99,235,.40);--accText:#1e40af;}

/* Derived tokens. Status colours depend only on dark vs light. */
:root,[data-catheme]{
  --txt2:color-mix(in srgb,var(--txt) 78%,var(--mut));
  --line2:color-mix(in srgb,var(--txt) 17%,transparent);
  --field:color-mix(in srgb,var(--bg) 65%,var(--card));
  --code:color-mix(in srgb,var(--bg) 55%,#000);
  --good:#4ade80;--good-soft:rgba(74,222,128,.12);--good-line:rgba(74,222,128,.40);
  --warn:#fbbf24;--warn-soft:rgba(251,191,36,.10);--warn-line:rgba(251,191,36,.42);--warn-txt:#fcd34d;
  --bad:#f87171;--bad-soft:rgba(248,113,113,.11);--bad-line:rgba(248,113,113,.45);--bad-txt:#fecaca;
  --radius:14px;--shadow:0 18px 50px rgba(0,0,0,.45);
  --sans:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif;
  --mono:ui-monospace,"JetBrains Mono","Cascadia Code",Menlo,Consolas,monospace;
  color-scheme:dark;
  /* wapyt's widgets (forms, dialogs, toasts, charts, the toolbar) follow the same palette. */
  --wapyt-font-family:var(--sans);
  --wapyt-bg:var(--card);--wapyt-surface:var(--bg2);--wapyt-border:var(--line);--wapyt-divider:var(--line2);
  --wapyt-text:var(--txt);
  --wapyt-bg-dark:var(--card);--wapyt-surface-dark:var(--bg2);--wapyt-border-dark:var(--line);
  --wapyt-divider-dark:var(--line2);--wapyt-text-dark:var(--txt);
  --wapyt-accent-light:var(--acc);--wapyt-accent-dark:var(--acc);
  --wapyt-accent-strong-light:var(--accText);--wapyt-accent-strong-dark:var(--accText);
  --wapyt-accent-text-light:var(--accText);--wapyt-accent-text-dark:var(--accText);
  --wapyt-on-accent-light:var(--ink);--wapyt-on-accent-dark:var(--ink);
  --wapyt-focus-light:var(--accLine);--wapyt-focus-dark:var(--accLine);
  --wapyt-muted-light:var(--mut);--wapyt-muted-dark:var(--mut);
  --wapyt-radius:8px;}
[data-catheme="daylight"]{
  --code:#f1f5f9;
  --good:#059669;--good-soft:rgba(5,150,105,.09);--good-line:rgba(5,150,105,.40);
  --warn:#b45309;--warn-soft:rgba(217,119,6,.08);--warn-line:rgba(217,119,6,.40);--warn-txt:#92400e;
  --bad:#dc2626;--bad-soft:rgba(220,38,38,.07);--bad-line:rgba(220,38,38,.40);--bad-txt:#991b1b;
  --shadow:0 14px 40px rgba(15,23,42,.12);color-scheme:light;}

body{background:var(--bg);color:var(--txt);}
#maindiv,.wapyt-layout{background:var(--bg) !important;}
.wapyt-cell{border-color:var(--line) !important;}

/* --- the toolbar: a logo tile for the brand, pill buttons --------------------------------- */
.wapyt-toolbar{background:var(--bg2) !important;border-bottom:1px solid var(--line) !important;min-height:52px;
  padding:8px 14px;}
.wapyt-toolbar .wapyt-toolbar-btn{border-radius:999px;color:var(--txt2);}
.wapyt-toolbar .wapyt-toolbar-btn:hover:not(:disabled){background:var(--accSoft);border-color:var(--accLine);color:var(--txt);}
.wapyt-toolbar .wapyt-toolbar-btn[aria-pressed="true"]{background:var(--accSoft);border-color:var(--accLine);color:var(--accText);}
.wapyt-toolbar-sep{background:var(--line2) !important;}
.wapyt-toolbar-text{opacity:1;color:var(--mut);font-size:12.5px;}
.wapyt-toolbar-text[data-ca="brand"]{color:var(--txt);font-size:15px;font-weight:750;letter-spacing:.2px;
  display:inline-flex;align-items:center;gap:10px;overflow:visible;margin-right:10px;}
.wapyt-toolbar-text[data-ca="brand"]::before{content:"CA";display:inline-flex;align-items:center;justify-content:center;
  width:30px;height:30px;border-radius:9px;background:var(--grad);color:var(--ink);font:800 12.5px var(--sans);letter-spacing:.5px;}

/* --- the theme picker (behind the toolbar's Theme button) --------------------------------- */
.ca-themes{position:fixed;top:56px;right:14px;z-index:1000;width:250px;background:var(--card);
  border:1px solid var(--line2);border-radius:var(--radius);box-shadow:var(--shadow);padding:12px;
  display:flex;flex-direction:column;gap:10px;color:var(--txt);font:13px/1.4 var(--sans);}
.ca-themes[hidden]{display:none;}
.ca-themes-head{display:flex;justify-content:space-between;align-items:baseline;font-size:10.5px;font-weight:800;
  letter-spacing:1.3px;text-transform:uppercase;color:var(--mut);}
.ca-themes-head b{color:var(--txt);}
.ca-swatches{display:grid;grid-template-columns:1fr 1fr;gap:6px;}
.ca-swatch{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:999px;border:1px solid var(--line);
  background:var(--card2);color:var(--txt2);font:inherit;cursor:pointer;text-align:left;}
.ca-swatch i{width:16px;height:16px;border-radius:50%;flex:none;box-shadow:inset 0 0 0 1px rgba(0,0,0,.25);}
.ca-swatch:hover{border-color:var(--accLine);color:var(--txt);}
.ca-swatch[aria-pressed="true"]{border-color:var(--txt);box-shadow:inset 0 0 0 1px var(--txt);color:var(--txt);font-weight:650;}
.ca-swatch:focus-visible{outline:2px solid var(--accLine);outline-offset:2px;}
.ca-themes-foot{color:var(--mut);font-size:11.5px;}

/* --- wapyt's dialogs and toasts ---------------------------------------------------------- */
.wapyt-msg-dialog{border-radius:18px !important;box-shadow:var(--shadow);background:var(--card) !important;
  color:var(--txt) !important;border-color:var(--line2) !important;}
.wapyt-msg-overlay{backdrop-filter:blur(5px);}
.wapyt-msg-btn{border-radius:999px !important;}
.wapyt-msg-btn-primary{background:var(--grad) !important;border-color:transparent !important;color:var(--ink) !important;}
.wapyt-toast{background:var(--card2) !important;color:var(--txt) !important;border:1px solid var(--line2) !important;
  border-radius:12px !important;box-shadow:var(--shadow);}

/* --- the page ---------------------------------------------------------------------------- */
.ca-view{height:100%;overflow:auto;color:var(--txt);font:14px/1.5 var(--sans);box-sizing:border-box;
  background:radial-gradient(1100px 420px at 100% -10%,var(--accSoft),transparent 65%),var(--bg);}
.ca-view a{color:var(--accText);}
.ca-view code{background:var(--card2);border:1px solid var(--line);border-radius:6px;padding:0 5px;font:12px var(--mono);}
.ca-page{padding:22px 26px;display:flex;flex-direction:column;gap:16px;max-width:1500px;margin:0 auto;}
.ca-page-head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;}
.ca-page-head h1{margin:0;font-size:22px;font-weight:750;letter-spacing:.1px;}
.ca-back{color:var(--mut);text-decoration:none;font-size:20px;padding:2px 10px;border-radius:999px;}
.ca-back:hover{background:var(--accSoft);color:var(--txt);}
.ca-card{background:radial-gradient(520px 170px at 0% 0%,var(--accSoft),transparent 70%),var(--card);
  border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;}
.ca-card h2,.ca-cluster h2{margin:0;font-size:16px;font-weight:700;}
.ca-sub{color:var(--mut);font-size:12.5px;}
.ca-empty{color:var(--mut);padding:20px;text-align:center;}
.ca-warn{color:var(--warn);}
.ca-banner{display:flex;gap:8px;align-items:flex-start;padding:10px 14px;border-radius:12px;border:1px solid;}
.ca-banner[data-kind=error]{background:var(--bad-soft);border-color:var(--bad-line);color:var(--bad-txt);}
.ca-banner[data-kind=warning]{background:var(--warn-soft);border-color:var(--warn-line);color:var(--warn-txt);}
.ca-host{display:flex;flex-direction:column;gap:10px;}
.ca-host-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;}
.ca-bar{position:relative;display:flex;height:26px;background:var(--field);border:1px solid var(--line);
  border-radius:8px;overflow:visible;}
.ca-seg{height:100%;opacity:.92;}
.ca-seg:first-child{border-radius:7px 0 0 7px;}
.ca-seg-ext{background:repeating-linear-gradient(45deg,color-mix(in srgb,var(--mut) 70%,transparent) 0 6px,
  color-mix(in srgb,var(--mut) 40%,transparent) 6px 12px);}
.ca-mark{position:absolute;top:-6px;bottom:-6px;width:0;border-left:2px dashed var(--mut);}
.ca-mark span{position:absolute;top:100%;left:4px;font-size:11px;color:var(--mut);white-space:nowrap;}
.ca-mark-budget{border-left:2px solid var(--bad);}
.ca-mark-budget span{color:var(--bad);left:auto;right:4px;}
.ca-host .ca-legend{margin-top:8px;}
.ca-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:var(--txt2);}
.ca-legend i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:6px;vertical-align:-1px;}
.ca-legend i.ca-ext{background:color-mix(in srgb,var(--mut) 70%,transparent);}
.ca-cluster{display:flex;flex-direction:column;gap:10px;}
.ca-cluster-head{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;}
.ca-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;}
.ca-server{position:relative;overflow:hidden;
  background:radial-gradient(520px 170px at 0% 0%,color-mix(in srgb,var(--ca-colour) 13%,transparent),transparent 70%),var(--card);
  border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px 14px;display:flex;flex-direction:column;gap:8px;
  transition:border-color .15s ease,transform .15s ease,box-shadow .15s ease;}
.ca-server::before{content:"";position:absolute;left:0;right:0;top:0;height:3px;
  background:linear-gradient(90deg,var(--ca-colour),color-mix(in srgb,var(--ca-colour) 25%,transparent));}
.ca-server:hover{border-color:var(--accLine);box-shadow:var(--shadow);transform:translateY(-1px);}
.ca-server header{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;}
.ca-server h3{margin:0;font-size:16px;font-weight:700;}
.ca-stats{display:flex;gap:20px;flex-wrap:wrap;}
.ca-k{color:var(--mut);font-size:12px;margin-right:4px;}
.ca-players{font-size:13px;color:var(--good);}
.ca-refusal{background:var(--warn-soft);border:1px solid var(--warn-line);color:var(--warn-txt);border-radius:12px;
  padding:9px 12px;font-size:13px;}
.ca-refusal details{margin-top:4px;color:var(--txt);}
.ca-refusal summary{cursor:pointer;color:var(--warn);}
.ca-fits{color:var(--good);font-size:13px;}
.ca-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:2px;}
.ca-btn{display:inline-flex;align-items:center;gap:6px;background:var(--card2);color:var(--txt);border:1px solid var(--line2);
  border-radius:999px;padding:6px 14px;font:inherit;font-size:13px;cursor:pointer;text-decoration:none;
  transition:border-color .12s ease,color .12s ease,background .12s ease;}
.ca-btn:hover:not([disabled]){background:var(--accSoft);border-color:var(--accLine);color:var(--accText);}
.ca-btn[disabled]{opacity:.45;cursor:not-allowed;}
.ca-btn[aria-pressed="true"]{background:var(--accSoft);border-color:var(--accLine);color:var(--accText);font-weight:650;}
.ca-btn:focus-visible{outline:2px solid var(--accLine);outline-offset:2px;}
.ca-btn.ca-primary{background:var(--grad);border-color:transparent;color:var(--ink);font-weight:650;}
.ca-btn.ca-primary:hover:not([disabled]){background:var(--grad);color:var(--ink);filter:brightness(1.08);}
.ca-btn.ca-danger{border-color:var(--bad-line);color:var(--bad);}
.ca-btn.ca-danger:hover:not([disabled]){background:var(--bad-soft);color:var(--bad);border-color:var(--bad);}
.ca-ranges{display:flex;gap:6px;flex-wrap:wrap;}
.ca-chart{position:relative;height:340px;margin:10px 0 6px;}
.ca-files{display:flex;flex-direction:column;gap:10px;}
.ca-crumbs{color:var(--mut);font-size:13px;}
.ca-files-split{display:grid;grid-template-columns:minmax(300px,2fr) 3fr;gap:14px;align-items:start;}
.ca-files-list{max-height:calc(100vh - 290px);border:1px solid var(--line);border-radius:12px;}
.ca-files-row,.ca-files-list tr[data-dir]{cursor:pointer;}
.ca-files-list tr:hover td{background:var(--accSoft);}
.ca-files-list td:nth-child(n+2){white-space:nowrap;color:var(--mut);}
.ca-files-list tr.ca-picked td{background:color-mix(in srgb,var(--acc) 22%,transparent);}
.ca-files-view{display:flex;flex-direction:column;gap:8px;min-width:0;}
.ca-files-view h2{margin:0;font-size:15px;word-break:break-all;}
.ca-files-text{width:100%;box-sizing:border-box;min-height:calc(100vh - 380px);background:var(--code);color:var(--txt);
  border:1px solid var(--line);border-radius:12px;padding:12px;font:12.5px/1.45 var(--mono);
  white-space:pre;overflow:auto;resize:vertical;}
.ca-files-text:focus{outline:none;border-color:var(--accLine);box-shadow:0 0 0 3px var(--accSoft);}
.ca-files-text[readonly]{color:var(--txt2);}
@media (max-width:900px){.ca-files-split{grid-template-columns:1fr;}.ca-files-text{min-height:50vh;}}
.ca-badge{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;padding:3px 10px;
  border-radius:999px;border:1px solid;white-space:nowrap;}
.ca-badge[data-kind=ok]{color:var(--good);border-color:var(--good-line);background:var(--good-soft);}
.ca-badge[data-kind=warn]{color:var(--warn);border-color:var(--warn-line);background:var(--warn-soft);}
.ca-badge[data-kind=off]{color:var(--txt2);border-color:var(--line2);background:var(--card2);}
.ca-table-wrap{padding:0;overflow:auto;}
.ca-table{width:100%;border-collapse:collapse;font-size:13px;}
.ca-table th{text-align:left;color:var(--mut);font-weight:600;font-size:11.5px;text-transform:uppercase;letter-spacing:.06em;
  padding:9px 12px;border-bottom:1px solid var(--line2);position:sticky;top:0;background:var(--card);}
.ca-table td{padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top;}
.ca-table tr[data-job]{cursor:pointer;}
.ca-table tr[data-job]:hover td{background:var(--accSoft);}
.ca-summary{max-width:520px;color:var(--txt2);}
.ca-log{background:var(--code);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin:0;color:var(--txt);
  font:12.5px/1.45 var(--mono);white-space:pre-wrap;max-height:calc(100vh - 220px);overflow:auto;}
.ca-form{display:flex;flex-direction:column;gap:14px;max-width:760px;}
.ca-field{display:grid;grid-template-columns:220px 1fr;gap:4px 16px;align-items:center;}
.ca-field-label{font-weight:550;}
.ca-field-top{align-items:start;}
.ca-field-top .ca-field-label{padding-top:6px;}
.ca-field-input{display:flex;align-items:center;gap:8px;}
/* Every text-like input and select the app draws itself. */
.ca-field input,.ca-field select,.ca-mod-add input,.ca-modadd select,.ca-modadd input,.ca-stack-filter,.ca-stack-in,
.ca-msg-row input:not([type]),.ca-sched input[type=time],.ca-sched input[type=number],.ca-sched input:not([type]){
  background:var(--field);color:var(--txt);border:1px solid var(--line2);border-radius:10px;padding:7px 11px;font:inherit;}
.ca-field input:focus,.ca-field select:focus,.ca-mod-add input:focus,.ca-modadd select:focus,.ca-modadd input:focus,
.ca-stack-filter:focus,.ca-stack-in:focus,.ca-msg-row input:focus,.ca-sched input:focus{
  outline:none;border-color:var(--accLine);box-shadow:0 0 0 3px var(--accSoft);}
.ca-field input,.ca-field select{flex:1;min-width:0;width:0;}
.ca-unit{color:var(--mut);min-width:28px;}
.ca-help{grid-column:2;color:var(--mut);font-size:12px;}
.ca-plain{color:inherit !important;text-decoration:none;}
.ca-plain:hover{text-decoration:underline;}
.ca-chips{display:flex;gap:6px;flex-wrap:wrap;}
.ca-chips:empty{display:none;}
.ca-chip{font-size:11.5px;padding:2px 9px;border-radius:999px;border:1px solid var(--line2);background:var(--card2);color:var(--txt2);}
.ca-chip-warn{border-color:var(--warn-line);background:var(--warn-soft);color:var(--warn-txt);}
.ca-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px;align-items:start;}
.ca-two .ca-form{max-width:none;}
.ca-status-head{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px;}
.ca-note{color:var(--txt2);font-size:13px;margin-top:6px;}
.ca-pad{padding:14px 16px 4px;}
.ca-list{margin:8px 0 0;padding-left:18px;}
.ca-mods{display:flex;flex-direction:column;gap:8px;}
.ca-mod-list{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:4px;}
.ca-mod-list li{display:flex;align-items:center;gap:8px;background:var(--field);border:1px solid var(--line);border-radius:10px;padding:4px 10px;}
.ca-mod-n{color:var(--mut);width:22px;font-size:12px;}
.ca-mod-btns{margin-left:auto;display:flex;gap:2px;}
.ca-icon{background:none;border:0;color:var(--mut);cursor:pointer;padding:2px 7px;border-radius:999px;font-size:11px;}
.ca-icon:hover{background:var(--accSoft);color:var(--txt);}
.ca-mod-add{display:flex;gap:8px;}
.ca-mod-add input{flex:1;min-width:0;}
.ca-tabs{display:flex;gap:4px;border-bottom:1px solid var(--line2);flex-wrap:wrap;}
.ca-tab{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;color:var(--mut);text-decoration:none;
  border-bottom:2px solid transparent;margin-bottom:-1px;font-weight:550;border-radius:9px 9px 0 0;}
.ca-view a.ca-tab{color:var(--mut);}
.ca-view a.ca-tab:hover{color:var(--txt);background:var(--accSoft);}
.ca-view a.ca-tab-on{color:var(--txt);border-bottom-color:var(--acc);}
#ca-cluster-body,#ca-game,#ca-mods-tab{display:flex;flex-direction:column;gap:16px;}
.ca-presets{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:10px;}
.ca-preset,.ca-mapchoice{display:flex;flex-direction:column;gap:4px;text-align:left;background:var(--card2);border:1px solid var(--line2);
  border-radius:12px;padding:12px 14px;color:var(--txt);font:inherit;cursor:pointer;
  transition:border-color .15s ease,transform .15s ease,background .15s ease;}
.ca-preset:hover,.ca-mapchoice:hover:not([disabled]){border-color:var(--accLine);background:var(--accSoft);transform:translateY(-1px);}
.ca-preset b{font-size:15px;}
.ca-preset span,.ca-mapchoice span{color:var(--mut);font-size:12.5px;}
.ca-game-bar{position:sticky;top:0;z-index:5;background:var(--bg);padding:8px 0;display:flex;flex-direction:column;gap:6px;}
.ca-jump{display:flex;gap:6px;flex-wrap:wrap;}
.ca-view .ca-jump a{font-size:12.5px;color:var(--txt2);text-decoration:none;border:1px solid var(--line2);border-radius:999px;padding:2px 11px;}
.ca-view .ca-jump a:hover{border-color:var(--accLine);background:var(--accSoft);color:var(--accText);}
.ca-game-form .wapyt-form-fieldset{margin-bottom:10px;}
.ca-more{margin-top:4px;}
.ca-more summary{cursor:pointer;color:var(--accText);margin-bottom:10px;}
.ca-mapcards{display:flex;flex-direction:column;gap:8px;margin-top:10px;}
.ca-mapcard{display:flex;flex-direction:column;gap:2px;background:var(--card2);border:1px solid var(--line);border-radius:12px;
  padding:10px 12px;color:var(--txt) !important;text-decoration:none;}
.ca-mapcard:hover{border-color:var(--accLine);background:var(--accSoft);}
.ca-modlist{list-style:none;margin:12px 0;padding:0;display:flex;flex-direction:column;gap:8px;}
.ca-modcard{display:flex;align-items:center;gap:12px;background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:10px 12px;}
.ca-modnum,.ca-qpos{width:28px;height:28px;border-radius:9px;background:var(--accSoft);display:flex;align-items:center;
  justify-content:center;font-weight:700;color:var(--accText);flex:none;}
.ca-modbody{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;}
.ca-modname{background:transparent;border:1px solid transparent;border-radius:8px;color:var(--txt);font:inherit;font-weight:650;
  font-size:15px;padding:3px 6px;margin-left:-6px;}
.ca-modname:hover,.ca-modname:focus{border-color:var(--line2);background:var(--field);outline:none;}
.ca-modbtns{display:flex;gap:2px;}
.ca-modadd{display:flex;gap:8px;flex-wrap:wrap;}
.ca-modadd select{min-width:280px;}
.ca-modadd input{flex:1;min-width:180px;}
.ca-override{display:grid;grid-template-columns:1fr 2fr auto auto;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid var(--line);}
.ca-override input[type=range]{width:100%;accent-color:var(--acc);}
.ca-override output{min-width:56px;text-align:right;font-variant-numeric:tabular-nums;}
.ca-queue{list-style:none;margin:10px 0 0;padding:0;display:flex;flex-direction:column;gap:8px;}
.ca-qrow{display:flex;align-items:center;gap:12px;background:var(--card2);border:1px solid var(--line);border-radius:12px;padding:10px 12px;}
.ca-qrow[data-state=running]{border-color:var(--warn-line);}
.ca-qrow[data-state=running] .ca-qpos{background:var(--warn-soft);color:var(--warn);}
.ca-qbody{flex:1;min-width:0;display:flex;flex-direction:column;gap:4px;}
.ca-qbtns{display:flex;gap:6px;}
.ca-cluster-head .ca-btn{padding:4px 12px;margin-right:6px;}
#ca-backups{display:flex;flex-direction:column;gap:16px;}
.ca-sched{display:flex;flex-direction:column;gap:10px;}
.ca-sched-row{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:var(--txt2);}
.ca-sched input[type=number]{width:70px;}
.ca-sched input[name=warn]{width:110px;}
.ca-sched input[name=message]{width:220px;}
.ca-days{display:flex;gap:4px;flex-wrap:wrap;}
.ca-day input{display:none;}
.ca-day span{display:inline-block;padding:3px 10px;border:1px solid var(--line2);border-radius:999px;cursor:pointer;
  font-size:12.5px;color:var(--mut);}
.ca-day input:checked+span{background:var(--grad);border-color:transparent;color:var(--ink);font-weight:650;}
.ca-switch-big{display:inline-flex;align-items:center;gap:8px;cursor:pointer;font-weight:650;}
.ca-switch-big input{width:18px;height:18px;accent-color:var(--acc);}
.ca-map-h{margin:16px 0 6px;font-size:15px;}
.ca-star{font-size:18px;color:var(--mut);}
.ca-star.ca-on{color:var(--warn);}
.ca-row-btns{display:flex;gap:6px;justify-content:flex-end;}
.ca-row-btns .ca-btn{padding:3px 11px;}
.ca-map-body{display:flex;flex-direction:column;gap:16px;}
.ca-page-head #ca-map-badge:empty{display:none;}
.ca-msg-row{display:flex;gap:10px;align-items:center;flex-wrap:wrap;}
.ca-msg-row input:not([type]){flex:1;min-width:240px;padding:8px 12px;}
.ca-msg-row label{display:inline-flex;gap:6px;align-items:center;}
.ca-gap{flex:1;}
.ca-console{display:flex;flex-direction:column;gap:10px;}
.ca-con-log{height:calc(100vh - 360px);min-height:280px;max-height:none;white-space:pre-wrap;word-break:break-word;}
.ca-con-log div{min-height:1.2em;}
.ca-con-line{color:var(--txt2);}
.ca-con-warn{color:var(--warn);}
.ca-con-errline,.ca-con-err{color:var(--bad);}
.ca-con-good{color:var(--good);}
.ca-con-cmd{color:var(--txt);font-weight:700;margin-top:4px;}
.ca-con-reply{color:var(--accText);}
.ca-con-note{color:var(--mut);font-style:italic;}
.ca-con-input{display:flex;gap:8px;align-items:center;background:var(--code);border:1px solid var(--line2);border-radius:12px;padding:4px 6px 4px 12px;}
.ca-con-input:focus-within{border-color:var(--accLine);box-shadow:0 0 0 3px var(--accSoft);}
.ca-con-prompt{color:var(--acc);font:600 14px var(--mono);}
.ca-con-input input{flex:1;background:transparent;border:0;color:var(--txt);font:13px var(--mono);padding:6px 0;outline:none;}
#ca-add{display:flex;flex-direction:column;gap:16px;}
.ca-mapchoices{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:10px;margin:10px 0 14px;}
.ca-mapchoice[disabled]{opacity:.45;cursor:not-allowed;}
.ca-mapchoice.ca-picked{border-color:var(--acc);background:var(--accSoft);box-shadow:0 0 0 2px var(--accLine);}
.ca-radios{display:flex;flex-direction:column;gap:8px;margin-top:8px;}
.ca-radio{display:flex;gap:8px;align-items:center;color:var(--txt);}
.ca-radio input{accent-color:var(--acc);}
#ca-stacks{display:flex;flex-direction:column;gap:10px;}
.ca-stack-filter{padding:5px 11px;}
.ca-stacks-table td{vertical-align:middle;}
.ca-stack-cat th{background:var(--card2) !important;color:var(--accText);font-weight:650;padding-top:14px;}
.ca-stack-in{width:120px;padding:4px 9px;}
.ca-stack-in:not(:placeholder-shown){border-color:var(--accLine);}
@media (prefers-reduced-motion:reduce){.ca-server,.ca-btn,.ca-preset,.ca-mapchoice{transition:none;}
  .ca-server:hover,.ca-preset:hover,.ca-mapchoice:hover:not([disabled]){transform:none;}}
@media (max-width:700px){.ca-field{grid-template-columns:1fr;}.ca-help{grid-column:1;}.ca-page{padding:12px;}
  .ca-themes{right:8px;left:8px;width:auto;}}
"""


def _inject_css() -> None:
    if js.document.getElementById("ca-css"):
        return
    style = js.document.createElement("style")
    style.id = "ca-css"
    style.textContent = _CSS
    js.document.head.appendChild(style)
