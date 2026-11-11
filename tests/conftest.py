import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APPCODE = ROOT / "appcode"
for path in (APPCODE, ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

GiB = 1024 ** 3


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """A fresh database per test."""
    from engine import store

    d = tmp_path / "data"
    monkeypatch.setenv("CA_DATA_DIR", str(d))
    monkeypatch.setenv("CA_STEAM_CHECK", "off")
    store.reset_thread_connection()
    yield d
    store.reset_thread_connection()


@pytest.fixture
def fake_proc(tmp_path, monkeypatch):
    """/proc/meminfo and /proc/pressure/memory the test controls."""
    from engine import meminfo

    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True)

    def write(mem_total=31 * GiB, mem_available=28 * GiB, swap_total=34 * GiB, swap_free=33 * GiB):
        kb = lambda n: n // 1024  # noqa: E731
        (proc / "meminfo").write_text(
            f"MemTotal: {kb(mem_total)} kB\nMemAvailable: {kb(mem_available)} kB\n"
            f"SwapTotal: {kb(swap_total)} kB\nSwapFree: {kb(swap_free)} kB\n")
        (proc / "pressure" / "memory").write_text(
            "some avg10=0.50 avg60=0.10 avg300=0.00 total=1\nfull avg10=0.10 avg60=0.00 avg300=0.00 total=1\n")

    write()
    monkeypatch.setattr(meminfo, "PROC", proc)
    return write
