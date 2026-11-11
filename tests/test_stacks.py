"""Per-item stack sizes: ConfigOverrideItemMaxQuantity lines in Game.ini."""
from pathlib import Path

import pytest

from engine import ark_ini, game_settings, store, views
from engine.models import Cluster

FIX = Path(__file__).parent / "fixtures"
SECTION = "/Script/ShooterGame.ShooterGameMode"


def test_stack_line_round_trip():
    line = game_settings.stack_line("PrimalItemResource_Stone_C", 1000)
    assert line == '(ItemClassString="PrimalItemResource_Stone_C",Quantity=(MaxItemQuantity=1000,bIgnoreMultiplier=true))'
    assert game_settings.parse_stack(line) == ("PrimalItemResource_Stone_C", 1000)
    assert game_settings.parse_stack('(ItemClassString="X_C", Quantity=(MaxItemQuantity=5))') == ("X_C", 5)


def test_render_writes_owns_and_clears_the_override_lines():
    game = (FIX / "Game.ini").read_bytes().decode()
    values = game_settings.official()
    values.update(game_settings.read_map_text(game, "game"))
    # Not managed (None): untouched.
    assert game_settings.render(game, "game", values, None) == game
    out = game_settings.render(game, "game", values, {"PrimalItemResource_Stone_C": 1000,
                                                      "PrimalItemResource_Wood_C": 500})
    assert game_settings.read_stacks(out) == {"PrimalItemResource_Stone_C": 1000, "PrimalItemResource_Wood_C": 500}
    assert out.count("\r\n") == game.count("\r\n") + 2 and "\n" not in out.replace("\r\n", "")
    # Same again: nothing changes.
    assert game_settings.render(out, "game", values, {"PrimalItemResource_Stone_C": 1000,
                                                      "PrimalItemResource_Wood_C": 500}) == out
    # Lines the app no longer wants (and hand-made ones) go; an empty dict clears them all.
    hand = ark_ini.replace_all(out, SECTION, "ConfigOverrideItemMaxQuantity",
                               ark_ini.read_all(out, SECTION, "ConfigOverrideItemMaxQuantity")
                               + ['(ItemClassString="Hand_C",Quantity=(MaxItemQuantity=7,bIgnoreMultiplier=true))'])
    cleared = game_settings.render(hand, "game", values, {})
    assert game_settings.read_stacks(cleared) == {}
    assert cleared == game


def test_replace_all_creates_the_section_when_missing():
    out = ark_ini.replace_all("", SECTION, "K", ["1", "2"])
    assert out == f"[{SECTION}]\nK=1\nK=2\n"


def test_save_stacks_validation(data_dir):
    store.save_cluster(Cluster("c", "C", "cid", "/t", "/r"))
    r = views.save_stacks("c", {"PrimalItemResource_Stone_C": "1000", "PrimalItemResource_Wood_C": ""})
    assert r["count"] == 1 and store.cluster("c").stacks == {"PrimalItemResource_Stone_C": 1000}
    with pytest.raises(views.Invalid, match="end in _C"):
        views.save_stacks("c", {"Stone": "10"})
    with pytest.raises(views.Invalid, match="between 1 and"):
        views.save_stacks("c", {"PrimalItemResource_Stone_C": "9999999"})
    assert views.stacks_detail("c")["managed"] is True


def test_item_catalogue():
    from services.ark_items import BY_CLASS, CATEGORIES, ITEMS, normal_stack

    assert len(BY_CLASS) == len(ITEMS)
    for cat, name, item_class, default, _note in ITEMS:
        assert cat in CATEGORIES, name
        assert item_class.endswith("_C") and views._ITEM_CLASS.match(item_class), name
        assert default >= 1
    assert "PrimalItem_WeaponEmptyCryopod_C" not in BY_CLASS        # stacking cryopods breaks them
    assert normal_stack(100, 3) == 300 and normal_stack(1, 3) == 1    # stack-of-1 items ignore it
