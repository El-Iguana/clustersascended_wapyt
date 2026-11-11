"""
Clusters Ascended — the browser UI. Runs in Pyodide.

Conventions from the sibling wapyt apps:

* BFF calls are ``await X().method_async(...)``.
* The UI is built in ``load_ui``, never ``__init__``.
* Import *names* from service modules, never ``from services import x``.
* DOM misses come back as ``JsNull``, which is falsy but not ``None``.

Routes (URL hash): ``#/`` dashboard, ``#/cluster/<id>``, ``#/map/<id>``,
``#/jobs[/<id>]``, ``#/host``, ``#/import``.
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
from ui.common import APPLICATION, Listeners, by_id, spawn
from ui.dashboard import DashboardView
from ui.host import HostView
from ui.import_view import ImportView
from ui.jobs import JobsView
from ui.server_view import ServerView

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
        self.set_theme("dark")
        js.document.title = "Clusters Ascended"
        self._view = None
        self._window_listeners = Listeners()
        _inject_css()
        self._build_chrome()
        self._window_listeners.on(js.window, "hashchange", lambda _e: self._route())
        self._route()

    def _build_chrome(self) -> None:
        self.toolbar = self.add_toolbar(
            "mainwindow_header",
            ToolbarConfig(
                label="Clusters Ascended",
                items=[
                    ToolbarButton("dashboard", "Dashboard", "mdi-view-dashboard-outline", group="nav"),
                    ToolbarButton("jobs", "Jobs", "mdi-format-list-checks", group="nav"),
                    ToolbarButton("host", "Host rules", "mdi-memory", group="nav"),
                    ToolbarButton("import", "Import", "mdi-import", group="nav"),
                    ToolbarSpacer(),
                    ToolbarText("user", "admin"),
                    ToolbarSeparator(),
                    ToolbarButton("logout", "Sign out", "mdi-logout"),
                ],
            ),
        )
        self.toolbar.on_click(self._on_toolbar_click)
        body = self.add_layout("mainwindow", LayoutConfig(type="line", rows=[CellConfig(id="main", height="100%")]))
        cell = body.get_cell("main")
        main = cell.getContainer() if hasattr(cell, "getContainer") else cell
        main.innerHTML = '<div class="ca-view" id="ca-view"></div>'

    def _on_toolbar_click(self, payload: dict) -> None:
        item = payload.get("id")
        if item == "logout":
            spawn(self._logout(), "sign out")
        elif item in ("dashboard", "jobs", "host", "import"):
            js.window.location.hash = "#/" if item == "dashboard" else f"#/{item}"

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
        elif page == "import":
            self._view = ImportView(container)
        elif page == "map" and len(parts) > 1:
            self._view = ServerView(container, parts[1])
        elif page == "cluster" and len(parts) > 1:
            self._view = ClusterView(container, parts[1], parts[2] if len(parts) > 2 else "")
        else:
            page = "dashboard"
            self._view = DashboardView(container)
        nav = {"map": "dashboard", "cluster": "dashboard"}.get(page, page)
        try:
            self.toolbar.set_active(nav, True)
        except Exception:  # noqa: BLE001 - cosmetic
            pass
        self._view.mount()


_CSS = """
.ca-view{height:100%;overflow:auto;background:#0b1020;color:#e5e7eb;
  font:14px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;box-sizing:border-box;}
