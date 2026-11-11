"""BFF: import an existing podman-compose cluster (writes the database only)."""
from __future__ import annotations

from pytincture.dataclass import backend_for_frontend, bff_policy


@backend_for_frontend
@bff_policy(application="clusters_ascended")
class ImportApi:
    def __init__(self, _user: dict = None) -> None:
        self._user = _user or {}

    def preview(self, compose_path: str, cluster_name: str = "") -> dict:
        from engine import importer

        plan = importer.preview(str(compose_path or "").strip(), str(cluster_name or "").strip())
        return {"ok": not plan.errors, "plan": plan.to_dict(), "error": " ".join(plan.errors)}

    def apply(self, compose_path: str, cluster_name: str = "") -> dict:
        from engine import importer, state
        from services.auth import actor

        result = importer.apply(str(compose_path or "").strip(), str(cluster_name or "").strip(),
                                actor=actor(self._user))
        state.request_refresh()
        return result
