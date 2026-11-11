"""Memory admission: the rules that keep a third map from OOMing the host."""
from engine.admission import HostMem, ServerView, Snapshot, admit, gib
from engine.models import Cluster, GiB, HostSettings


def snap(phases, needs=None, actual=None, host=None, clusters=None, settings=None, players=None):
    needs = needs or {}
    actual = actual or {}
    players = players or {}
    servers = {}
    for i, (sid, phase) in enumerate(phases.items(), start=1):
        servers[sid] = ServerView(sid, sid.upper(), "c1", i, phase, needs.get(sid, 16 * GiB),
                                  actual.get(sid), players.get(sid, 0), started_at=float(i))
    host = host or HostMem(31 * GiB, 28 * GiB, 34 * GiB, 33 * GiB)
    return Snapshot(host, settings or HostSettings(), servers, clusters or {})


def running_two():
    # Two maps up: 16 GiB each held (anon+swap), host shows ~33 GiB used.
    host = HostMem(31 * GiB, 4 * GiB, 34 * GiB, 29 * GiB)
    return snap({"lc": "ready", "ast": "ready", "gen": "stopped"},
                needs={"lc": 18 * GiB, "ast": 17 * GiB, "gen": 17 * GiB},
                actual={"lc": 16 * GiB, "ast": 15 * GiB}, host=host)


def test_first_map_fits():
    d = admit(snap({"lc": "stopped", "ast": "stopped"}), "start", "lc")
    assert d.ok, d.reason


def test_third_map_is_refused_with_a_breakdown():
    d = admit(running_two(), "start", "gen")
    assert not d.ok
    assert "Not enough memory to start GEN" in d.reason
    assert "at most 2 maps" in d.reason
    assert "budget" in d.reason
    assert any("GEN needs 17.0 GiB" in line for line in d.lines)
    assert d.numbers["budget"] == 41 * GiB


def test_make_room_picks_the_least_important_map():
    d = admit(running_two(), "start", "gen")
    assert d.victims == ["ast"]          # priority 2 goes before priority 1


def test_victims_prefer_empty_maps_at_equal_priority():
    s = running_two()
    s.servers["ast"].priority = 1
    s.servers["lc"].players, s.servers["ast"].players = 0, 4
    d = admit(s, "start", "gen")
    assert d.victims == ["lc"]


def test_restart_does_not_count_itself():
    d = admit(running_two(), "restart", "lc")
    assert d.ok, d.reason


def test_starting_an_already_running_map_is_refused():
    d = admit(running_two(), "start", "lc")
    assert not d.ok and "already ready" in d.reason


def test_queued_jobs_are_projected():
    s = snap({"lc": "ready", "ast": "stopped", "gen": "stopped"}, actual={"lc": 16 * GiB},
             host=HostMem(31 * GiB, 14 * GiB, 34 * GiB, 34 * GiB))
    # AST is already queued to start, so GEN would be the third.
    d = admit(s, "start", "gen", pending=[("start", "ast")])
    assert not d.ok
    # ...but if LC is queued to stop, GEN fits.
    d = admit(s, "start", "gen", pending=[("start", "ast"), ("stop", "lc")])
    assert d.ok, d.reason


def test_external_memory_counts():
    # Nothing managed runs, but something else holds 30 GiB.
    host = HostMem(31 * GiB, 1 * GiB, 34 * GiB, 34 * GiB)
    d = admit(snap({"lc": "stopped"}, host=host), "start", "lc")
    assert d.numbers["external"] == 30 * GiB
    d = admit(snap({"lc": "stopped"}, needs={"lc": 16 * GiB}, host=host,
                   settings=HostSettings(swap_allowance=0)), "start", "lc")
    assert not d.ok


