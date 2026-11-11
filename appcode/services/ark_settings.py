"""
The game settings players can change with sliders and switches, grouped the
way a player thinks about them, plus presets. Pure (stdlib only): the page
draws the form from it and the engine writes the ini files from it.

Each setting lives in GameUserSettings.ini ``[ServerSettings]`` ("gus") or
Game.ini ``[/Script/ShooterGame.ShooterGameMode]`` ("game"). A setting at its
official value is not written at all: ASA's default applies. Keys come from
the ARK wiki's server configuration page; ones it marks as unknown for ASA
are left out.
"""
from __future__ import annotations

GUS_SECTION = "ServerSettings"
GAME_SECTION = "/Script/ShooterGame.ShooterGameMode"

# (group id, title, icon, blurb)
GROUPS = (
    ("difficulty", "Difficulty", "mdi-sword-cross", "How strong wild creatures get."),
    ("xp", "Experience", "mdi-star-four-points-outline", "How fast survivors level up."),
    ("taming", "Taming", "mdi-paw", "How long tames take."),
    ("gathering", "Gathering", "mdi-pickaxe", "Resources from trees, rocks and creatures; crops."),
    ("breeding", "Breeding & raising", "mdi-egg-outline", "Mating, eggs, babies and imprinting."),
    ("items", "Stacks & items", "mdi-package-variant", "How much fits in a stack, and spoiling."),
    ("survival", "Survival & combat", "mdi-heart-pulse", "Hunger, thirst, damage and toughness."),
    ("daynight", "Day & night", "mdi-weather-sunset", "How long days and nights last."),
    ("rules", "Rules", "mdi-gavel", "What players can and cannot do."),
    ("travel", "Cluster travel", "mdi-transit-transfer", "What may move between the maps of this cluster."),
    ("advanced", "Advanced", "mdi-tune-variant", "Rarely needed."),
)

