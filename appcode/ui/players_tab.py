"""
A map's Players tab (``#/map/<id>/players``): send a message, who is online
(kick, ban), everyone seen in the cluster, bans, and this map's sessions.
"""
from __future__ import annotations

import js

from wapyt import message

from services.fmt import duration
from services.players_api import PlayersApi
from ui.common import Listeners, attr, closest, esc, failed, spawn, toast


def when(ts) -> str:
    return str(js.Date.new(float(ts) * 1000).toLocaleString()) if ts else "–"


def ago(ts) -> str:
    if not ts:
        return "–"
    return duration(js.Date.now() / 1000 - float(ts))


class PlayersTab:
    def __init__(self, container, server_id: str) -> None:
        self.el = container
        self.server_id = server_id
        self.listeners = Listeners()
        self.data: dict = {}

    def mount(self) -> None:
        self.el.innerHTML = f"""
<section class="ca-card ca-form" id="ca-msg">
  <h2><span class="mdi mdi-message-text-outline"></span> Send a message</h2>
  <div class="ca-msg-row"><input id="ca-msg-text" maxlength="200" placeholder="Type a message for the players…">
    <button class="ca-btn ca-primary" data-act="send"><span class="mdi mdi-send"></span> Send</button></div>
  <div class="ca-msg-row ca-sub">
    <label><input type="radio" name="msg-kind" value="chat" checked> In chat</label>
    <label><input type="radio" name="msg-kind" value="big"> Big on-screen message</label>
    <span class="ca-gap"></span>
    <label><input type="radio" name="msg-to" value="map" checked> This map</label>
    <label><input type="radio" name="msg-to" value="cluster"> Every map in the cluster</label>
  </div>
</section>
<div id="ca-players-body"><div class="ca-empty">Loading…</div></div>"""
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.on(js.document.getElementById("ca-msg-text"), "keydown", self._on_key)
        self.listeners.every(10000, lambda: spawn(self.load(), "players refresh"))
        spawn(self.load(), "players load")

    def unmount(self) -> None:
        self.listeners.clear()

    async def load(self) -> None:
        result = await PlayersApi().map_async(self.server_id)
        if not result.get("ok"):
            failed(result, "load the players")
            return
        self.data = result
        body = js.document.getElementById("ca-players-body")
        if body:
            body.innerHTML = self._render(result)

    def _render(self, d: dict) -> str:
        maps = d.get("maps") or {}
        banned = {b["eos_id"] for b in d["bans"]}
        if d["phase"] != "ready":
            online = '<div class="ca-sub">The map is not running.</div>'
        elif not d["online"]:
            online = '<div class="ca-sub">Nobody is on right now.</div>'
        else:
            rows = "".join(
                f'<tr><td><b>{esc(p["name"])}</b></td><td>{ago(p.get("joined_at"))}</td>'
                f'<td class="ca-row-btns"><button class="ca-btn" data-act="kick" data-id="{esc(p["id"])}" '
                f'data-name="{esc(p["name"])}">Kick</button><button class="ca-btn ca-danger" data-act="ban" '
                f'data-id="{esc(p["id"])}" data-name="{esc(p["name"])}">Ban</button></td></tr>'
                for p in d["online"])
            online = (f'<table class="ca-table"><thead><tr><th>Survivor</th><th>On for</th><th></th></tr></thead>'
                      f"<tbody>{rows}</tbody></table>")
        known = "".join(
            f'<tr><td><b>{esc(p["name"])}</b>{" <span class=ca-chip>banned</span>" if p["id"] in banned else ""}</td>'
            f'<td>{ago(p["last_seen"])} ago</td><td>{duration(p["seconds"])}</td>'
            f'<td>{esc(", ".join(maps.get(m, m) for m in p["maps"]))}</td>'
            f'<td class="ca-row-btns">'
            + (f'<button class="ca-btn" data-act="unban" data-id="{esc(p["id"])}" data-name="{esc(p["name"])}">Unban</button>'
               if p["id"] in banned else
               f'<button class="ca-btn ca-danger" data-act="ban" data-id="{esc(p["id"])}" data-name="{esc(p["name"])}">Ban</button>')
            + "</td></tr>" for p in d["known"]) or '<tr><td colspan="5" class="ca-empty">Nobody yet.</td></tr>'
        bans = "".join(
            f'<tr><td><b>{esc(b["name"] or b["eos_id"])}</b></td><td>{esc(b["reason"])}</td><td>{when(b["banned_at"])}</td>'
            f'<td>{esc(", ".join(maps.get(m, m) for m in b["applied"])) or "waiting for maps to start"}</td>'
            f'<td class="ca-row-btns"><button class="ca-btn" data-act="unban" data-id="{esc(b["eos_id"])}" '
            f'data-name="{esc(b["name"])}">Unban</button></td></tr>'
            for b in d["bans"]) or '<tr><td colspan="5" class="ca-empty">No bans.</td></tr>'
        sessions = "".join(
            f'<tr><td>{esc(h["name"])}</td><td>{when(h["joined_at"])}</td>'
            f'<td>{when(h["left_at"]) if h.get("left_at") else "<b>online</b>"}</td>'
            f'<td>{duration((h["left_at"] or js.Date.now() / 1000) - h["joined_at"])}</td></tr>'
            for h in d["history"][:60]) or '<tr><td colspan="4" class="ca-empty">No sessions yet.</td></tr>'
        return f"""
<section class="ca-card"><h2><span class="mdi mdi-account-multiple"></span> Online now</h2>{online}</section>
<section class="ca-card ca-table-wrap"><h2 class="ca-pad">Survivors seen in this cluster</h2>
  <table class="ca-table"><thead><tr><th>Survivor</th><th>Last seen</th><th>Time played</th><th>Maps</th><th></th>
  </tr></thead><tbody>{known}</tbody></table></section>
<section class="ca-card ca-table-wrap"><h2 class="ca-pad">Bans (every map in the cluster)</h2>
  <table class="ca-table"><thead><tr><th>Survivor</th><th>Reason</th><th>When</th><th>Applied on</th><th></th>
  </tr></thead><tbody>{bans}</tbody></table></section>
<section class="ca-card ca-table-wrap"><h2 class="ca-pad">Recent sessions on this map</h2>
  <table class="ca-table"><thead><tr><th>Survivor</th><th>Joined</th><th>Left</th><th>Stayed</th></tr></thead>
  <tbody>{sessions}</tbody></table></section>"""

    # -- events --------------------------------------------------------------

    def _on_key(self, event) -> None:
        if event.key == "Enter":
            event.preventDefault()
            spawn(self._send(), "send message")

    def _on_click(self, event) -> None:
        btn = closest(event.target, "[data-act]")
        if not btn or btn.disabled:
            return
        act = attr(btn, "data-act")
        if act == "send":
            spawn(self._send(), "send message")
        elif act == "kick":
            spawn(self._kick(attr(btn, "data-id"), attr(btn, "data-name")), "kick")
        elif act == "ban":
            spawn(self._ban(attr(btn, "data-id"), attr(btn, "data-name")), "ban")
        elif act == "unban":
            spawn(self._unban(attr(btn, "data-id"), attr(btn, "data-name")), "unban")

    async def _send(self) -> None:
        box = js.document.getElementById("ca-msg-text")
        text = str(box.value).strip() if box else ""
        if not text:
            return
        kind = js.document.querySelector('input[name="msg-kind"]:checked')
        to = js.document.querySelector('input[name="msg-to"]:checked')
        target = str(to.value) if to else "map"
        target_id = self.server_id if target == "map" else self.data.get("cluster", "")
        result = await PlayersApi().message_async(target, target_id, text, bool(kind and str(kind.value) == "big"))
        if failed(result, "send the message"):
            return
        box.value = ""
        skipped = f" (not running: {', '.join(result['skipped'])})" if result.get("skipped") else ""
        toast(f"Sent to {', '.join(result['sent'])}{skipped}.", kind="success")

    async def _kick(self, eos_id: str, name: str) -> None:
        if not await message.confirm(f"Kick {name} off this map? They can join again.", title="Kick",
                                     ok_text="Kick"):
            return
        result = await PlayersApi().kick_async(self.server_id, eos_id)
        if not failed(result, f"kick {name}"):
            toast(f"{name} was kicked.", kind="success")
            await self.load()

    async def _ban(self, eos_id: str, name: str) -> None:
        reason = await message.prompt(f"Ban {name} from every map in this cluster?\n\nThey are removed now and "
                                      "cannot join until unbanned. Reason (optional, only you see it):",
                                      title=f"Ban {name}", ok_text="Ban")
        if reason is None:
            return
        result = await PlayersApi().ban_async(self.data.get("cluster", ""), eos_id, name, str(reason))
        if not failed(result, f"ban {name}"):
            later = f" Stopped maps get it when they next start ({', '.join(result['later'])})." \
                if result.get("later") else ""
            toast(f"{name} is banned.{later}", kind="success")
            await self.load()

    async def _unban(self, eos_id: str, name: str) -> None:
        if not await message.confirm(f"Unban {name}? They can join every map again.", title="Unban",
                                     ok_text="Unban"):
            return
        result = await PlayersApi().unban_async(self.data.get("cluster", ""), eos_id)
        if not failed(result, f"unban {name}"):
            later = (f" Maps that were not running ({', '.join(result['later'])}) may still refuse them; "
                     "unban again once they run." if result.get("later") else "")
            toast(f"{name} is unbanned.{later}", kind="success")
            await self.load()
