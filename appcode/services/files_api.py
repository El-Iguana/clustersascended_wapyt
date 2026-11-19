"""BFF: a map's files (engine/files.py)."""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class FilesApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def listing(self, server_id: str, root: str = "saved", path: str = "") -> dict:
        from engine import files

        try:
            return {"ok": True, **files.listing(str(server_id), str(root), str(path or ""))}
        except files.Invalid as exc:
            return {"ok": False, "error": str(exc)}

    def read(self, server_id: str, root: str, path: str) -> dict:
        from engine import files

        try:
            return {"ok": True, **files.read(str(server_id), str(root), str(path))}
        except files.Invalid as exc:
            return {"ok": False, "error": str(exc)}

    def save(self, server_id: str, root: str, path: str, text: str, mtime: float) -> dict:
        from engine import files
        from services.auth import actor

        try:
            return {"ok": True, **files.write(str(server_id), str(root), str(path), str(text or ""),
                                              float(mtime or 0), actor=actor(self._user))}
        except files.Invalid as exc:
            return {"ok": False, "error": str(exc)}
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}
