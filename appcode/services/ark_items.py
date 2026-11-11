"""
Items whose stack size players commonly change. Pure: shared by the page and
the engine. Class names and ASA default stacks were checked against the ARK
wiki and an ASA item database (2026-11-11); ASA kept ASE's stack sizes.

Items that stack to 1 by default ignore the global ItemStackSizeMultiplier,
so they only change through a per-item size here.
"""
from __future__ import annotations

# (category, display name, item class, ASA default stack, note)
ITEMS = (
    ("Resources", "Stone", "PrimalItemResource_Stone_C", 100, ""),
    ("Resources", "Wood", "PrimalItemResource_Wood_C", 100, ""),
    ("Resources", "Thatch", "PrimalItemResource_Thatch_C", 200, ""),
    ("Resources", "Fiber", "PrimalItemResource_Fibers_C", 300, ""),
    ("Resources", "Flint", "PrimalItemResource_Flint_C", 100, ""),
    ("Resources", "Metal", "PrimalItemResource_Metal_C", 300, ""),
    ("Resources", "Crystal", "PrimalItemResource_Crystal_C", 100, ""),
    ("Resources", "Obsidian", "PrimalItemResource_Obsidian_C", 100, ""),
    ("Resources", "Oil", "PrimalItemResource_Oil_C", 100, ""),
    ("Resources", "Silica Pearls", "PrimalItemResource_Silicon_C", 100, ""),
    ("Resources", "Black Pearl", "PrimalItemResource_BlackPearl_C", 200, ""),
    ("Resources", "Organic Polymer", "PrimalItemResource_Polymer_Organic_C", 20, ""),
    ("Resources", "Chitin", "PrimalItemResource_Chitin_C", 100, ""),
    ("Resources", "Keratin", "PrimalItemResource_Keratin_C", 100, ""),
    ("Resources", "Hide", "PrimalItemResource_Hide_C", 200, ""),
    ("Resources", "Pelt", "PrimalItemResource_Pelt_C", 200, ""),
    ("Resources", "Sulfur", "PrimalItemResource_Sulfur_C", 100, ""),
    ("Resources", "Element Shard", "PrimalItemResource_ElementShard_C", 1000, ""),
    ("Resources", "Angler Gel", "PrimalItemResource_AnglerGel_C", 100, ""),
    ("Resources", "Sap", "PrimalItemResource_Sap_C", 30, ""),
    ("Resources", "Silk", "PrimalItemResource_Silk_C", 200, ""),
    ("Resources", "Sand", "PrimalItemResource_Sand_C", 100, ""),
    ("Resources", "Clay", "PrimalItemResource_Clay_C", 100, ""),
    ("Resources", "Rare Flower", "PrimalItemResource_RareFlower_C", 100, ""),
    ("Resources", "Rare Mushroom", "PrimalItemResource_RareMushroom_C", 100, ""),
    ("Resources", "Congealed Gas Ball", "PrimalItemResource_Gas_C", 100, ""),
    ("Refined", "Metal Ingot", "PrimalItemResource_MetalIngot_C", 300, ""),
    ("Refined", "Polymer", "PrimalItemResource_Polymer_C", 100, ""),
    ("Refined", "Cementing Paste", "PrimalItemResource_ChitinPaste_C", 100, ""),
    ("Refined", "Charcoal", "PrimalItemResource_Charcoal_C", 100, ""),
    ("Refined", "Sparkpowder", "PrimalItemResource_Sparkpowder_C", 100, ""),
    ("Refined", "Gunpowder", "PrimalItemResource_Gunpowder_C", 100, ""),
    ("Refined", "Electronics", "PrimalItemResource_Electronics_C", 100, ""),
    ("Refined", "Element", "PrimalItemResource_Element_C", 100, ""),
    ("Refined", "Absorbent Substrate", "PrimalItemResource_SubstrateAbsorbent_C", 100, ""),
    ("Food", "Raw Meat", "PrimalItemConsumable_RawMeat_C", 40, ""),
    ("Food", "Cooked Meat", "PrimalItemConsumable_CookedMeat_C", 50, ""),
    ("Food", "Raw Prime Meat", "PrimalItemConsumable_RawPrimeMeat_C", 1,
     "Stacks to 1 normally: only a size here changes it."),
    ("Food", "Cooked Prime Meat", "PrimalItemConsumable_CookedPrimeMeat_C", 30, ""),
    ("Food", "Raw Mutton", "PrimalItemConsumable_RawMutton_C", 1,
     "Stacks to 1 normally. Players report a size here may not take effect on servers."),
    ("Food", "Cooked Lamb Chop", "PrimalItemConsumable_CookedLambChop_C", 30, ""),
    ("Food", "Raw Fish Meat", "PrimalItemConsumable_RawMeat_Fish_C", 40, ""),
    ("Food", "Cooked Fish Meat", "PrimalItemConsumable_CookedMeat_Fish_C", 50, ""),
    ("Food", "Raw Prime Fish Meat", "PrimalItemConsumable_RawPrimeMeat_Fish_C", 1,
     "Stacks to 1 normally: only a size here changes it."),
    ("Food", "Cooked Prime Fish Meat", "PrimalItemConsumable_CookedPrimeMeat_Fish_C", 30, ""),
    ("Food", "Mejoberry", "PrimalItemConsumable_Berry_Mejoberry_C", 100, ""),
    ("Food", "Tintoberry", "PrimalItemConsumable_Berry_Tintoberry_C", 100, ""),
    ("Food", "Amarberry", "PrimalItemConsumable_Berry_Amarberry_C", 100, ""),
    ("Food", "Azulberry", "PrimalItemConsumable_Berry_Azulberry_C", 100, ""),
    ("Food", "Narcoberry", "PrimalItemConsumable_Berry_Narcoberry_C", 100, ""),
    ("Food", "Stimberry", "PrimalItemConsumable_Berry_Stimberry_C", 100, ""),
    ("Ammo & consumables", "Narcotic", "PrimalItemConsumable_Narcotic_C", 100, ""),
    ("Ammo & consumables", "Stimulant", "PrimalItemConsumable_Stimulant_C", 100, ""),
    ("Ammo & consumables", "Fertilizer", "PrimalItemConsumable_Fertilizer_Compost_C", 1,
     "Careful: a crop plot uses up a whole stack of fertilizer at once."),
    ("Ammo & consumables", "Simple Bullet", "PrimalItemAmmo_SimpleBullet_C", 100, ""),
    ("Ammo & consumables", "Advanced Rifle Bullet", "PrimalItemAmmo_AdvancedRifleBullet_C", 100, ""),
    ("Ammo & consumables", "Stone Arrow", "PrimalItemAmmo_ArrowStone_C", 100, ""),
    ("Ammo & consumables", "Tranquilizer Arrow", "PrimalItemAmmo_ArrowTranq_C", 100, ""),
    ("Ammo & consumables", "Tranq Dart", "PrimalItemAmmo_TranqDart_C", 100, ""),
    ("Ammo & consumables", "Shocking Tranquilizer Dart", "PrimalItemAmmo_RefinedTranqDart_C", 100, ""),
)

CATEGORIES = ("Resources", "Refined", "Food", "Ammo & consumables")
BY_CLASS = {i[2]: i for i in ITEMS}


def normal_stack(default: int, multiplier: float) -> int:
    """What a stack holds without a per-item size: items of 1 ignore the multiplier."""
    if default <= 1:
        return default
    return max(1, int(default * float(multiplier or 1.0)))
