"""Saving cluster and map settings: what is accepted, and what it changes."""
import pytest

from engine import store, views
from engine.models import Cluster, GiB, Server, effective


@pytest.fixture
def two_maps(data_dir):
    store.save_cluster(Cluster("main", "Main", "maincluster", "/t", "/r",
                               defaults={"mod_ids": ["111", "222"], "admin_password": "secret",
                                         "custom_args": ["-usestore"], "battleye": False, "max_players": 70}))
    store.save_server(Server("a", "main", "A", "A_WP", "A", "asa-a", 7777, 27020, "/i/a", "/s/a", priority=1))
    store.save_server(Server("b", "main", "B", "B_WP", "B", "asa-b", 7787, 27021, "/i/b", "/s/b", priority=2))


def test_cluster_save_masks_and_keeps_passwords(two_maps):
    views.save_cluster("main", {"admin_password": views.MASK, "max_players": "40",
                                "mod_ids": ["333", "111", "333"], "custom_args": "-usestore -newsaveformat"})
    c = store.cluster("main")
    assert c.defaults["admin_password"] == "secret"
    assert c.defaults["mod_ids"] == ["333", "111"]
    assert c.defaults["max_players"] == 40
    assert c.defaults["custom_args"] == ["-usestore", "-newsaveformat"]
    detail = views.cluster_detail("main")
    assert detail["cluster"]["defaults"]["admin_password"] == views.MASK


def test_cluster_refuses_bad_values(two_maps):
    for values in ({"admin_password": ""}, {"admin_password": "has space"}, {"mod_ids": ["abc"]},
                   {"custom_args": "usestore"}, {"max_players": "500"}, {"memory_cap": "lots"}):
        with pytest.raises(views.Invalid):
            views.save_cluster("main", values)


def test_map_overrides_and_inherit(two_maps):
    views.save_server("b", {"max_players": "20", "battleye": "on", "mods_mode": "append", "mod_ids": ["999"],
                            "admin_password": "own-pass", "custom_args": None})
    b = store.server("b")
    eff = effective(b, store.cluster("main"))
    assert eff["max_players"] == 20 and eff["battleye"] is True
    assert eff["mod_ids"] == ["111", "222", "999"]
    assert eff["admin_password"] == "own-pass"
    assert eff["custom_args"] == ["-usestore"]
    views.save_server("b", {"max_players": "", "battleye": "inherit", "mods_mode": "inherit",
                            "admin_password": ""})
    b = store.server("b")
    assert b.overrides == {}


def test_map_ports_must_be_free(two_maps):
    with pytest.raises(views.Invalid, match="used by A"):
        views.save_server("b", {"game_port": "7777"})
    with pytest.raises(views.Invalid, match="must differ"):
        views.save_server("b", {"game_port": "27021"})
    views.save_server("b", {"game_port": "7797", "need_override": "18.5", "priority": "3"})
    b = store.server("b")
    assert (b.game_port, b.need_override, b.priority) == (7797, int(18.5 * GiB), 3)


def test_clear_hold(two_maps):
    s = store.server("a")
    s.held = "Crash loop"
    store.save_server(s)
    views.clear_hold("a")
    assert store.server("a").held == ""
