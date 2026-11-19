"""The Files tab: what can be read, what can be changed, and that nothing outside a map's folders is reachable."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture
def map_files(data_dir, tmp_path, monkeypatch):
    from engine import game_settings, state, store
    from engine.models import Cluster, Server

    saved, install, transfer = tmp_path / "Saved", tmp_path / "install", tmp_path / "Cluster"
    (saved / "Config" / "WindowsServer").mkdir(parents=True)
    (saved / "Logs").mkdir()
    (install / "ShooterGame").mkdir(parents=True)
    transfer.mkdir()
    (saved / "Config" / "WindowsServer" / "GameUserSettings.ini").write_bytes(b"[ServerSettings]\r\nA=1\r\n")
    (saved / "Config" / "WindowsServer" / "Engine.ini").write_bytes(b"[Core]\r\nX=1\r\n")
    (saved / "Logs" / "ShooterGame.log").write_text("line\n" * 10)
    (saved / "world.ark").write_bytes(b"\x00\x01binary")
    (install / "ShooterGame" / "notes.txt").write_text("install")
    (tmp_path / "secret.txt").write_text("outside")
    store.save_cluster(Cluster("c", "C", "cid", str(transfer), str(tmp_path)))
    store.save_server(Server("lc", "c", "LC", "LostColony_WP", "LC", "asa-lc", 7777, 27020, str(install), str(saved)))
    state._overrides.clear()
    state._live.clear()

    def plain_write(path, text):
        Path(path).write_bytes(text.encode("utf-8"))

    monkeypatch.setattr(game_settings, "_write", plain_write)
    return saved


def test_listing_puts_folders_first_and_marks_text(map_files):
    from engine import files

    d = files.listing("lc", "saved", "")
    assert [e["name"] for e in d["entries"]] == ["Config", "Logs", "world.ark"]
    assert d["writable"] and [r["key"] for r in d["roots"]] == ["saved", "game", "transfers"]
    logs = files.listing("lc", "saved", "Logs")
    assert logs["entries"][0]["text"] and logs["crumbs"] == [{"name": "Logs", "path": "Logs"}]
    assert not files.listing("lc", "game", "")["writable"]


@pytest.mark.parametrize("rel", ["..", "../secret.txt", "Config/../../secret.txt", "a\x00b"])
def test_nothing_outside_the_map_s_folders(map_files, rel):
    from engine import files

    with pytest.raises(files.Invalid):
        files.read("lc", "saved", rel)


def test_a_symlink_out_is_refused(map_files, tmp_path):
    from engine import files

    os.symlink(tmp_path / "secret.txt", map_files / "link.txt")
    with pytest.raises(files.Invalid, match="outside"):
        files.read("lc", "saved", "link.txt")


def test_read_text_binary_and_the_end_of_a_big_log(map_files, monkeypatch):
    from engine import files

    d = files.read("lc", "saved", "Config/WindowsServer/GameUserSettings.ini")
    assert d["text"] == "[ServerSettings]\r\nA=1\r\n" and d["editable"] and d["note"]
    assert files.read("lc", "saved", "world.ark")["binary"]
    monkeypatch.setattr(files, "MAX_READ", 20)
    big = files.read("lc", "saved", "Logs/ShooterGame.log")
    assert big["truncated"] and not big["editable"] and big["text"].startswith("line")
    assert files.read("lc", "game", "ShooterGame/notes.txt")["why_not"] == "This folder is read-only here."


def test_save_keeps_crlf_and_the_old_copy(map_files, data_dir):
    from engine import files

    rel = "Config/WindowsServer/Engine.ini"
    d = files.read("lc", "saved", rel)
    files.write("lc", "saved", rel, "[Core]\nX=2\n", d["mtime"])        # a textarea sends LF
    assert (map_files / rel).read_bytes() == b"[Core]\r\nX=2\r\n"
    kept = list((data_dir / "file-backups" / "lc").iterdir())
    assert len(kept) == 1 and kept[0].read_bytes() == b"[Core]\r\nX=1\r\n"


def test_save_refusals(map_files):
    from engine import files, state

    rel = "Config/WindowsServer/Engine.ini"
    d = files.read("lc", "saved", rel)
    with pytest.raises(files.Invalid, match="changed since"):
        files.write("lc", "saved", rel, "x", d["mtime"] - 10)
    with pytest.raises(files.Invalid, match="Logs are read-only"):
        files.write("lc", "saved", "Logs/ShooterGame.log", "x", files.read("lc", "saved", "Logs/ShooterGame.log")["mtime"])
    with pytest.raises(files.Invalid, match="Only .ini"):
        files.write("lc", "saved", "world.ark", "x", files.read("lc", "saved", "world.ark")["mtime"])
    with pytest.raises(files.Invalid, match="read-only"):
        files.write("lc", "game", "ShooterGame/notes.txt", "x", 0)
    with pytest.raises(files.Invalid, match="only changes existing"):
        files.write("lc", "saved", "Config/new.ini", "x", 0)
    (map_files / "bad$name.ini").write_text("x")
    with pytest.raises(files.Invalid, match="characters"):
        files.write("lc", "saved", "bad$name.ini", "y", files.read("lc", "saved", "bad$name.ini")["mtime"])
    state.set_override("lc", "ready")
    assert "Stop LC" in files.read("lc", "saved", rel)["why_not"]
    with pytest.raises(files.Invalid, match="Stop LC"):
        files.write("lc", "saved", rel, "x", d["mtime"])
    state.set_override("lc", None)
