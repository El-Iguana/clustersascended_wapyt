from datetime import datetime, timezone

from engine import logtail

LOG = """\
[2026.11.11-12.05.39:001][  0]Log file open
[2026.11.11-12.15.50:042][232]Server has completed startup and is now advertising for join. (16.91GB Mem)
[2026.11.11-12.25.31:891][373]SERVER: Server is stopping.
[2026.11.11-14.40.02:100][  0]Log file open
[2026.11.11-14.43.10:500][ 12]Hang detected on GameThread (15.0 seconds)
[2026.11.11-14.51.00:000][300]Server has completed startup and is now advertising for join. (17.20GB Mem)
"""


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


def test_stamp():
    assert logtail.stamp("[2026.11.11-12.15.50:042][232]x") == utc(2026, 11, 11, 12, 15, 50, 42000)
    assert logtail.stamp("no stamp") is None


def test_a_previous_runs_ready_line_does_not_count():
    events = logtail.scan(LOG, since=utc(2026, 11, 11, 14, 40))
    assert [(e.kind, e.mem_gb) for e in events] == [("hang", None), ("ready", 17.2)]
    assert logtail.scan(LOG, since=utc(2026, 11, 11, 15, 0)) == []


def test_follower_reads_increments_and_handles_rotation(tmp_path):
    p = tmp_path / "ShooterGame.log"
    p.write_text("[2026.11.11-12.00.00:000][0]a\n[2026.11.11-12.00.01:000][0]par")
    f = logtail.Follower(p)
    assert f.read_new() == "[2026.11.11-12.00.00:000][0]a\n"
    with open(p, "a") as fh:
        fh.write("tial\n")
    assert f.read_new() == "[2026.11.11-12.00.01:000][0]partial\n"
    assert f.read_new() == ""
    p.unlink()
    p.write_text("new file\n")   # ASA rotates on start: new inode
    assert f.read_new() == "new file\n"


def test_ready_history(tmp_path):
    (tmp_path / "ShooterGame.log").write_text(LOG)
    (tmp_path / "ShooterGame-backup-1.log").write_text(LOG)
    assert logtail.ready_reading_history(tmp_path) == [16.91, 17.2, 16.91, 17.2]
