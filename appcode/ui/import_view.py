"""
Import an existing podman-compose cluster. Preview first; importing writes
the app's database only (the compose file is copied, containers untouched).
"""
from __future__ import annotations

import js

from services.import_api import ImportApi
from ui.common import Listeners, closest, esc, failed, spawn, toast

DEFAULT_PATH = "/home/oldmangan/asa-cluster/docker-compose.yaml"


class ImportView:
    def __init__(self, container) -> None:
        self.el = container
        self.listeners = Listeners()

    def mount(self) -> None:
        self.el.innerHTML = f"""
<div class="ca-page" id="ca-import">
  <div class="ca-page-head"><h1>Import a cluster</h1>
    <span class="ca-sub">From a podman-compose file of POK (acekorneya/asa_server) services that share one ClusterId.</span></div>
  <div class="ca-card ca-form">
    <label class="ca-field"><span class="ca-field-label">Compose file</span>
      <span class="ca-field-input"><input name="path" value="{esc(DEFAULT_PATH)}" spellcheck="false"></span></label>
    <label class="ca-field"><span class="ca-field-label">Cluster name</span>
      <span class="ca-field-input"><input name="name" placeholder="defaults to the ClusterId"></span></label>
    <div class="ca-actions">
      <button class="ca-btn" data-act="preview"><span class="mdi mdi-eye-outline"></span> Preview</button>
      <button class="ca-btn ca-primary" data-act="apply" disabled><span class="mdi mdi-import"></span> Import</button>
    </div>
  </div>
  <div id="ca-import-plan"></div>
</div>"""
        self.listeners.on(self.el, "click", self._on_click)

    def unmount(self) -> None:
        self.listeners.clear()

    def _values(self):
        path = js.document.querySelector('#ca-import input[name="path"]')
        name = js.document.querySelector('#ca-import input[name="name"]')
        return (str(path.value).strip() if path else "", str(name.value).strip() if name else "")

    def _on_click(self, event) -> None:
        btn = closest(event.target, "[data-act]")
        if not btn or btn.disabled:
            return
        act = btn.getAttribute("data-act")
        if act == "preview":
            spawn(self.preview(), "import preview")
        elif act == "apply":
            spawn(self.apply(), "import apply")

    async def preview(self) -> None:
        path, name = self._values()
        result = await ImportApi().preview_async(path, name)
        plan = result.get("plan") or {}
        out = js.document.getElementById("ca-import-plan")
        apply_btn = js.document.querySelector('#ca-import [data-act="apply"]')
        if apply_btn:
            apply_btn.disabled = not result.get("ok")
        if not out:
            return
        parts = []
        for e in plan.get("errors", []):
            parts.append(f'<div class="ca-banner" data-kind="error">{esc(e)}</div>')
        for w in plan.get("warnings", []):
            parts.append(f'<div class="ca-banner" data-kind="warning">{esc(w)}</div>')
        c = plan.get("cluster")
        if c:
            d = c["defaults"]
            parts.append(
                f'<div class="ca-card"><h2>Cluster {esc(c["name"])}</h2>'
                f'<div class="ca-sub">ClusterId <code>{esc(c["cluster_id"])}</code> · transfer folder '
                f'<code>{esc(c["transfer_dir"])}</code> · root <code>{esc(c["root_dir"])}</code></div>'
                f'<div class="ca-sub">Shared: {len(d.get("mod_ids") or [])} mods ({esc(", ".join(d.get("mod_ids") or []))}) · '
                f'args <code>{esc(" ".join(d.get("custom_args") or []))}</code> · BattlEye '
                f'{"on" if d.get("battleye") else "off"} · max players {esc(d.get("max_players"))}</div></div>')
            rows = []
            for s in plan.get("servers", []):
                over = s.get("overrides") or {}
                own = ", ".join(f"{k}={v}" for k, v in over.items()) or "none"
                rows.append(f"<tr><td>{s['priority']}</td><td>{esc(s['name'])}</td><td>{esc(s['map_name'])}</td>"
                            f"<td>{esc(s['session_name'])}</td><td>{s['game_port']} / {s['rcon_port']}</td>"
                            f"<td><code>{esc(s['container_name'])}</code></td><td class='ca-summary'>{esc(own)}</td></tr>")
            parts.append('<div class="ca-card ca-table-wrap"><table class="ca-table"><thead><tr><th>Priority</th>'
                         "<th>Map</th><th>MAP_NAME</th><th>Session</th><th>Game / RCON</th><th>Container</th>"
                         f'<th>Own settings</th></tr></thead><tbody>{"".join(rows)}</tbody></table></div>')
        out.innerHTML = "".join(parts)

    async def apply(self) -> None:
        path, name = self._values()
        result = await ImportApi().apply_async(path, name)
        if failed(result, "import"):
            return
        toast(f"Imported {len(result['servers'])} maps into {result['cluster']}.", kind="success")
        js.window.location.hash = "#/"