.ca-view a{color:#93c5fd;}
.ca-view code{background:#111a2e;border:1px solid #1e293b;border-radius:4px;padding:0 4px;font-size:12px;}
.ca-page{padding:20px 24px;display:flex;flex-direction:column;gap:16px;max-width:1500px;margin:0 auto;}
.ca-page-head{display:flex;align-items:center;gap:14px;flex-wrap:wrap;}
.ca-page-head h1{margin:0;font-size:20px;font-weight:600;}
.ca-back{color:#9ca3af;text-decoration:none;font-size:20px;padding:2px 8px;border-radius:6px;}
.ca-back:hover{background:#1f2937;color:#e5e7eb;}
.ca-card{background:#111827;border:1px solid #1f2937;border-radius:10px;padding:16px 18px;}
.ca-card h2,.ca-cluster h2{margin:0;font-size:16px;font-weight:600;}
.ca-sub{color:#94a3b8;font-size:12.5px;}
.ca-empty{color:#94a3b8;padding:20px;text-align:center;}
.ca-warn{color:#fbbf24;}
.ca-banner{display:flex;gap:8px;align-items:flex-start;padding:10px 14px;border-radius:8px;border:1px solid;}
.ca-banner[data-kind=error]{background:#2a0b0b;border-color:#b91c1c;color:#fecaca;}
.ca-banner[data-kind=warning]{background:#2a1a04;border-color:#92400e;color:#fcd34d;}
.ca-host{display:flex;flex-direction:column;gap:10px;}
.ca-host-head{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;}
.ca-bar{position:relative;display:flex;height:26px;background:#0b1222;border:1px solid #1e293b;
  border-radius:6px;overflow:visible;}
.ca-seg{height:100%;opacity:.9;}
.ca-seg:first-child{border-radius:5px 0 0 5px;}
.ca-seg-ext{background:repeating-linear-gradient(45deg,#475569 0 6px,#334155 6px 12px);}
.ca-mark{position:absolute;top:-6px;bottom:-6px;width:0;border-left:2px dashed #94a3b8;}
.ca-mark span{position:absolute;top:100%;left:4px;font-size:11px;color:#94a3b8;white-space:nowrap;}
.ca-mark-budget{border-left:2px solid #f87171;}
.ca-mark-budget span{color:#f87171;left:auto;right:4px;}
.ca-host .ca-legend{margin-top:8px;}
.ca-legend{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px;color:#cbd5e1;}
.ca-legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:-1px;}
.ca-legend i.ca-ext{background:#475569;}
.ca-cluster{display:flex;flex-direction:column;gap:10px;}
.ca-cluster-head{display:flex;justify-content:space-between;align-items:flex-end;gap:12px;flex-wrap:wrap;}
.ca-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:14px;}
.ca-server{background:#111827;border:1px solid #1f2937;border-left:4px solid var(--ca-colour);
  border-radius:10px;padding:14px 16px;display:flex;flex-direction:column;gap:8px;}
.ca-server header{display:flex;justify-content:space-between;gap:10px;align-items:flex-start;}
.ca-server h3{margin:0;font-size:16px;}
.ca-stats{display:flex;gap:20px;flex-wrap:wrap;}
.ca-k{color:#94a3b8;font-size:12px;margin-right:4px;}
.ca-players{font-size:13px;color:#a7f3d0;}
.ca-refusal{background:#1f1206;border:1px solid #92400e;color:#fcd34d;border-radius:8px;padding:8px 10px;font-size:13px;}
.ca-refusal details{margin-top:4px;color:#e5e7eb;}
.ca-refusal summary{cursor:pointer;color:#fbbf24;}
.ca-fits{color:#86efac;font-size:13px;}
.ca-actions{display:flex;gap:8px;flex-wrap:wrap;margin-top:2px;}
.ca-btn{display:inline-flex;align-items:center;gap:6px;background:#1f2937;color:#e5e7eb;border:1px solid #334155;
  border-radius:7px;padding:6px 12px;font:inherit;font-size:13px;cursor:pointer;text-decoration:none;}
.ca-btn:hover{background:#273449;}
.ca-btn[disabled]{opacity:.45;cursor:not-allowed;}
.ca-btn.ca-primary{background:#2563eb;border-color:#2563eb;color:#fff;}
.ca-btn.ca-primary:hover{background:#1d4ed8;}
.ca-btn.ca-danger{border-color:#7f1d1d;color:#fca5a5;}
.ca-btn.ca-danger:hover{background:#2a0a0a;}
.ca-badge{font-size:12px;font-weight:600;padding:2px 10px;border-radius:999px;border:1px solid;white-space:nowrap;}
.ca-badge[data-kind=ok]{color:#4ade80;border-color:#166534;background:#052e16;}
.ca-badge[data-kind=warn]{color:#fbbf24;border-color:#92400e;background:#2a1a04;}
.ca-badge[data-kind=off]{color:#cbd5e1;border-color:#334155;background:#111827;}
.ca-table-wrap{padding:0;overflow:auto;}
.ca-table{width:100%;border-collapse:collapse;font-size:13px;}
.ca-table th{text-align:left;color:#94a3b8;font-weight:500;padding:8px 12px;border-bottom:1px solid #1f2937;
  position:sticky;top:0;background:#111827;}
.ca-table td{padding:7px 12px;border-bottom:1px solid #172033;vertical-align:top;}
.ca-table tr[data-job]{cursor:pointer;}
.ca-table tr[data-job]:hover td{background:#152036;}
.ca-summary{max-width:520px;color:#cbd5e1;}
.ca-log{background:#05080f;border:1px solid #1f2937;border-radius:8px;padding:12px 14px;margin:0;
  font:12.5px/1.45 ui-monospace,Menlo,Consolas,monospace;white-space:pre-wrap;max-height:calc(100vh - 220px);overflow:auto;}
.ca-form{display:flex;flex-direction:column;gap:14px;max-width:760px;}
.ca-field{display:grid;grid-template-columns:220px 1fr;gap:4px 16px;align-items:center;}
.ca-field-label{font-weight:500;}
.ca-field-top{align-items:start;}
.ca-field-top .ca-field-label{padding-top:6px;}
.ca-field-input{display:flex;align-items:center;gap:8px;}
.ca-field input{flex:1;min-width:0;width:0;background:#0b1222;color:#e5e7eb;border:1px solid #334155;border-radius:6px;
  padding:6px 10px;font:inherit;}
.ca-field input:focus{outline:none;border-color:#38bdf8;}
.ca-unit{color:#94a3b8;min-width:28px;}
.ca-help{grid-column:2;color:#94a3b8;font-size:12px;}
.ca-plain{color:inherit !important;text-decoration:none;}
.ca-plain:hover{text-decoration:underline;}
.ca-chips{display:flex;gap:6px;flex-wrap:wrap;}
.ca-chips:empty{display:none;}
.ca-chip{font-size:11.5px;padding:1px 8px;border-radius:999px;border:1px solid #334155;color:#cbd5e1;}
.ca-chip-warn{border-color:#92400e;color:#fcd34d;}
.ca-two{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:16px;align-items:start;}
.ca-two .ca-form{max-width:none;}
.ca-status-head{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:8px;}
.ca-note{color:#cbd5e1;font-size:13px;margin-top:6px;}
.ca-pad{padding:14px 16px 4px;}
.ca-list{margin:8px 0 0;padding-left:18px;}
.ca-field select{flex:1;min-width:0;background:#0b1222;color:#e5e7eb;border:1px solid #334155;border-radius:6px;padding:6px 8px;font:inherit;}
.ca-mods{display:flex;flex-direction:column;gap:8px;}
.ca-mod-list{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:4px;}
.ca-mod-list li{display:flex;align-items:center;gap:8px;background:#0b1222;border:1px solid #1e293b;border-radius:6px;padding:3px 8px;}
.ca-mod-n{color:#64748b;width:22px;font-size:12px;}
.ca-mod-btns{margin-left:auto;display:flex;gap:2px;}
.ca-icon{background:none;border:0;color:#94a3b8;cursor:pointer;padding:2px 6px;border-radius:4px;font-size:11px;}
.ca-icon:hover{background:#1f2937;color:#e5e7eb;}
.ca-mod-add{display:flex;gap:8px;}
.ca-mod-add input{flex:1;min-width:0;background:#0b1222;color:#e5e7eb;border:1px solid #334155;border-radius:6px;padding:6px 10px;font:inherit;}
.ca-tabs{display:flex;gap:4px;border-bottom:1px solid #1f2937;flex-wrap:wrap;}
.ca-tab{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;color:#94a3b8;text-decoration:none;
  border-bottom:2px solid transparent;margin-bottom:-1px;font-weight:500;}
.ca-tab:hover{color:#e5e7eb;}
.ca-tab-on{color:#e5e7eb;border-bottom-color:#38bdf8;}
#ca-cluster-body,#ca-game,#ca-mods-tab{display:flex;flex-direction:column;gap:16px;}
.ca-presets{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:10px;margin-top:10px;}
.ca-preset{display:flex;flex-direction:column;gap:4px;text-align:left;background:#0b1222;border:1px solid #334155;
  border-radius:10px;padding:12px 14px;color:#e5e7eb;font:inherit;cursor:pointer;}
.ca-preset:hover{border-color:#38bdf8;background:#0f1a30;}
.ca-preset b{font-size:15px;}
.ca-preset span{color:#94a3b8;font-size:12.5px;}
.ca-game-bar{position:sticky;top:0;z-index:5;background:#0b1020;padding:8px 0;display:flex;flex-direction:column;gap:6px;}
.ca-jump{display:flex;gap:6px;flex-wrap:wrap;}
.ca-jump a{font-size:12.5px;color:#cbd5e1;text-decoration:none;border:1px solid #334155;border-radius:999px;padding:2px 10px;}
.ca-jump a:hover{border-color:#38bdf8;color:#fff;}
.ca-game-form .wapyt-form-fieldset{margin-bottom:10px;}
.ca-more{margin-top:4px;}
.ca-more summary{cursor:pointer;color:#93c5fd;margin-bottom:10px;}
.ca-mapcards{display:flex;flex-direction:column;gap:8px;margin-top:10px;}
.ca-mapcard{display:flex;flex-direction:column;gap:2px;background:#0b1222;border:1px solid #1e293b;border-radius:8px;
  padding:10px 12px;color:#e5e7eb !important;text-decoration:none;}
.ca-mapcard:hover{border-color:#38bdf8;}
.ca-modlist{list-style:none;margin:12px 0;padding:0;display:flex;flex-direction:column;gap:8px;}
.ca-modcard{display:flex;align-items:center;gap:12px;background:#0b1222;border:1px solid #1e293b;border-radius:10px;padding:10px 12px;}
.ca-modnum{width:28px;height:28px;border-radius:50%;background:#1e293b;display:flex;align-items:center;justify-content:center;
  font-weight:600;color:#93c5fd;flex:none;}
.ca-modbody{flex:1;min-width:0;display:flex;flex-direction:column;gap:2px;}
.ca-modname{background:transparent;border:1px solid transparent;border-radius:6px;color:#e5e7eb;font:inherit;font-weight:600;
  font-size:15px;padding:3px 6px;margin-left:-6px;}
.ca-modname:hover,.ca-modname:focus{border-color:#334155;background:#0b1020;outline:none;}
.ca-modbtns{display:flex;gap:2px;}
.ca-modadd{display:flex;gap:8px;flex-wrap:wrap;}
.ca-modadd select{min-width:280px;background:#0b1222;color:#e5e7eb;border:1px solid #334155;border-radius:6px;padding:6px 10px;font:inherit;}
.ca-modadd input{flex:1;min-width:180px;background:#0b1222;color:#e5e7eb;border:1px solid #334155;border-radius:6px;padding:6px 10px;font:inherit;}
.ca-override{display:grid;grid-template-columns:1fr 2fr auto auto;gap:10px;align-items:center;padding:6px 0;border-bottom:1px solid #172033;}
.ca-override input[type=range]{width:100%;}
.ca-override output{min-width:56px;text-align:right;font-variant-numeric:tabular-nums;}
@media (max-width:700px){.ca-field{grid-template-columns:1fr;}.ca-help{grid-column:1;}.ca-page{padding:12px;}}
"""


def _inject_css() -> None:
    if js.document.getElementById("ca-css"):
        return
    style = js.document.createElement("style")
    style.id = "ca-css"
    style.textContent = _CSS
    js.document.head.appendChild(style)