# (key, file, group, kind, default, min, max, step, label, help, lower_is_faster)
# kind: "mult" (a × multiplier slider), "float", "int", "bool".
_ROWS = (
    # Difficulty
    ("OverrideOfficialDifficulty", "gus", "difficulty", "float", 0.0, 0.0, 10.0, 0.5, "Difficulty",
     "With difficulty offset 1.0, wild creatures reach level 30 × this (5 = level 150, 0 = the map's own).",
     False),
    # Experience
    ("XPMultiplier", "gus", "xp", "mult", 1.0, 0.5, 20.0, 0.5, "All experience", "", False),
    ("KillXPMultiplier", "game", "xp", "mult", 1.0, 0.0, 10.0, 0.5, "From kills", "", False),
    ("HarvestXPMultiplier", "game", "xp", "mult", 1.0, 0.0, 10.0, 0.5, "From gathering", "", False),
    ("CraftXPMultiplier", "game", "xp", "mult", 1.0, 0.0, 10.0, 0.5, "From crafting", "", False),
    ("GenericXPMultiplier", "game", "xp", "mult", 1.0, 0.0, 10.0, 0.5, "Over time", "", False),
    ("SpecialXPMultiplier", "game", "xp", "mult", 1.0, 0.0, 10.0, 0.5, "From special events", "", False),
    # Taming
    ("TamingSpeedMultiplier", "gus", "taming", "mult", 1.0, 1.0, 20.0, 0.5, "Taming speed", "", False),
    ("DinoCharacterFoodDrainMultiplier", "gus", "taming", "mult", 1.0, 0.1, 5.0, 0.1, "Creature hunger",
     "How fast creatures get hungry (also changes taming time).", True),
    ("WildDinoCharacterFoodDrainMultiplier", "game", "taming", "mult", 1.0, 0.1, 5.0, 0.1,
     "Wild creature hunger", "", True),
    # Gathering
    ("HarvestAmountMultiplier", "gus", "gathering", "mult", 1.0, 0.5, 20.0, 0.5, "Resources per hit", "", False),
    ("HarvestHealthMultiplier", "gus", "gathering", "mult", 1.0, 0.5, 5.0, 0.1, "Resource toughness",
     "Trees and rocks take more hits, so they give more in total.", False),
    ("ResourcesRespawnPeriodMultiplier", "gus", "gathering", "mult", 1.0, 0.1, 5.0, 0.1, "Regrow time",
     "How long trees, rocks and bushes take to come back.", True),
    ("DinoHarvestingDamageMultiplier", "game", "gathering", "mult", 3.2, 0.5, 10.0, 0.1,
     "Creature gathering", "How much creatures gather per hit.", False),
    ("CropGrowthSpeedMultiplier", "game", "gathering", "mult", 1.0, 1.0, 20.0, 0.5, "Crop growth", "", False),
    # Breeding & raising
    ("MatingIntervalMultiplier", "game", "breeding", "mult", 1.0, 0.01, 2.0, 0.01, "Time between matings",
     "How soon a creature can mate again.", True),
    ("MatingSpeedMultiplier", "game", "breeding", "mult", 1.0, 1.0, 10.0, 0.5, "Mating speed", "", False),
    ("EggHatchSpeedMultiplier", "game", "breeding", "mult", 1.0, 1.0, 100.0, 1.0, "Egg hatching & gestation", "",
     False),
    ("BabyMatureSpeedMultiplier", "game", "breeding", "mult", 1.0, 1.0, 100.0, 1.0, "Baby growing up", "", False),
    ("BabyFoodConsumptionSpeedMultiplier", "game", "breeding", "mult", 1.0, 0.1, 2.0, 0.05, "Baby hunger", "",
     True),
    ("BabyCuddleIntervalMultiplier", "game", "breeding", "mult", 1.0, 0.01, 1.0, 0.01, "Time between cuddles",
     "Imprinting asks for attention this much more often when lower. Pair it with baby growing up.", True),
    ("BabyImprintAmountMultiplier", "game", "breeding", "mult", 1.0, 0.5, 20.0, 0.5, "Imprint per cuddle", "",
     False),
    ("BabyCuddleGracePeriodMultiplier", "game", "breeding", "mult", 1.0, 0.5, 10.0, 0.5, "Cuddle grace period",
     "How long a missed cuddle is forgiven.", False),
    ("BabyImprintingStatScaleMultiplier", "game", "breeding", "mult", 1.0, 0.0, 2.0, 0.1, "Imprint stat bonus",
     "0 turns the imprinting bonus off.", False),
    ("LayEggIntervalMultiplier", "game", "breeding", "mult", 1.0, 0.1, 2.0, 0.1, "Unfertilized egg laying",
     "Time between eggs.", True),
    ("AllowAnyoneBabyImprintCuddle", "gus", "breeding", "bool", False, None, None, None,
     "Anyone can cuddle babies", "Not only the survivor who imprints.", False),
    ("DisableImprintDinoBuff", "gus", "breeding", "bool", False, None, None, None,
     "No rider bonus from imprinting", "", False),
    # Stacks & items
    ("ItemStackSizeMultiplier", "gus", "items", "mult", 1.0, 1.0, 20.0, 0.5, "Stack sizes",
     "Items still weigh the same each. Some items ignore it. Lowering it later can shrink existing stacks.",
     False),
    ("GlobalSpoilingTimeMultiplier", "game", "items", "mult", 1.0, 1.0, 20.0, 0.5, "Food lasts",
     "Higher: food spoils more slowly.", False),
    ("GlobalItemDecompositionTimeMultiplier", "game", "items", "mult", 1.0, 1.0, 20.0, 0.5, "Dropped items last",
     "Items on the ground and death bags.", False),
    # Survival & combat
    ("PlayerCharacterFoodDrainMultiplier", "gus", "survival", "mult", 1.0, 0.1, 3.0, 0.1, "Survivor hunger", "",
     True),
    ("PlayerCharacterWaterDrainMultiplier", "gus", "survival", "mult", 1.0, 0.1, 3.0, 0.1, "Survivor thirst", "",
     True),
    ("PlayerCharacterStaminaDrainMultiplier", "gus", "survival", "mult", 1.0, 0.1, 3.0, 0.1, "Survivor stamina use",
     "", True),
    ("PlayerCharacterHealthRecoveryMultiplier", "gus", "survival", "mult", 1.0, 0.5, 5.0, 0.1, "Survivor healing",
     "", False),
    ("PlayerDamageMultiplier", "gus", "survival", "mult", 1.0, 0.5, 5.0, 0.1, "Survivor damage", "", False),
    ("PlayerResistanceMultiplier", "gus", "survival", "mult", 1.0, 0.1, 2.0, 0.05, "Damage survivors take",
     "Lower: survivors are tougher.", True),
    ("DinoDamageMultiplier", "gus", "survival", "mult", 1.0, 0.1, 5.0, 0.1, "Wild creature damage", "", False),
    ("DinoResistanceMultiplier", "gus", "survival", "mult", 1.0, 0.1, 2.0, 0.05, "Damage wild creatures take",
     "Lower: wild creatures are tougher.", True),
    ("StructureResistanceMultiplier", "gus", "survival", "mult", 1.0, 0.1, 2.0, 0.05, "Damage buildings take",
     "Lower: buildings are tougher.", True),
    # Day & night
    ("DayCycleSpeedScale", "gus", "daynight", "mult", 1.0, 0.1, 5.0, 0.1, "Whole day speed",
     "1 = a day takes about 50 real minutes.", False),
    ("DayTimeSpeedScale", "gus", "daynight", "mult", 1.0, 0.1, 5.0, 0.1, "Daytime speed",
     "Lower: longer days.", False),
    ("NightTimeSpeedScale", "gus", "daynight", "mult", 1.0, 0.1, 5.0, 0.1, "Night speed",
     "Higher: shorter nights.", False),
    # Rules
    ("ServerPVE", "gus", "rules", "bool", False, None, None, None, "Player vs environment only",
     "No fighting between tribes.", False),
    ("ShowMapPlayerLocation", "gus", "rules", "bool", True, None, None, None, "Show yourself on the map", "", False),
    ("AllowThirdPersonPlayer", "gus", "rules", "bool", True, None, None, None, "Third-person view", "", False),
    ("ServerCrosshair", "gus", "rules", "bool", True, None, None, None, "Crosshair", "", False),
    ("AllowHitMarkers", "gus", "rules", "bool", True, None, None, None, "Hit markers", "", False),
    ("ShowFloatingDamageText", "gus", "rules", "bool", False, None, None, None, "Damage numbers", "", False),
    ("AllowFlyerCarryPvE", "gus", "rules", "bool", False, None, None, None, "Flyers can carry wild creatures", "",
     False),
    ("AlwaysAllowStructurePickup", "gus", "rules", "bool", False, None, None, None,
     "Pick up buildings any time", "Normally only for 30 seconds after placing.", False),
    ("DisableStructureDecayPvE", "gus", "rules", "bool", False, None, None, None,
     "Buildings never decay", "", False),
    ("AllowCaveBuildingPvE", "gus", "rules", "bool", False, None, None, None, "Build in caves", "", False),
    ("bAllowUnlimitedRespecs", "game", "rules", "bool", False, None, None, None,
     "Unlimited Mindwipe Tonic", "No cooldown on resetting your levels.", False),
    ("bAllowSpeedLeveling", "game", "rules", "bool", False, None, None, None, "Level up movement speed", "", False),
    ("bAllowFlyerSpeedLeveling", "game", "rules", "bool", False, None, None, None,
     "Level up flyer speed", "Needs \"Level up movement speed\" too.", False),
    ("bDisableStructurePlacementCollision", "game", "rules", "bool", False, None, None, None,
     "Buildings may clip into the ground", "", False),
    # Cluster travel
    ("PreventDownloadSurvivors", "gus", "travel", "bool", False, None, None, None, "Block arriving survivors",
     "", False),
    ("PreventDownloadDinos", "gus", "travel", "bool", False, None, None, None, "Block arriving creatures", "",
     False),
    ("PreventDownloadItems", "gus", "travel", "bool", False, None, None, None, "Block arriving items", "", False),
    ("PreventUploadSurvivors", "gus", "travel", "bool", False, None, None, None, "Block leaving survivors", "",
     False),
    ("PreventUploadDinos", "gus", "travel", "bool", False, None, None, None, "Block leaving creatures", "", False),
    ("PreventUploadItems", "gus", "travel", "bool", False, None, None, None, "Block leaving items", "", False),
    # Advanced
    ("DifficultyOffset", "gus", "advanced", "float", 1.0, 0.0, 1.0, 0.05, "Difficulty offset",
     "Scales the difficulty above; 1.0 makes wild levels reach 30 × difficulty.", False),
    ("ResourceNoReplenishRadiusPlayers", "game", "advanced", "mult", 1.0, 0.0, 2.0, 0.1,
     "Regrow distance from survivors", "Lower: resources regrow closer to players.", False),
    ("ResourceNoReplenishRadiusStructures", "game", "advanced", "mult", 1.0, 0.0, 2.0, 0.1,
     "Regrow distance from buildings", "Lower: resources regrow closer to buildings.", False),
)