def test_cluster_rules_can_only_be_stricter():
    clusters = {"c1": Cluster("c1", "Main", "x", "/t", "/r", max_running=1)}
    s = snap({"lc": "ready", "ast": "stopped"}, actual={"lc": 16 * GiB}, clusters=clusters,
             host=HostMem(31 * GiB, 14 * GiB, 34 * GiB, 34 * GiB))
    d = admit(s, "start", "ast")
    assert not d.ok and "cluster Main runs at most 1" in d.reason


def test_cluster_memory_cap():
    clusters = {"c1": Cluster("c1", "Main", "x", "/t", "/r", memory_cap=30 * GiB)}
    s = snap({"lc": "ready", "ast": "stopped"}, actual={"lc": 16 * GiB}, clusters=clusters,
             host=HostMem(31 * GiB, 14 * GiB, 34 * GiB, 34 * GiB))
    d = admit(s, "start", "ast")
    assert not d.ok and "capped at 30.0 GiB" in d.reason


def test_memory_must_physically_exist():
    # Within the 41 GiB budget on paper (19 used + 16), but this host has only
    # 2 GiB of swap: 12 GiB RAM + 2 GiB swap − 2 GiB reserve cannot hold 16.
    host = HostMem(31 * GiB, 12 * GiB, 2 * GiB, 2 * GiB)
    s = snap({"lc": "stopped"}, host=host)
    d = admit(s, "start", "lc")
    assert not d.ok and "free right now" in d.reason


def test_a_starting_map_still_growing_reserves_its_remainder():
    # LC is starting and holds 4 of its 18 GiB; that 14 GiB is spoken for.
    host = HostMem(31 * GiB, 26 * GiB, 34 * GiB, 10 * GiB)
    s = snap({"lc": "starting", "ast": "stopped"}, needs={"lc": 18 * GiB}, actual={"lc": 4 * GiB},
             host=host)
    d = admit(s, "start", "ast")
    assert not d.ok


def test_gib_format():
    assert gib(41 * GiB) == "41.0 GiB"


# -- startup vs running needs --------------------------------------------------

def test_a_start_reserves_its_startup_need():
    # 41 GiB budget; LC runs at 17; GEN needs 16 to run but 22 to start.
    host = HostMem(31 * GiB, 14 * GiB, 34 * GiB, 34 * GiB)
    s = snap({"lc": "ready", "gen": "stopped"}, needs={"lc": 17 * GiB, "gen": 16 * GiB},
             actual={"lc": 17 * GiB}, host=host)
    s.servers["gen"].need_start = 22 * GiB
    d = admit(s, "start", "gen")
    assert d.numbers["need"] == 22 * GiB and d.numbers["need_running"] == 16 * GiB
    assert "needs 22.0 GiB to start (16.0 GiB once it is running)" in d.lines[0]


def test_a_loading_map_holds_its_startup_need_until_it_is_up():
    host = HostMem(31 * GiB, 20 * GiB, 34 * GiB, 34 * GiB)
    s = snap({"lc": "starting", "gen": "stopped"}, needs={"lc": 17 * GiB, "gen": 10 * GiB},
             actual={"lc": 6 * GiB}, host=host)
    s.servers["lc"].need_start = 24 * GiB
    d = admit(s, "start", "gen")
    assert d.numbers["committed"] == 24 * GiB             # not its running 17, not its current 6
    s.servers["lc"].phase = "ready"
    s.servers["lc"].actual = 17 * GiB
    d = admit(s, "start", "gen")
    assert d.numbers["committed"] == 17 * GiB


def test_never_fits():
    from engine.admission import never_fits

    host = HostMem(31 * GiB, 26 * GiB, 34 * GiB, 34 * GiB)
    s = snap({"big": "stopped"}, needs={"big": 40 * GiB}, host=host)
    s.servers["big"].need_start = 50 * GiB
    assert "even with every map stopped" in never_fits(s, "big")
    s.servers["big"].need_start = None
    s.servers["big"].need = 16 * GiB
    assert never_fits(s, "big") is None
