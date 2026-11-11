"""A tiny Source RCON server for tests: answers ListPlayers and SaveWorld."""
from __future__ import annotations

import socket
import struct
import threading


class FakeRcon:
    def __init__(self, password: str = "adminsecret", players: list[str] | None = None) -> None:
        self.password = password
        self.players = players or []
        self.commands: list[str] = []
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def close(self) -> None:
        self.sock.close()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    @staticmethod
    def _packet(pid: int, ptype: int, body: str) -> bytes:
        data = struct.pack("<ii", pid, ptype) + body.encode() + b"\x00\x00"
        return struct.pack("<i", len(data)) + data

    def _read(self, conn):
        head = conn.recv(4)
        if len(head) < 4:
            return None
        (n,) = struct.unpack("<i", head)
        data = b""
        while len(data) < n:
            data += conn.recv(n - len(data))
        pid, ptype = struct.unpack("<ii", data[:8])
        return pid, ptype, data[8:-2].decode()

    def _handle(self, conn) -> None:
        with conn:
            pkt = self._read(conn)
            if not pkt:
                return
            pid, _, body = pkt
            if body != self.password:
                conn.sendall(self._packet(-1, 2, ""))
                return
            conn.sendall(self._packet(pid, 2, ""))
            pkt = self._read(conn)
            if not pkt:
                return
            pid, _, cmd = pkt
            self.commands.append(cmd)
            if cmd == "ListPlayers":
                reply = "\n".join(f"{i}. {p}, 0002{'a' * 28}" for i, p in enumerate(self.players)) \
                    or "No Players Connected"
            elif cmd == "SaveWorld":
                reply = "World Saved"
            else:
                reply = "Server received, But no response!!"
            conn.sendall(self._packet(pid, 0, reply))