SETTINGS = {
    r[0]: {"key": r[0], "file": r[1], "group": r[2], "kind": r[3], "default": r[4], "min": r[5],
           "max": r[6], "step": r[7], "label": r[8], "help": r[9], "lower_is_faster": r[10]}
    for r in _ROWS
}
ORDER = [r[0] for r in _ROWS]


def section_of(file: str) -> str:
    return GUS_SECTION if file == "gus" else GAME_SECTION


# Presets: values over Official. "Official" is every default.
PRESETS = (
    ("official", "Official", "The way ARK ships: 1× everything.", {}),
    ("casual", "Casual 3×", "Three times faster progress; good for a few evenings a week.", {
        "XPMultiplier": 3.0, "TamingSpeedMultiplier": 3.0, "HarvestAmountMultiplier": 3.0,
        "EggHatchSpeedMultiplier": 3.0, "BabyMatureSpeedMultiplier": 3.0, "MatingIntervalMultiplier": 0.5,
        "BabyCuddleIntervalMultiplier": 0.33, "BabyImprintAmountMultiplier": 3.0,
        "ItemStackSizeMultiplier": 2.0, "CropGrowthSpeedMultiplier": 3.0,
    }),
    ("boosted", "Boosted 10×", "Ten times faster: big tames and breeding lines without the grind.", {
        "XPMultiplier": 10.0, "TamingSpeedMultiplier": 10.0, "HarvestAmountMultiplier": 10.0,
        "EggHatchSpeedMultiplier": 20.0, "BabyMatureSpeedMultiplier": 20.0, "MatingIntervalMultiplier": 0.1,
        "BabyCuddleIntervalMultiplier": 0.05, "BabyImprintAmountMultiplier": 10.0,
        "ItemStackSizeMultiplier": 5.0, "CropGrowthSpeedMultiplier": 10.0, "MatingSpeedMultiplier": 5.0,
    }),
    ("solo", "Solo / small tribe", "Gentle rates and no decay, for one to a few players.", {
        "XPMultiplier": 2.0, "TamingSpeedMultiplier": 5.0, "HarvestAmountMultiplier": 3.0,
        "EggHatchSpeedMultiplier": 10.0, "BabyMatureSpeedMultiplier": 10.0, "MatingIntervalMultiplier": 0.2,
        "BabyCuddleIntervalMultiplier": 0.1, "BabyImprintAmountMultiplier": 10.0,
        "AllowAnyoneBabyImprintCuddle": True, "ItemStackSizeMultiplier": 3.0, "DisableStructureDecayPvE": True,
        "AlwaysAllowStructurePickup": True, "AllowFlyerCarryPvE": True, "ServerPVE": True,
    }),
)


