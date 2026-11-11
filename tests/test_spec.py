"""The app's containers reproduce the compose ones (plus the app's own choices)."""
from pathlib import Path

from engine import spec
from engine.compose_import import plan_import
from engine.models import HostSettings, effective

FIXTURE = Path(__file__).parent / "fixtures" / "pok-compose.yaml"
BASE = "/home/oldmangan/asa-cluster"


def build(index=0, **kw):
    plan = plan_import(FIXTURE.read_text(), BASE)
    s = plan.servers[index]
    return spec.build(effective(s, plan.cluster), HostSettings(), cluster=plan.cluster.id, server=s.id,
                      priority=s.priority, log_dir="/data/logs", **kw)


def test_env_matches_the_compose_service():
    env = build(2).env
    assert env["MAP_NAME"] == "Genesis_WP"
    assert env["SESSION_NAME"] == "OldManGan3"
    assert env["ASA_PORT"] == "7797" and env["RCON_PORT"] == "27022"
    assert env["CLUSTER_ID"] == "oldmangancluster"
    assert env["MOD_IDS"] == "1188679,930170,929420,942024,928548,950970,945883,1598772,940975"
    assert env["CUSTOM_SERVER_ARGS"] == "-newsaveformat -usestore -NoTransferFromFiltering -NoBattlEye"
    assert env["SERVER_ADMIN_PASSWORD"] == "adminsecret"
    assert env["PUID"] == env["PGID"] == "7777"
    assert env["INSTANCE_NAME"] == "asa-genesis1"
    assert env["UPDATE_COORDINATION_ROLE"] == "MASTER"      # carried over as extra env
    # The app's own choices.
    assert env["RANDOM_STARTUP_DELAY"] == "FALSE"
    assert env["UPDATE_SERVER"] == "FALSE"
    assert build(2, update_pass=True).env["UPDATE_SERVER"] == "TRUE"


def test_argv():
    s = build(0)
    argv = spec.create_argv(s, "/data/env/lostcolony.env")
    assert argv[:2] == ["create", "--name"] and argv[2] == "asa-lostcolony"
    joined = " ".join(argv)
    for part in ("--network host", "--restart no", "--env-file /data/env/lostcolony.env",
                 "--stop-timeout 300", "--oom-score-adj 300", "--log-driver k8s-file",
                 "-v /home/oldmangan/asa-cluster/ServerFiles/arkserver:/home/pok/arkserver",
                 "-v /home/oldmangan/asa-cluster/Instance_LostColony/Saved:/home/pok/arkserver/ShooterGame/Saved",
                 "-v /home/oldmangan/asa-cluster/Cluster:/home/pok/arkserver/ShooterGame/Saved/clusters",
                 "--label io.clustersascended.managed=1", "--label io.clustersascended.server=lostcolony"):
        assert part in joined, part
    assert argv[-1] == "docker.io/acekorneya/asa_server:2_1_latest"
    assert "adminsecret" not in joined          # passwords only in the env file


def test_hash_changes_with_settings_only():
    a, b = build(0), build(0)
    assert a.digest() == b.digest()
    assert build(0, update_pass=True).digest() != a.digest()
    assert spec.oom_score(1) == 300 and spec.oom_score(3) == 500 and spec.oom_score(20) == 1000


def test_battleye_on_drops_the_flag():
    plan = plan_import(FIXTURE.read_text(), BASE)
    plan.cluster.defaults["battleye"] = True
    s = plan.servers[0]
    env = spec.build_env(effective(s, plan.cluster), HostSettings())
    assert "-NoBattlEye" not in env["CUSTOM_SERVER_ARGS"]


def test_env_file_refuses_line_breaks():
    import pytest

    with pytest.raises(ValueError):
        spec.env_file_text({"A": "x\ny"})
    assert spec.env_file_text({"B": "2", "A": "1"}) == "A=1\nB=2\n"
