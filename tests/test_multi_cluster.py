"""Several clusters on one host: creating them, and imports that must not touch each other."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ISLAND, SCORCHED, CENTER = "TheIsland_WP", "ScorchedEarth_WP", "TheCenter_WP"


def compose(base: Path, folder: str, cluster_id: str, rows: list, transfer: Path | None = None) -> str:
    """rows: (service, map, game port, rcon port[, container])"""
    root = base / folder
    root.mkdir(parents=True, exist_ok=True)
    transfer = transfer or root / "Cluster"
    services = {}
    for key, map_name, game, rcon, *container in rows:
        services[key] = {
            "container_name": container[0] if container else key, "network_mode": "host",
            "environment": {"MAP_NAME": map_name, "ASA_PORT": str(game), "RCON_PORT": str(rcon),
                            "CLUSTER_ID": cluster_id, "SERVER_ADMIN_PASSWORD": "pw", "MOD_IDS": "111"},
            "volumes": [f"{root / key / 'inst'}:/home/pok/arkserver",
                        f"{root / key / 'Saved'}:/home/pok/arkserver/ShooterGame/Saved",
                        f"{transfer}:/home/pok/arkserver/ShooterGame/Saved/clusters"]}
    path = root / "docker-compose.yaml"
    path.write_text(yaml.safe_dump({"services": services}))
    return str(path)


def alpha(base: Path) -> str:
    return compose(base, "a", "alphacluster", [("asa-a1", ISLAND, 41000, 42000), ("asa-a2", SCORCHED, 41010, 42001)])


def layout() -> dict:
    from engine import store

    return {c.id: sorted(s.id for s in store.servers(c.id)) for c in store.clusters()}


@pytest.fixture
def imported(data_dir, tmp_path):
    from engine import importer

    assert importer.apply(alpha(tmp_path))["ok"]
    return tmp_path


@pytest.fixture
def no_podman(monkeypatch):
    from engine import addmap

    def mkdirs(paths, _owner):
        for p in paths:
            Path(p).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(addmap, "_mkdirs", mkdirs)
    monkeypatch.setattr(addmap, "ports_in_use", lambda: set())


# -- imports ------------------------------------------------------------------

def test_two_clusters_keep_their_own_maps(imported):
    from engine import importer, store
    from engine.models import effective

    other = compose(imported, "b", "betacluster", [("asa-b1", ISLAND, 41100, 42100), ("asa-b2", CENTER, 41110, 42101)])
    assert importer.apply(other)["ok"]
    assert layout() == {"alpha": ["a1", "a2"], "beta": ["b1", "b2"]}
    a, b = effective(store.server("a1"), store.cluster("alpha")), effective(store.server("b1"), store.cluster("beta"))
    assert (a["cluster_id"], b["cluster_id"]) == ("alphacluster", "betacluster")
    assert a["transfer_dir"] != b["transfer_dir"]
    assert {s["kind"] for s in store.schedules("beta")} == {"backup", "restart", "update"}


@pytest.mark.parametrize("rows, name, expected", [
    ([("asa-a1", CENTER, 41100, 42100, "beta-c")], "", "already used by"),         # same service name
    ([("asa-z", CENTER, 41100, 42100, "asa-a1")], "", "already belongs to"),       # same container
    ([("asa-z", CENTER, 41000, 42100)], "", "port 41000 belongs to"),              # same port
    ([("asa-z", CENTER, 41100, 42100)], "alpha", "already a cluster called"),      # same cluster name
])
def test_a_collision_refuses_the_whole_import(imported, rows, name, expected):
    from engine import importer, store

    before = (layout(), store.cluster("alpha").to_dict(), store.server("a1").to_dict())
    path = compose(imported, "b", "betacluster", rows)
    assert any(expected in e for e in importer.preview(path, name).errors)
    r = importer.apply(path, name)
    assert not r["ok"] and expected in r["error"]
    assert (layout(), store.cluster("alpha").to_dict(), store.server("a1").to_dict()) == before


def test_a_shared_cluster_id_or_transfer_folder_is_refused(imported):
    from engine import importer

    same_id = compose(imported, "b", "alphacluster", [("asa-b1", CENTER, 41100, 42100)])
    assert "already uses the ClusterId" in importer.apply(same_id, "Beta")["error"]
    same_transfer = compose(imported, "c", "gammacluster", [("asa-c1", CENTER, 41200, 42200)],
                            transfer=imported / "a" / "Cluster")
    assert "already uses the transfer folder" in importer.apply(same_transfer)["error"]
    assert list(layout()) == ["alpha"]


def test_importing_again_changes_nothing_the_app_owns(imported):
    from engine import importer, store

    c = store.cluster("alpha")
    c.name, c.game, c.stacks, c.max_running, c.memory_cap = "Renamed", {"XPMultiplier": 3}, {"Stone_C": 500}, 1, 5
    c.defaults["admin_password"] = "changed"
    store.save_cluster(c)
    s = store.server("a1")
    s.overrides, s.game_port = {"game": {"XPMultiplier": 9}}, 41555
    store.save_server(s)
    before = (store.cluster("alpha").to_dict(), store.server("a1").to_dict())
    r = importer.apply(str(imported / "a" / "docker-compose.yaml"), "Some other name")
    assert not r["ok"] and "nothing to add" in r["error"]
    assert (store.cluster("alpha").to_dict(), store.server("a1").to_dict()) == before


def test_importing_again_adds_only_new_maps(imported):
    from engine import importer, store

    c = store.cluster("alpha")
    c.game = {"XPMultiplier": 3}
    store.save_cluster(c)
    path = compose(imported, "a", "alphacluster", [("asa-a1", ISLAND, 41000, 42000), ("asa-a2", SCORCHED, 41010, 42001),
                                                   ("asa-a3", CENTER, 41020, 42002)])
    r = importer.apply(path)
    assert r["ok"] and r["servers"] == ["a3"]
    assert store.cluster("alpha").game == {"XPMultiplier": 3}
    assert store.server("a3").priority == 3


def test_a_failed_write_leaves_nothing(imported, monkeypatch):
    import sqlite3

    from engine import importer, store

    def boom(_s):
        raise sqlite3.IntegrityError("no")

    monkeypatch.setattr(store, "save_server", boom)
    assert not importer.apply(compose(imported, "b", "betacluster", [("asa-b1", CENTER, 41100, 42100)]))["ok"]
    assert list(layout()) == ["alpha"]


# -- a new cluster ------------------------------------------------------------

def new(tmp_path, name="Friends PvE", **over):
    return {"name": name, "root_dir": str(tmp_path / "new"), **over}


def test_create_a_cluster_and_plan_its_first_map(imported, no_podman, monkeypatch):
    from engine import addmap, clusters, store, views

    r = clusters.create(new(imported, server_password="join"))
    assert r["cluster"] == "friends-pve"
    c = store.cluster("friends-pve")
    assert c.cluster_id == "friendspvecluster" and c.game == {}
    assert c.transfer_dir == str(imported / "new" / "Cluster") and Path(c.transfer_dir).is_dir()
    assert c.defaults["admin_password"] and c.defaults["server_password"] == "join"
    assert c.defaults["mod_ids"] == [] and "-NoBattlEye" not in c.defaults["custom_args"]
    assert {s["kind"] for s in store.schedules(c.id)} == {"backup", "restart", "update"}
    assert views.cluster_detail(c.id)["servers"] == []
    assert views.backups_detail(c.id)

    d = addmap.suggest(c.id)
    assert d["session"] == "Friends PvE1" and d["taken_maps"] == []
    monkeypatch.setattr(addmap, "dir_size", lambda _p: 0)
    monkeypatch.setattr(addmap.shutil, "disk_usage", lambda _p: type("U", (), {"free": 10 ** 12})())
    p = addmap.plan(c.id, {"map_name": ISLAND, "session": d["session"], "game_port": d["game_port"],
                           "rcon_port": d["rcon_port"], "source": "download"})
    assert p["cluster"] == c.id and p["server_id"] == "theisland"       # alpha has The Island too
    assert p["saved"].startswith(c.root_dir)


@pytest.mark.parametrize("over, expected", [
    ({"name": " "}, "needs a name"),
    ({"name": "!!!"}, "at least one letter"),
    ({"name": "ALPHA"}, "already a cluster"),
    ({"cluster_id": "alphacluster"}, "already uses the ClusterId"),
    ({"cluster_id": "bad id"}, "ClusterId is"),
    ({"root_dir": "relative/path"}, "full path"),
    ({"root_dir": '/tmp/a"; rm -rf x'}, "full path"),
    ({"root_dir": "/tmp/a:b"}, "full path"),
    ({"admin_password": "has space"}, "letters, digits"),
    ({"max_players": "0"}, "between 1 and 200"),
])
def test_create_refusals(imported, no_podman, over, expected):
    from engine import clusters

    with pytest.raises(clusters.Invalid, match=expected):
        clusters.create(new(imported, **over) if "name" in over else {**new(imported), **over})
    assert list(layout()) == ["alpha"]


def test_folders_may_not_overlap_another_cluster(imported, no_podman):
    from engine import clusters

    for root in (imported / "a", imported / "a" / "inner", imported):
        with pytest.raises(clusters.Invalid, match="overlaps"):
            clusters.create(new(imported, root_dir=str(root)))
    busy = imported / "busy"
    busy.mkdir()
    (busy / "file").write_text("x")
    with pytest.raises(clusters.Invalid, match="not an empty folder"):
        clusters.create(new(imported, root_dir=str(busy)))


def test_delete_an_empty_cluster(imported, no_podman):
    from engine import clusters, store

    cid = clusters.create(new(imported))["cluster"]
    c = store.cluster(cid)
    store.conn().execute("INSERT INTO bans(cluster, eos_id, banned_at) VALUES(?, 'x', 0)", (cid,))
    r = clusters.delete(cid)
    assert r == {"name": "Friends PvE", "kept": []}
    assert store.cluster(cid) is None and store.schedules(cid) == []
    assert store.conn().execute("SELECT COUNT(*) FROM bans").fetchone()[0] == 0
    assert not Path(c.root_dir).exists()
    assert list(layout()) == ["alpha"] and store.schedules("alpha")


def test_delete_keeps_files_and_refuses_a_cluster_with_maps(imported, no_podman):
    from engine import clusters, store

    with pytest.raises(clusters.Invalid, match="still has maps"):
        clusters.delete("alpha")
    cid = clusters.create(new(imported))["cluster"]
    c = store.cluster(cid)
    (Path(c.transfer_dir) / "upload").write_text("a survivor")
    assert clusters.delete(cid)["kept"] == [c.transfer_dir, c.root_dir]
    assert (Path(c.transfer_dir) / "upload").read_text() == "a survivor"
    with pytest.raises(clusters.Invalid, match="Unknown"):
        clusters.delete(cid)


def test_a_failed_folder_leaves_no_cluster(imported, monkeypatch):
    from engine import addmap, clusters

    def fail(_paths, _owner):
        raise RuntimeError("podman says no")

    monkeypatch.setattr(addmap, "_mkdirs", fail)
    with pytest.raises(clusters.Invalid, match="podman says no"):
        clusters.create(new(imported))
    assert list(layout()) == ["alpha"] and not (imported / "new").exists()


# -- every map stopped first ----------------------------------------------------

def test_adding_a_cluster_or_importing_needs_every_map_stopped(imported, no_podman, monkeypatch):
    """Like adding a map: a running (or busy) map anywhere blocks a new cluster and an import."""
    from engine import addmap, clusters, importer, paths

    copies = lambda: len(list((paths.data_dir() / "import").glob("*")))   # noqa: E731
    before = copies()
    monkeypatch.setattr(addmap, "busy_maps", lambda: ["A1"])
    with pytest.raises(clusters.Invalid, match="Stop every map first .*A1"):
        clusters.create(new(imported))
    r = importer.apply(compose(imported, "b", "betacluster", [("asa-b1", CENTER, 41100, 42100)]))
    assert not r["ok"] and "Stop every map first" in r["error"]
    assert list(layout()) == ["alpha"]
    assert copies() == before          # a refused import leaves no copy of the compose file

    monkeypatch.setattr(addmap, "busy_maps", lambda: [])
    assert clusters.create(new(imported))["cluster"] == "friends-pve"
    assert importer.apply(compose(imported, "b", "betacluster", [("asa-b1", CENTER, 41100, 42100)]))["ok"]
    assert sorted(layout()) == ["alpha", "beta", "friends-pve"]
