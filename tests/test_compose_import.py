"""Importing the real (redacted) POK compose file into a cluster."""
from pathlib import Path

from engine.compose_import import plan_import
from engine.models import effective

FIXTURE = Path(__file__).parent / "fixtures" / "pok-compose.yaml"
BASE = "/home/oldmangan/asa-cluster"


def plan():
    return plan_import(FIXTURE.read_text(), BASE)


def test_one_cluster_three_maps():
    p = plan()
    assert p.errors == []
    c = p.cluster
    assert c.cluster_id == "oldmangancluster"
    assert c.id == "oldmangan"
    assert c.transfer_dir == f"{BASE}/Cluster"
    assert c.root_dir == BASE
    assert [s.id for s in p.servers] == ["lostcolony", "astraeos", "genesis1"]
    assert [s.priority for s in p.servers] == [1, 2, 3]
    lc = p.servers[0]
    assert (lc.name, lc.map_name, lc.session_name) == ("Lost Colony", "LostColony_WP", "OldManGan1")
    assert (lc.game_port, lc.rcon_port, lc.container_name) == (7777, 27020, "asa-lostcolony")
    assert lc.install_dir == f"{BASE}/ServerFiles/arkserver"
    assert lc.saved_dir == f"{BASE}/Instance_LostColony/Saved"


def test_cluster_defaults():
    d = plan().cluster.defaults
    assert d["mod_ids"] == ["1188679", "930170", "929420", "942024", "928548", "950970", "945883", "940975"]
    assert d["custom_args"] == ["-newsaveformat", "-usestore", "-NoTransferFromFiltering"]
    assert d["battleye"] is False
    assert d["admin_password"] == "adminsecret"
    assert d["max_players"] == 70
    assert d["extra_env"]["UPDATE_SERVER"] == "FALSE"
    assert "MAP_NAME" not in d["extra_env"]


def test_genesis_keeps_its_exact_mod_order():
    p = plan()
    gen = p.servers[2]
    assert gen.overrides["mods_mode"] == "replace"
    assert effective(gen, p.cluster)["mod_ids"] == [
        "1188679", "930170", "929420", "942024", "928548", "950970", "945883", "1598772", "940975"]
    lc = p.servers[0]
    assert lc.overrides == {}
    assert effective(lc, p.cluster)["mod_ids"] == p.cluster.defaults["mod_ids"]


def test_secrets_are_hidden_in_the_preview():
    shown = plan().to_dict()
    assert shown["cluster"]["defaults"]["admin_password"] == "••••••"


def test_mixed_cluster_ids_are_refused():
    text = FIXTURE.read_text().replace("CLUSTER_ID=oldmangancluster", "CLUSTER_ID=other", 1)
    p = plan_import(text, BASE)
    assert p.errors and "share one CLUSTER_ID" in p.errors[0]


def test_appended_mods():
    text = FIXTURE.read_text()
    text = text.replace("945883,1598772,940975", "945883,940975,1598772")
    gen = plan_import(text, BASE).servers[2]
    assert gen.overrides == {"mod_ids": ["1598772"], "mods_mode": "append"}


def test_no_asa_services():
    p = plan_import("services:\n  web:\n    image: nginx\n", BASE)
    assert p.errors == ["No ASA (POK) services found in this compose file."]
