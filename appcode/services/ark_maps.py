"""ASA maps a player can add to a cluster. Pure (stdlib only): shared by the page and the engine."""
from __future__ import annotations

# (MAP_NAME, display name, folder name, what it is)
MAPS = (
    ("TheIsland_WP", "The Island", "TheIsland", "The original ARK."),
    ("ScorchedEarth_WP", "Scorched Earth", "ScorchedEarth", "Desert survival: heat, storms, wyverns."),
    ("TheCenter_WP", "The Center", "TheCenter", "A floating island with a world underneath."),
    ("Aberration_WP", "Aberration", "Aberration", "A broken ARK underground: radiation, no flyers."),
    ("Extinction_WP", "Extinction", "Extinction", "Earth, ruined: titans and the corrupted."),
    ("Ragnarok_WP", "Ragnarok", "Ragnarok", "A huge free map with a volcano and castles."),
    ("Valguero_WP", "Valguero", "Valguero", "A free map: Aberration-style trench and an ice cave."),
    ("Astraeos_WP", "Astraeos", "Astraeos", "A large map inspired by Greek myth."),
    ("LostColony_WP", "Lost Colony", "LostColony", "Premium map."),
    ("Genesis_WP", "Genesis 1", "Genesis1", "Five biomes in a simulation, with missions."),
    ("BobsMissions_WP", "Club ARK", "ClubARK", "The social hub map."),
)


def display_name(map_name: str) -> str:
    for m, name, _f, _d in MAPS:
        if m == map_name:
            return name
    return map_name.removesuffix("_WP")


def folder_name(map_name: str) -> str:
    for m, _n, folder, _d in MAPS:
        if m == map_name:
            return folder
    return "".join(ch for ch in map_name.removesuffix("_WP") if ch.isalnum()) or "Map"
