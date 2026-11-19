"""
A cluster's backups and schedules (``#/cluster/<id>/backups``): the nightly
backup, daily restart and auto-update, and every map's backups with Restore, Keep and
Delete.
"""
from __future__ import annotations

import js

from wapyt import message

from services.backups_api import BackupsApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast

GiB = 1024 ** 3
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
KINDS = {"scheduled": "Nightly", "manual": "By hand", "pre-update": "Before an update",
         "pre-restore": "Before a restore"}


def when(ts) -> str:
    return str(js.Date.new(float(ts) * 1000).toLocaleString()) if ts else "–"


def size(n) -> str:
    return f"{n / GiB:.2f} GiB" if n >= GiB / 10 else f"{n / 1024 ** 2:.0f} MiB"


def _schedule_card(sch: dict, title: str, icon: str, blurb: str, extra: str) -> str:
    k = sch["kind"]
    days = "".join(
        f'<label class="ca-day"><input type="checkbox" data-sched="{k}" name="day" value="{i}"'
        f'{" checked" if i in sch["days"] else ""}><span>{d}</span></label>' for i, d in enumerate(DAYS))
    last = (f'Last: {when(sch["last_run"])} · {esc(sch["last_result"])}' if sch.get("last_run")
            else "Has not run yet.")
    nxt = f'Next: {when(sch["next_run"])}' if sch["enabled"] and sch.get("next_run") else "Off."
    return f"""
<section class="ca-card ca-sched" data-kind="{k}">
  <div class="ca-host-head"><h2><span class="mdi {icon}"></span> {title}</h2>
    <label class="ca-switch-big"><input type="checkbox" data-sched="{k}" name="enabled"{" checked" if sch["enabled"] else ""}>
      <span>{"On" if sch["enabled"] else "Off"}</span></label></div>
  <div class="ca-sub">{blurb}</div>
  <div class="ca-sched-row"><label>At <input type="time" data-sched="{k}" name="at" value="{esc(sch["at"])}"></label>
    <div class="ca-days">{days}</div></div>
  {extra}
  <div class="ca-sub">{nxt} {last}</div>
  <div class="ca-actions"><button class="ca-btn ca-primary" data-act="save-sched" data-kind="{k}">Save</button>
    <button class="ca-btn" data-act="run-sched" data-id="{sch["id"]}" data-kind="{k}">Run now</button></div>
</section>"""


