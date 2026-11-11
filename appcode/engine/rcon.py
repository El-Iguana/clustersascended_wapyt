"""
A minimal Source RCON client (from the old webadmin). Host networking puts
every map's RCON on 127.0.0.1:<rcon_port>. One command per connection.
"""
from __future__ import annotations

import re
import socket
import struct

_AUTH = 3
_EXEC = 2


class RconError(Exception):
    pass


def _pack(pid: int, ptype: int, body: str) -> bytes:
    data = struct.pack("<ii", pid, ptype) + body.encode("utf-8") + b"\x00\x00"
    return struct.pack("<i", len(data)) + data


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise RconError("connection closed mid-packet")
        buf += chunk
    return buf


def _recv(sock: socket.socket) -> tuple[int, int, str]:
    (length,) = struct.unpack("<i", _recv_exact(sock, 4))
    data = _recv_exact(sock, length)
    pid, ptype = struct.unpack("<ii", data[:8])
    return pid, ptype, data[8:-2].decode("utf-8", "replace")


def command(port: int, password: str, cmd: str, timeout: float = 6.0, host: str = "127.0.0.1") -> str:
    if not password:
        raise RconError("no admin password configured")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(_pack(1, _AUTH, password))
            pid, _, _ = _recv(sock)
            if pid == -1:
                raise RconError("RCON authentication failed")
            sock.sendall(_pack(2, _EXEC, cmd))
            _, _, body = _recv(sock)
            return body
    except (OSError, struct.error) as exc:
        raise RconError(str(exc)) from exc


_PLAYER = re.compile(r"^\s*\d+\.\s+(.+),\s*([0-9a-fA-F]{16,64})\s*$")


def parse_players(text: str) -> list[dict]:
    """``ListPlayers`` lines, "0. Name, 0002…eos id" (names may contain ", ")."""
    out = []
    for line in (text or "").splitlines():
        m = _PLAYER.match(line)
        if m:
            out.append({"name": m.group(1).strip(), "id": m.group(2)})
    return out
