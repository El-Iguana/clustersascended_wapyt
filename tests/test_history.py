"""Memory history: a reading a minute, highest per slice, gaps when the app was not running."""
from __future__ import annotations

import pytest

from engine.models import GiB

T0 = 1_800_000_000 // 3600 * 3600        # an hour boundary


@pytest.fixture
def two_maps(data_dir):
    from engine import history, state, store
    from engine.admission import HostMem
    from engine.models import Cluster, Server

    store.save_cluster(Cluster("main", "Main", "maincluster", "/t", "/r"))
    store.save_cluster(Cluster("other", "Other", "othercluster", "/t2", "/r2"))
    for i, (sid, cid) in enumerate((("lc", "main"), ("ast", "main"), ("isle", "other"))):
        store.save_server(Server(sid, cid, sid.upper(), f"{sid}_WP", sid, f"asa-{sid}", 7700 + i, 27000 + i,
                                 f"/i/{sid}", f"/s/{sid}", priority=i + 1))
    state._live.clear()
    state.set_host(HostMem(mem_total=31 * GiB, mem_available=11 * GiB, swap_total=34 * GiB, swap_free=30 * GiB))
    history._last.update(sample=0.0, prune=0.0)
    yield
    state._live.clear()


def run(sid, gib):
    from engine import state

    lv = state.mutate(sid)
    lv.container_state = "running"
    lv.mem = {"total": int(gib * GiB)}


def test_a_reading_a_minute_with_everything_else(two_maps):
    from engine import history, store

    run("lc", 17)
    assert history.maybe_record(T0)
    assert not history.maybe_record(T0 + 30)            # not a minute yet
    rows = {sid: total for _b, sid, total in store.mem_sample_buckets(T0, 60)}
    assert rows == {"lc": 17 * GiB, "_host": 24 * GiB, "_other": 7 * GiB}


def test_highest_per_slice_zero_when_stopped_gap_when_app_down(two_maps):
    from engine import history, store

    store.add_mem_samples(T0 + 3600, [("lc", 10 * GiB), ("_host", 20 * GiB), ("_other", 10 * GiB)])
    store.add_mem_samples(T0 + 3660, [("lc", 12 * GiB), ("_host", 22 * GiB), ("_other", 10 * GiB)])
    store.add_mem_samples(T0 + 3960, [("_host", 8 * GiB), ("_other", 8 * GiB)])        # lc stopped
    d = history.overview("6h", now=T0 + 6 * 3600)
    i = d["labels"].index(history._label(T0 + 3600, 6 * 3600))
    lc = next(m for m in d["maps"] if m["id"] == "lc")
    assert lc["values"][i] == 12.0                       # the higher of two readings in the slice
    assert lc["values"][i + 3] == 0.0                    # a covered slice without lc: stopped
    assert lc["values"][0] is None and d["other"][0] is None   # no readings at all: a gap
    assert [m["id"] for m in d["maps"]] == ["lc"]        # maps that never ran are left out
    assert lc["colour"] == 0 and d["peak"]["gib"] == 22.0
    assert d["partial"] and len(d["labels"]) == 180


def test_colours_follow_the_dashboard_order_across_clusters(two_maps):
    from engine import history, store

    store.add_mem_samples(T0, [("isle", 5 * GiB), ("_host", 9 * GiB), ("_other", 4 * GiB)])
    d = history.overview("24h", now=T0 + 600)
    assert [(m["id"], m["colour"]) for m in d["maps"]] == [("isle", 2)]
    s = history.for_server("isle", "24h", now=T0 + 600)
    assert s["colour"] == 2 and s["highest"] == 5.0 and s["need"] > 0
    assert history.for_server("nope") is None


def test_old_readings_go_and_a_removed_map_takes_its_readings(two_maps):
    from engine import history, store

    store.add_mem_samples(T0 - 15 * 86400, [("lc", GiB), ("_host", GiB)])
    store.add_mem_samples(T0 - 60, [("lc", GiB), ("_host", GiB)])
    run("lc", 1)
    history.maybe_record(T0)                              # prunes on its first reading
    assert {b for b, *_ in store.mem_sample_buckets(0, 86400 * 30)} == {(T0 - 60) // (86400 * 30) * 86400 * 30}
    store.delete_server("lc")
    assert {sid for _b, sid, _t in store.mem_sample_buckets(0, 60)} == {"_host", "_other"}