class BackupsTab:
    def __init__(self, container, cluster_id: str) -> None:
        self.el = container
        self.cluster_id = cluster_id
        self.listeners = Listeners()
        self.data: dict = {}

    def mount(self) -> None:
        self.el.innerHTML = '<div id="ca-backups"><div class="ca-empty">Loading…</div></div>'
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.on(self.el, "change", self._on_change)
        self.listeners.every(10000, lambda: spawn(self.refresh_lists(), "backups refresh"))
        spawn(self.load(), "backups load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        result = await BackupsApi().cluster_async(self.cluster_id)
        if failed(result, "load the backups"):
            return
        self.data = result
        root = js.document.getElementById("ca-backups")
        if not root:
            return
        scheds = {s["kind"]: s for s in result["schedules"]}
        cards = []
        b = scheds.get("backup")
        if b:
            cards.append(_schedule_card(
                b, "Nightly backup", "mdi-backup-restore",
                "Backs up every map's world, one after another. A running map is saved first; a stopped map "
                "that has not changed since its last backup is skipped.",
                f'<label class="ca-sched-row">Keep the newest <input type="number" min="1" max="60" '
                f'data-sched="backup" name="keep" value="{b["params"].get("keep", 7)}"> nightly backups per map '
                f'(ones you keep forever are never removed)</label>'))
        r = scheds.get("restart")
        if r:
            warn = ", ".join(str(w) for w in r["params"].get("warn") or [15, 5, 1])
            cards.append(_schedule_card(
                r, "Daily restart", "mdi-restart",
                "Restarts each running map in turn: players see a countdown in chat, the world is saved, and the "
                "map comes back through the start queue (one at a time, memory checked).",
                f'<label class="ca-sched-row">Warn players <input data-sched="restart" name="warn" value="{esc(warn)}"> '
                f'minutes before</label><label class="ca-sched-row">Message <input data-sched="restart" name="message" '
                f'value="{esc(r["params"].get("message", "Daily restart"))}"></label>'))
        u = scheds.get("update")
        if u:
            warn = ", ".join(str(w) for w in u["params"].get("warn") or [15, 5, 1])
            cards.append(_schedule_card(
                u, "Auto-update", "mdi-update",
                "When Steam has a newer ARK build, players see a countdown in chat, every running map is saved "
                "and stopped, each map is backed up and updated one at a time, and the maps that were running "
                "start again. When every map is up to date, nothing happens.",
                f'<label class="ca-sched-row">Warn players <input data-sched="update" name="warn" value="{esc(warn)}"> '
                f'minutes before</label><label class="ca-sched-row">Message <input data-sched="update" name="message" '
                f'value="{esc(u["params"].get("message", "Server update"))}"></label>'))
        disk = result.get("disk") or {}
        free = f' · {size(disk["free"])} free on the disk' if disk else ""
        root.innerHTML = f"""
<div class="ca-two">{"".join(cards)}</div>
<section class="ca-card">
  <div class="ca-host-head"><h2><span class="mdi mdi-archive-outline"></span> Backups</h2>
    <button class="ca-btn ca-primary" data-act="backup-all"><span class="mdi mdi-content-save-all-outline"></span>
      Back up every map now</button></div>
  <div class="ca-sub" id="ca-backup-usage">{size(result["total"])} used by backups{free}</div>
  <div id="ca-backup-maps">{self._maps(result)}</div>
</section>"""

    async def refresh_lists(self) -> None:
        result = await BackupsApi().cluster_async(self.cluster_id)
        if not result.get("ok"):
            return
        self.data = result
        box = js.document.getElementById("ca-backup-maps")
        if box:
            box.innerHTML = self._maps(result)
        usage = js.document.getElementById("ca-backup-usage")
        disk = result.get("disk") or {}
        if usage:
            usage.textContent = f"{size(result['total'])} used by backups" + (
                f" · {size(disk['free'])} free on the disk" if disk else "")

    def _maps(self, d: dict) -> str:
        parts = []
        for m in d["maps"]:
            rows = []
            for b in m["backups"]:
                pinned = bool(b["pinned"])
                rows.append(
                    f'<tr><td>{when(b["created"])}</td><td>{esc(KINDS.get(b["kind"], b["kind"]))}</td>'
                    f'<td>{size(b["size"])}</td><td>'
                    f'<button class="ca-icon ca-star{" ca-on" if pinned else ""}" data-act="pin" data-id="{b["id"]}" '
                    f'data-pinned="{1 if pinned else 0}" title="{"Kept forever (click to allow removal)" if pinned else "Keep forever"}">'
                    f'{"★" if pinned else "☆"}</button></td>'
                    f'<td class="ca-row-btns"><button class="ca-btn" data-act="restore" data-id="{b["id"]}" '
                    f'data-map="{esc(m["name"])}" data-when="{when(b["created"])}">'
                    f'<span class="mdi mdi-backup-restore"></span> Restore</button>'
                    f'<button class="ca-btn ca-danger" data-act="delete" data-id="{b["id"]}"'
                    f'{" disabled" if pinned else ""}>Delete</button></td></tr>')
            body = "".join(rows) or '<tr><td colspan="5" class="ca-empty">No backups yet.</td></tr>'
            parts.append(
                f'<h3 class="ca-map-h"><a href="#/map/{esc(m["id"])}">{esc(m["name"])}</a></h3>'
                f'<table class="ca-table"><thead><tr><th>When</th><th>Kind</th><th>Size</th><th>Keep</th><th></th>'
                f"</tr></thead><tbody>{body}</tbody></table>")
        return "".join(parts)

    # -- events --------------------------------------------------------------

    def _on_change(self, event) -> None:
        if attr(event.target, "name") == "enabled":
            label = event.target.parentElement.querySelector("span")
            if label:
                label.textContent = "On" if event.target.checked else "Off"

    def _on_click(self, event) -> None:
        btn = closest(event.target, "[data-act]")
        if not btn or btn.disabled:
            return
        act = attr(btn, "data-act")
        if act == "save-sched":
            spawn(self._save_schedule(attr(btn, "data-kind")), "save schedule")
        elif act == "run-sched":
            spawn(self._run_schedule(int(attr(btn, "data-id")), attr(btn, "data-kind")), "run schedule")
        elif act == "backup-all":
            spawn(self._backup_all(), "backup all")
        elif act == "pin":
            spawn(self._pin(int(attr(btn, "data-id")), attr(btn, "data-pinned") != "1"), "pin")
        elif act == "delete":
            spawn(self._delete(int(attr(btn, "data-id"))), "delete backup")
        elif act == "restore":
            spawn(self._restore(int(attr(btn, "data-id")), attr(btn, "data-map"), attr(btn, "data-when")),
                  "restore")

    def _values(self, kind: str) -> dict:
        out = {"days": []}
        for el in js.document.querySelectorAll(f'#ca-backups [data-sched="{kind}"]'):
            name = attr(el, "name")
            if name == "day":
                if el.checked:
                    out["days"].append(int(el.value))
            elif name == "enabled":
                out["enabled"] = bool(el.checked)
            else:
                out[name] = str(el.value)
        return out

    async def _save_schedule(self, kind: str) -> None:
        result = await BackupsApi().save_schedule_async(self.cluster_id, kind, self._values(kind))
        if not failed(result, "save the schedule"):
            toast("Schedule saved.", kind="success")
            await self.load()

    async def _run_schedule(self, schedule_id: int, kind: str) -> None:
        text = {"backup": "Back up every map now?",
                "restart": "Restart every running map now, with the countdown in chat?",
                "update": "Check for an ARK update now? If a map is behind, players get the countdown in chat, "
                          "then the maps are updated and the ones that were running start again."}.get(kind, "Run now?")
        if not await message.confirm(text, title="Run now", ok_text="Run now"):
            return
        result = await BackupsApi().run_now_async(schedule_id)
        if not failed(result, "run the schedule"):
            toast(result["result"], kind="success")
            await self.load()

    async def _backup_all(self) -> None:
        result = await BackupsApi().backup_all_async(self.cluster_id)
        if not failed(result, "start the backups"):
            toast(f"Backing up {', '.join(result['queued'])}, one after another.", kind="success")

    async def _pin(self, backup_id: int, pinned: bool) -> None:
        result = await BackupsApi().pin_async(backup_id, pinned)
        if not failed(result, "change the backup"):
            await self.refresh_lists()

    async def _delete(self, backup_id: int) -> None:
        if not await message.confirm("Delete this backup? It cannot be brought back.", title="Delete backup",
                                     ok_text="Delete", danger=True):
            return
        result = await BackupsApi().delete_async(backup_id)
        if not failed(result, "delete the backup"):
            toast("Deleted.", kind="success")
            await self.refresh_lists()

    async def _restore(self, backup_id: int, map_name: str, when_text: str) -> None:
        if not await message.confirm(
                f"Put {map_name} back to how it was at {when_text}?\n\n"
                "Everything built, tamed or found on this map since then is lost. The map is saved and stopped "
                "first, its current world is backed up (\"Before a restore\") and kept aside, and if it was "
                "running it starts again afterwards.\n\nPlayers' characters on other maps are not affected.",
                title=f"Restore {map_name}", ok_text="Restore", danger=True):
            return
        result = await BackupsApi().restore_async(backup_id)
        if not failed(result, "start the restore"):
            toast(f"Restoring {map_name}. Follow it on the dashboard's queue.", kind="success")
