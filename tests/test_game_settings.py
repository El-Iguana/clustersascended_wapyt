"""Game settings: the catalogue, editing ini files in place, import and writing before a start."""
import shutil
from pathlib import Path

import pytest

from engine import ark_ini, game_settings, store, views
from engine.models import Cluster, Server
from services import ark_settings
from services.ark_settings import ORDER, PRESETS, SETTINGS

FIX = Path(__file__).parent / "fixtures"


def test_catalogue_is_consistent():
    assert len(ORDER) == len(set(ORDER)) == len(SETTINGS)
    groups = {g[0] for g in ark_settings.GROUPS}
    for key, s in SETTINGS.items():
        assert s["group"] in groups, key
        assert s["file"] in ("gus", "game"), key
        if s["kind"] != "bool":
            assert s["min"] <= s["default"] <= s["max"], key
            assert s["step"] > 0, key
    for pid, _n, _b, values in PRESETS:
        for k, v in values.items():
            assert k in SETTINGS, (pid, k)
            assert ark_settings.in_range(k, ark_settings.normalize(k, v)), (pid, k)


def test_wild_level():
    assert ark_settings.wild_level({"OverrideOfficialDifficulty": 5.0}) == 150
    assert ark_settings.wild_level({"OverrideOfficialDifficulty": 6.0}) == 180
    # The override is the difficulty: the offset no longer matters.
    assert ark_settings.wild_level({"OverrideOfficialDifficulty": 4.0, "DifficultyOffset": 0.2}) == 120
    # Without it, the offset scales the map's difficulty of 4.
    assert ark_settings.wild_level({}) == 120
    assert ark_settings.wild_level({"DifficultyOffset": 0.2}) == 36
    assert ark_settings.wild_level({"DifficultyOffset": 0.0}) == 15


def test_ini_matching_is_case_insensitive_and_keeps_everything_else():
    text = "[/Script/ShooterGame.ShooterGameMode]\r\nBabyMatureSpeedMultiplier=10.000000\r\nOther=1\r\n"
    assert ark_ini.read(text, "/script/shootergame.shootergamemode", ["babymaturespeedmultiplier"]) == \
        {"babymaturespeedmultiplier": "10.000000"}
    out = ark_ini.write(text, "/script/shootergame.shootergamemode",
                        {"babyMatureSpeedMultiplier": "20.000000", "EggHatchSpeedMultiplier": "5.000000"})
    assert out == ("[/Script/ShooterGame.ShooterGameMode]\r\nBabyMatureSpeedMultiplier=20.000000\r\nOther=1\r\n"
                   "EggHatchSpeedMultiplier=5.000000\r\n")


def test_render_changes_only_what_differs():
    gus = (FIX / "GameUserSettings.ini").read_bytes().decode()
    values = game_settings.official()
    values.update(game_settings.read_map_text(gus, "gus"))
    assert game_settings.render(gus, "gus", values) == gus            # nothing to change: byte for byte
    values["XPMultiplier"] = 4.0
    out = game_settings.render(gus, "gus", values)
    assert "XPMultiplier=4.000000\r\n" in out
    assert out.replace("XPMultiplier=4.000000", "XPMultiplier=3.000000") == gus.replace(
        _line(gus, "XPMultiplier"), "XPMultiplier=3.000000")
    # A key at its official value that the file does not have is not added.
    missing = [k for k in ORDER if SETTINGS[k]["file"] == "gus" and k + "=" not in gus]
    assert missing
    for k in missing:
        assert k + "=" not in out


def _line(text, key):
    return next(line for line in text.splitlines() if line.startswith(key + "="))


def maps(tmp_path):
    servers = []
    for sid in ("lc", "ast"):
        d = tmp_path / sid / "Saved" / "Config" / "WindowsServer"
        d.mkdir(parents=True)
        shutil.copy(FIX / "GameUserSettings.ini", d)
        shutil.copy(FIX / "Game.ini", d)
        servers.append(Server(sid, "c", sid, f"{sid}_WP", sid, f"asa-{sid}", 7777 + len(servers) * 10,
                              27020 + len(servers), str(tmp_path / sid / "install"), str(tmp_path / sid / "Saved")))
    return servers


