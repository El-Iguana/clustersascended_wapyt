"""
A map's Files tab (``#/map/<id>/files``): browse its world and settings, its
game files and the cluster's transfers; read any text file; change small
config files in the world folder while the map is stopped.
"""
from __future__ import annotations

import js

from wapyt import message

from services.files_api import FilesApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast


def _size(n) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return ""


def _when(ts) -> str:
    if not ts:
        return "–"
    opts = js.Object.new()
    opts.dateStyle, opts.timeStyle = "short", "short"
    return str(js.Date.new(float(ts) * 1000).toLocaleString(js.undefined, opts))


class FilesTab:
    def __init__(self, container, server_id: str) -> None:
        self.el = container
        self.server_id = server_id
        self.listeners = Listeners()
        self.root = "saved"
        self.path = ""
        self.file: dict = {}

    def mount(self) -> None:
        self.el.innerHTML = """
<section class="ca-card ca-files">
  <div class="ca-host-head"><div class="ca-ranges" id="ca-files-roots"></div>
    <button class="ca-btn" data-act="refresh"><span class="mdi mdi-refresh"></span> Refresh</button></div>
  <nav class="ca-crumbs" id="ca-files-crumbs"></nav>
  <div class="ca-files-split">
    <div class="ca-table-wrap ca-files-list" id="ca-files-list"><div class="ca-empty">Loading…</div></div>
    <div class="ca-files-view" id="ca-files-view"><div class="ca-empty">Pick a file to see it.</div></div>
  </div>
</section>"""
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.on(self.el, "input", self._on_input)
        spawn(self.load(), "files load")

    def unmount(self) -> None:
        self.listeners.clear()

    # -- folder ----------------------------------------------------------------

    async def load(self) -> None:
        d = await FilesApi().listing_async(self.server_id, self.root, self.path)
        if failed(d, "open the folder"):
            if self.path:                       # a folder that went away: back to the top
                self.path = ""
                await self.load()
            return
        roots = js.document.getElementById("ca-files-roots")
        if roots:
            roots.innerHTML = "".join(
                f'<button class="ca-btn" data-root="{esc(r["key"])}" aria-pressed="{"true" if r["key"] == self.root else "false"}">'
                f'{esc(r["label"])}</button>' for r in d["roots"])
        crumbs = js.document.getElementById("ca-files-crumbs")
        if crumbs:
            top = next((r["label"] for r in d["roots"] if r["key"] == self.root), "")
            crumbs.innerHTML = (f'<a href="#" data-dir="">{esc(top)}</a>' + "".join(
                f' / <a href="#" data-dir="{esc(c["path"])}">{esc(c["name"])}</a>' for c in d["crumbs"])
                + ("" if d["writable"] else ' <span class="ca-badge" data-kind="off">read-only</span>'))
        rows = []
        if d["path"]:
            parent = "/".join(d["path"].split("/")[:-1])
            rows.append(f'<tr data-dir="{esc(parent)}"><td><span class="mdi mdi-arrow-up"></span> ..</td><td></td><td></td></tr>')
        for e in d["entries"]:
            full = f'{d["path"]}/{e["name"]}' if d["path"] else e["name"]
            icon = "mdi-folder-outline" if e["dir"] else ("mdi-file-document-outline" if e["text"] else "mdi-file-outline")
            attr_ = f'data-dir="{esc(full)}"' if e["dir"] else f'data-file="{esc(full)}"'
            picked = " ca-picked" if not e["dir"] and full == self.file.get("path") else ""
            rows.append(f'<tr {attr_} class="ca-files-row{picked}"><td><span class="mdi {icon}"></span> {esc(e["name"])}</td>'
                        f'<td>{"" if e["dir"] else _size(e["size"])}</td><td>{_when(e["mtime"])}</td></tr>')
        if not d["entries"]:
            rows.append('<tr><td colspan="3" class="ca-empty">Empty folder.</td></tr>')
        more = '<div class="ca-sub ca-pad">Only the first 2000 entries are shown.</div>' if d.get("more") else ""
        box = js.document.getElementById("ca-files-list")
        if box:
            box.innerHTML = (f'<table class="ca-table"><thead><tr><th>Name</th><th>Size</th><th>Changed</th></tr></thead>'
                             f'<tbody>{"".join(rows)}</tbody></table>{more}')

    # -- file ------------------------------------------------------------------

    async def open(self, path: str) -> None:
        d = await FilesApi().read_async(self.server_id, self.root, path)
        if failed(d, "open the file"):
            return
        self.file = d
        for el in js.document.querySelectorAll("#ca-files-list [data-file]"):
            el.classList.toggle("ca-picked", attr(el, "data-file") == path)
        view = js.document.getElementById("ca-files-view")
        if not view:
            return
        meta = f'{_size(d["size"])} · changed {_when(d["mtime"])}'
        if d["binary"]:
            view.innerHTML = (f'<h2>{esc(d["name"])}</h2><div class="ca-sub">{meta}</div>'
                              '<div class="ca-empty">Not a text file, so it is not shown here.</div>')
            return
        notes = []
        if d["truncated"]:
            notes.append(f"Showing the last 2 MB of {_size(d['size'])}.")
        if d.get("note"):
            notes.append(d["note"])
        if not d["editable"] and d.get("why_not"):
            notes.append(d["why_not"])
        save = ('<div class="ca-actions"><button class="ca-btn ca-primary" data-act="save" disabled>'
                '<span class="mdi mdi-content-save-outline"></span> Save</button>'
                '<button class="ca-btn" data-act="revert" disabled>Undo changes</button>'
                '<span class="ca-sub">The previous version is kept by the app.</span></div>') if d["editable"] else ""
        view.innerHTML = (
            f'<h2>{esc(d["name"])}</h2><div class="ca-sub">{meta}</div>'
            + "".join(f'<div class="ca-note">{esc(n)}</div>' for n in notes)
            + f'<textarea class="ca-files-text" id="ca-files-text" spellcheck="false"'
              f'{"" if d["editable"] else " readonly"}></textarea>{save}')
        text = js.document.getElementById("ca-files-text")
        if text:
            text.value = d["text"]
            if d["truncated"] or str(d["name"]).startswith("ShooterGame.log"):
                text.scrollTop = text.scrollHeight

    def _dirty(self) -> bool:
        text = js.document.getElementById("ca-files-text")
        return bool(text and self.file.get("editable")
                    and str(text.value).replace("\r\n", "\n") != str(self.file.get("text", "")).replace("\r\n", "\n"))

    def _sync_buttons(self) -> None:
        dirty = self._dirty()
        for act in ("save", "revert"):
            btn = js.document.querySelector(f'#ca-files-view [data-act="{act}"]')
            if btn:
                btn.disabled = not dirty

    def _on_input(self, event) -> None:
        if closest(event.target, "#ca-files-text"):
            self._sync_buttons()

    async def save(self) -> None:
        text = js.document.getElementById("ca-files-text")
        if not text:
            return
        f = self.file
        if not await message.confirm(f"Save your changes to {f['name']}?", title="Save file", ok_text="Save"):
            return
        r = await FilesApi().save_async(self.server_id, self.root, f["path"], str(text.value), f["mtime"])
        if failed(r, f"save {f['name']}"):
            return
        toast(f"Saved {f['name']}.", kind="success")
        await self.open(f["path"])
        await self.load()

    async def _leave_ok(self) -> bool:
        if not self._dirty():
            return True
        return await message.confirm("Your changes to this file are not saved. Leave it anyway?",
                                     title="Unsaved changes", ok_text="Leave it", danger=True)

    async def _go(self, root: str | None = None, path: str | None = None, file: str | None = None) -> None:
        if not await self._leave_ok():
            return
        if root is not None and root != self.root:
            self.root, self.path, self.file = root, "", {}
            view = js.document.getElementById("ca-files-view")
            if view:
                view.innerHTML = '<div class="ca-empty">Pick a file to see it.</div>'
        if path is not None:
            self.path = path
        if file is not None:
            await self.open(file)
            return
        await self.load()

    def _on_click(self, event) -> None:
        el = closest(event.target, "[data-root]")
        if el:
            spawn(self._go(root=attr(el, "data-root")), "files root")
            return
        el = closest(event.target, "[data-dir]")
        if el:
            event.preventDefault()
            spawn(self._go(path=attr(el, "data-dir")), "files folder")
            return
        el = closest(event.target, "[data-file]")
        if el:
            spawn(self._go(file=attr(el, "data-file")), "files open")
            return
        btn = closest(event.target, "[data-act]")
        if not btn or btn.disabled:
            return
        act = attr(btn, "data-act")
        if act == "refresh":
            spawn(self.load(), "files refresh")
        elif act == "save":
            spawn(self.save(), "files save")
        elif act == "revert":
            text = js.document.getElementById("ca-files-text")
            if text:
                text.value = self.file.get("text", "")
                self._sync_buttons()
