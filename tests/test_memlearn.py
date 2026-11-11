from engine import memlearn
from engine.models import GiB, HostSettings


def test_need_floor_margin_and_override():
    s = HostSettings()
    assert memlearn.need(s, None, []) == 16 * GiB
    assert memlearn.need(s, None, [10 * GiB]) == 16 * GiB
    assert memlearn.need(s, None, [17 * GiB, 15 * GiB]) == int(17 * GiB * 1.10)
    assert memlearn.need(s, 20 * GiB, [30 * GiB]) == 20 * GiB


def test_only_recent_runs_count():
    s = HostSettings()
    peaks = [17 * GiB] * 5 + [30 * GiB]
    assert memlearn.need(s, None, peaks) == int(17 * GiB * 1.10)