def test_import_splits_shared_and_own(tmp_path):
    servers = maps(tmp_path)
    game = servers[1].saved_dir + "/Config/WindowsServer/Game.ini"
    Path(game).write_text(Path(game).read_text().replace("BabyMatureSpeedMultiplier=10", "BabyMatureSpeedMultiplier=20"))
    c = Cluster("c", "C", "cid", "/t", "/r")
    shared, own = game_settings.import_from_maps(c, servers)
    assert shared["XPMultiplier"] == 3.0
    assert shared["TamingSpeedMultiplier"] == 5.0
    assert shared["ItemStackSizeMultiplier"] == 3.0
    assert shared["EggHatchSpeedMultiplier"] == 5.0
    assert shared["OverrideOfficialDifficulty"] == 4.0
    assert shared["DifficultyOffset"] == 0.2
    assert shared["ServerPVE"] is True
    assert "BabyMatureSpeedMultiplier" not in shared
    assert own == {"lc": {"BabyMatureSpeedMultiplier": 10.0}, "ast": {"BabyMatureSpeedMultiplier": 20.0}}


def test_apply_writes_through_unshare_and_keeps_a_backup(tmp_path, data_dir, monkeypatch):
    import os
    monkeypatch.setenv("CA_PODMAN", str(Path(__file__).parent / "fakes" / "podman"))
    state_dir = tmp_path / "pod"
    state_dir.mkdir()
    (state_dir / "state.json").write_text('{"containers": {}}')
    monkeypatch.setenv("FAKE_PODMAN_STATE", str(state_dir))
    os.chmod(Path(__file__).parent / "fakes" / "podman", 0o755)
    servers = maps(tmp_path)
    c = Cluster("c", "C", "cid", "/t", "/r")
    log = []
    assert game_settings.apply(servers[0], c, log.append) is False      # never managed: untouched
    shared, _own = game_settings.import_from_maps(c, servers)
    c.game = {**shared, "XPMultiplier": 6.0, "MatingSpeedMultiplier": 2.0}
    assert game_settings.apply(servers[0], c, log.append) is True
    cfg = Path(servers[0].saved_dir) / "Config" / "WindowsServer"
    gus, game = (cfg / "GameUserSettings.ini").read_bytes().decode(), (cfg / "Game.ini").read_bytes().decode()
    assert "XPMultiplier=6.000000\r\n" in gus and "\r\n" in gus
    assert "MatingSpeedMultiplier=2.000000\r\n" in game
    assert len(gus.splitlines()) == len((FIX / "GameUserSettings.ini").read_bytes().decode().splitlines())
    assert gus.count("\r\n") == (FIX / "GameUserSettings.ini").read_bytes().count(b"\r\n")
    backups = list((data_dir / "ini-backups" / "lc").iterdir())
    assert len(backups) == 2
    assert game_settings.apply(servers[0], c, log.append) is False      # idempotent


def test_save_and_import_through_views(tmp_path, data_dir):
    servers = maps(tmp_path)
    store.save_cluster(Cluster("c", "C", "cid", "/t", "/r", defaults={"mod_ids": ["942024"]}))
    for s in servers:
        store.save_server(s)
    result = views.import_game("c")
    assert result["shared"] > 5
    assert store.mod_names(["942024"]) == {"942024": "Dino Depot"}
    views.save_game("c", {"XPMultiplier": "5", "ServerPVE": "false"})
    assert store.cluster("c").game == {"XPMultiplier": 5.0}           # PvE off is official: not stored
    with pytest.raises(views.Invalid, match="between"):
        views.save_game("c", {"XPMultiplier": "500"})
    with pytest.raises(views.Invalid, match="Not a setting"):
        views.save_game("c", {"RCONPort": "1"})
    views.save_map_game("ast", {"DinoDamageMultiplier": 2})
    assert game_settings.effective(store.cluster("c"), store.server("ast"))["DinoDamageMultiplier"] == 2.0
    views.save_map_game("ast", {})
    assert "game" not in store.server("ast").overrides
