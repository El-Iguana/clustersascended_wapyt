"""
A map's Console tab (``#/map/<id>/console``): its game log as it happens,
buttons for common commands, and a command line (↑/↓ for history).
"""
from __future__ import annotations

import js

from wapyt import message

from services.players_api import PlayersApi
from ui.common import Listeners, attr, closest, esc, failed, spawn

MAX_LINES = 2000
QUICK = (
    ("SaveWorld", "Save the world", "mdi-content-save", None),
    ("ListPlayers", "Who is on", "mdi-account-multiple", None),
    ("SetTimeOfDay 08:00", "Make it morning", "mdi-weather-sunny", None),
    ("DestroyWildDinos", "Respawn all wild creatures", "mdi-paw-off",
     "Remove every wild creature so new ones spawn (tames are safe)? Players may see creatures pop out."),
)


class ConsoleTab:
    def __init__(self, container, server_id: str) -> None:
        self.el = container
        self.server_id = server_id
        self.listeners = Listeners()
        self.inode = 0
        self.offset = -1
        self.history: list[str] = []
        self.pos = 0
        self._busy = False

    def mount(self) -> None:
        quick = "".join(f'<button class="ca-btn" data-cmd="{esc(cmd)}" data-confirm="{esc(c or "")}">'
                        f'<span class="mdi {icon}"></span> {esc(label)}</button>' for cmd, label, icon, c in QUICK)
        self.el.innerHTML = f"""
<section class="ca-card ca-console">
  <div class="ca-host-head"><h2><span class="mdi mdi-console"></span> Console</h2>
    <label class="ca-sub"><input type="checkbox" id="ca-con-follow" checked> Follow new lines</label></div>
  <div class="ca-actions">{quick}</div>
  <pre class="ca-log ca-con-log" id="ca-con-log"></pre>
  <div class="ca-con-input"><span class="ca-con-prompt">&gt;</span>
    <input id="ca-con-cmd" spellcheck="false" autocomplete="off" placeholder="Admin command, e.g. ServerChat Hello (↑ ↓ for history)">
    <button class="ca-btn ca-primary" data-act="run">Run</button></div>
  <div class="ca-sub">Commands go to the server as an admin (RCON). Replies show in the log in blue.</div>
</section>"""
        self.listeners.on(self.el, "click", self._on_click)
        self.listeners.on(js.document.getElementById("ca-con-cmd"), "keydown", self._on_key)
        self.listeners.every(2000, lambda: spawn(self.poll(), "console poll"))
        spawn(self.poll(), "console first")

    def unmount(self) -> None:
        self.listeners.clear()

    def _append(self, html_text: str) -> None:
        log = js.document.getElementById("ca-con-log")
        if not log:
            return
        log.insertAdjacentHTML("beforeend", html_text)
        extra = log.childElementCount - MAX_LINES
        while extra > 0 and log.firstElementChild:
            log.firstElementChild.remove()
            extra -= 1
        follow = js.document.getElementById("ca-con-follow")
        if follow and follow.checked:
            log.scrollTop = log.scrollHeight

    async def poll(self) -> None:
        if self._busy:
            return
        self._busy = True
        try:
            result = await PlayersApi().console_async(self.server_id, self.inode, self.offset)
        finally:
            self._busy = False
        if not result.get("ok"):
            return
        log = js.document.getElementById("ca-con-log")
        if result.get("reset") and log:
            log.innerHTML = ""
        self.inode = result.get("inode") or 0
        self.offset = result.get("offset", -1)
        text = result.get("text") or ""
        if not text:
            if result.get("inode") is None and log and not log.childElementCount:
                self._append('<div class="ca-con-note">No game log yet: the map has not started.</div>')
            return
        lines = [ln for ln in text.splitlines() if ln.strip()][-MAX_LINES:]
        self._append("".join(f'<div class="{_kind(ln)}">{esc(ln)}</div>' for ln in lines))

    def _on_key(self, event) -> None:
        box = event.target
        if event.key == "Enter":
            event.preventDefault()
            spawn(self.run(str(box.value)), "console run")
        elif event.key == "ArrowUp" and self.history:
            event.preventDefault()
            self.pos = max(0, self.pos - 1)
            box.value = self.history[self.pos]
        elif event.key == "ArrowDown" and self.history:
            event.preventDefault()
            self.pos = min(len(self.history), self.pos + 1)
            box.value = self.history[self.pos] if self.pos < len(self.history) else ""

    def _on_click(self, event) -> None:
        quick = closest(event.target, "[data-cmd]")
        if quick:
            spawn(self._quick(attr(quick, "data-cmd"), attr(quick, "data-confirm")), "quick command")
            return
        if closest(event.target, '[data-act="run"]'):
            box = js.document.getElementById("ca-con-cmd")
            spawn(self.run(str(box.value) if box else ""), "console run")

    async def _quick(self, cmd: str, confirm: str) -> None:
        if confirm and not await message.confirm(confirm, title=cmd, ok_text="Do it", danger=True):
            return
        await self.run(cmd, keep=False)

    async def run(self, cmd: str, keep: bool = True) -> None:
        cmd = cmd.strip()
        if not cmd:
            return
        box = js.document.getElementById("ca-con-cmd")
        if keep:
            if not self.history or self.history[-1] != cmd:
                self.history.append(cmd)
            self.pos = len(self.history)
            if box:
                box.value = ""
        self._append(f'<div class="ca-con-cmd">&gt; {esc(cmd)}</div>')
        result = await PlayersApi().command_async(self.server_id, cmd)
        if not result.get("ok"):
            self._append(f'<div class="ca-con-err">{esc(result.get("error") or "Failed.")}</div>')
            failed(result, "run the command")
            return
        self._append("".join(f'<div class="ca-con-reply">{esc(ln)}</div>'
                             for ln in (result.get("reply") or "").splitlines() or ["(no reply)"]))


def _kind(line: str) -> str:
    low = line.lower()
    if "error" in low or "fatal" in low or "hang detected" in low:
        return "ca-con-errline"
    if "warning" in low:
        return "ca-con-warn"
    if "advertising for join" in low or "joined this ark" in low or "left this ark" in low:
        return "ca-con-good"
    return "ca-con-line"
