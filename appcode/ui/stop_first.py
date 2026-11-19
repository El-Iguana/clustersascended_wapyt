"""
The "every map must be stopped first" banner shared by the add-a-map wizard,
the new-cluster form and the import page, with its "Save and stop every map"
button. Pages poll ``ClusterApi().stop_first_async()`` and re-render it.
"""
from __future__ import annotations

from wapyt import message

from services.cluster_api import ClusterApi
from ui.common import esc, failed, toast


def banner(status: dict, ready_text: str) -> str:
    """``status`` is ``ClusterApi.stop_first()``: ``busy`` (names) and ``why`` ("" when clear)."""
    if not status.get("why"):
        return f'<div class="ca-fits"><span class="mdi mdi-check-circle"></span> {esc(ready_text)}</div>'
    busy = status.get("busy") or []
    if not busy:
        return f'<div class="ca-refusal"><span class="mdi mdi-timer-sand"></span> {esc(status["why"])}</div>'
    return (f'<div class="ca-refusal"><span class="mdi mdi-stop-circle-outline"></span> Every map on this '
            f'machine, in every cluster, must be stopped first. Still running or busy: '
            f'<b>{esc(", ".join(busy))}</b>.'
            f'<div class="ca-actions"><button class="ca-btn ca-danger" data-act="stop-all">'
            f'Save and stop every map</button></div></div>')


async def stop_all() -> None:
    if not await message.confirm("Save the world on every running map and stop them all?\n\n"
                                 "Players on them are disconnected.", title="Stop every map",
                                 ok_text="Save and stop all", danger=True):
        return
    result = await ClusterApi().stop_all_async()
    if not failed(result, "stop the maps"):
        toast(f"Stopping {', '.join(result['queued']) or 'nothing'}…", kind="success")
