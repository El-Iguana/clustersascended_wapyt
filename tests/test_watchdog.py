import pytest

from engine import watchdog
from engine.admission import HostMem, ServerView
from engine.models import GiB, HostSettings


@pytest.fixture(autouse=True)
def fresh():
    watchdog.reset()
    yield
    watchdog.reset()


def host(free_ram, free_swap, psi=0.0):
    return HostMem(31 * GiB, int(free_ram * GiB), 34 * GiB, int(free_swap * GiB), 0.0, psi)


SERVERS = [
    ServerView("lc", "LC", "c", 1, "ready", 18 * GiB, 17 * GiB, players=3, started_at=100),
    ServerView("ast", "AST", "c", 2, "ready", 20 * GiB, 19 * GiB, players=0, started_at=200),
]


def test_levels():
    s = HostSettings()
    assert watchdog.evaluate(host(10, 10), s)[0] == "ok"
    assert watchdog.evaluate(host(2, 3), s)[0] == "warn"
    assert watchdog.evaluate(host(1, 1), s)[0] == "critical"
    assert watchdog.evaluate(host(10, 10, psi=45), s)[0] == "critical"


def test_acts_only_when_critical_is_sustained():
    s = HostSettings()
    assert watchdog.tick(host(1, 1), s, SERVERS, now=1000) is None      # just turned critical
    assert watchdog.tick(host(1, 1), s, SERVERS, now=1010) is None
    assert watchdog.tick(host(1, 1), s, SERVERS, now=1021) == "ast"     # least important
    # Cooldown: no second victim right away.
    assert watchdog.tick(host(1, 1), s, SERVERS, now=1030) is None
    assert watchdog.status()["last_victim"] == "ast"


def test_recovering_resets_the_timer():
    s = HostSettings()
    watchdog.tick(host(1, 1), s, SERVERS, now=1000)
    watchdog.tick(host(10, 10), s, SERVERS, now=1015)
    assert watchdog.tick(host(1, 1), s, SERVERS, now=1025) is None


def test_a_loading_map_goes_first():
    servers = SERVERS + [ServerView("gen", "GEN", "c", 3, "starting", 16 * GiB, 6 * GiB, started_at=300)]
    servers[1].priority = 9
    assert watchdog.pick_victim(servers) == "gen"


def test_disabled_never_acts():
    s = HostSettings(watchdog_enabled=False)
    for t in (0, 30, 60):
        assert watchdog.tick(host(1, 1), s, SERVERS, now=t) is None
    assert watchdog.status()["level"] == "critical"
