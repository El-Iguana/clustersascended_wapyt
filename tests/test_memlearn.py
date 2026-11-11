from engine import memlearn
from engine.models import GiB, HostSettings


def test_running_need_is_measured_or_the_default():
    s = HostSettings()
    assert memlearn.need(s, None, []) == 16 * GiB                       # nothing measured yet
    assert memlearn.need(s, None, [10 * GiB]) == int(10 * GiB * 1.10)   # measured: no floor
    assert memlearn.need(s, None, [17 * GiB, 15 * GiB]) == int(17 * GiB * 1.10)
    assert memlearn.need(s, 20 * GiB, [30 * GiB]) == 20 * GiB


def test_only_recent_runs_count():
    s = HostSettings()
    peaks = [17 * GiB] * 5 + [30 * GiB]
    assert memlearn.need(s, None, peaks) == int(17 * GiB * 1.10)


def test_starting_always_needs_more_than_running():
    s = HostSettings()
    run, start = memlearn.needs(s, None, [], [])
    assert (run, start) == (16 * GiB, int(16 * GiB * 1.25))            # no starts measured: factor
    run, start = memlearn.needs(s, None, [12 * GiB], [])
    assert start == int(run * 1.25)
    run, start = memlearn.needs(s, None, [12 * GiB], [18 * GiB])        # a measured start peaked higher
    assert start == int(18 * GiB * 1.10)
    run, start = memlearn.needs(s, None, [12 * GiB], [12 * GiB])        # measured about the same:
    assert start == int(run * 1.25)                                     # still assumed to take more