def preset(preset_id: str) -> dict:
    for pid, _name, _blurb, values in PRESETS:
        if pid == preset_id:
            return dict(values)
    raise KeyError(preset_id)


def normalize(key: str, value):
    """A value from a form or an ini file, as the setting's type; raises ValueError."""
    s = SETTINGS[key]
    if s["kind"] == "bool":
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in ("true", "1", "on", "yes"):
            return True
        if text in ("false", "0", "off", "no"):
            return False
        raise ValueError(f"{s['label']}: on or off.")
    try:
        number = float(str(value).strip())
    except ValueError:
        raise ValueError(f"{s['label']}: a number, please.")
    if s["kind"] == "int":
        number = int(round(number))
    return number


def in_range(key: str, value) -> bool:
    s = SETTINGS[key]
    if s["kind"] == "bool":
        return isinstance(value, bool)
    return s["min"] <= value <= s["max"]


def is_default(key: str, value) -> bool:
    d = SETTINGS[key]["default"]
    if SETTINGS[key]["kind"] == "bool":
        return bool(value) == bool(d)
    return abs(float(value) - float(d)) < 1e-9


def ini_value(key: str, value) -> str:
    """How ASA writes it: True/False, 6-decimal floats."""
    if SETTINGS[key]["kind"] == "bool":
        return "True" if value else "False"
    if SETTINGS[key]["kind"] == "int":
        return str(int(value))
    return f"{float(value):.6f}"


def differs(values: dict) -> list:
    """Keys whose value is not the official one."""
    return [k for k in ORDER if k in values and not is_default(k, values[k])]


def wild_level(values: dict) -> int | None:
    """Max wild level implied by difficulty and offset (None: the map's own)."""
    diff = float(values.get("OverrideOfficialDifficulty", 0.0) or 0.0)
    if diff <= 0:
        return None
    offset = float(values.get("DifficultyOffset", 1.0))
    return int(round(30 * (offset * (diff - 0.5) + 0.5)))
