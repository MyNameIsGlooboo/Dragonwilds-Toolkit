"""Container slot counts and item stack sizes for RuneScape: Dragonwilds.

Where the numbers come from
---------------------------
Cooked assets use *unversioned* properties: every export starts with a header
of fragments (u16: skip 0-6, has-zeroes 7, is-last 8, value count 9-15),
an optional zero mask, then the raw values in schema order. Names are not
stored, so fields were identified by comparing assets and checking against
save files.

Container slots: the inventory component of a container blueprint overrides
one int field of the native class /Script/Dominion/InventoryComponent. It is
schema index 5 on InventoryComponent itself and index 6 on its blueprint
subclass BP_Components_WorldItemInventory (one blueprint variable in front).
Values read straight from the game (patch of 2026-09-15):
    BP_BaseBuilding_Chest          48    BP_BaseBuilding_Chest_Iron  64
    BP_BaseBuilding_Chest_Small    32    BP_BaseBuilding_Crate       16
    BP_BaseBuilding_PersonalChest  40    BP_PlayerGravestone         32
    BP_LootChest_Base (all loot chests inherit, none override)       16
A real world save agrees: 4 small chests there are filled up to slot 31 and
5 crates up to slot 15; nothing is ever used at or past these counts.
"MaxSlotIndex" in a save is only the highest used slot, never the size.

Processing stations keep three inventories. Fuel and Catalyst sizes are
component overrides (as above). Input ("Resources") slots are not overridden
in the blueprints; they come from DT_ProcessingStationDataTable (row field 6
= input slots, field 7 = fuel slots), e.g. Furnace 2, Cauldron 3.

Stack sizes: the item data assets (ITEM_*.uasset) store max stack as an int
24 schema slots before the item's save ID string (the 22-char persistence ID)
in every native item class; the weight float sits 20 slots before it. When
the field is not written the class default applies (gear: not stackable).
build_stack_table() reads it with a structural check: the bytes between the
value and the save ID must parse exactly as the properties the header says
are present. Against the third-party DB this gave no disagreements on the
items it could read (see tests/test_capacity.py and the research notes).
"""
import re
import struct

# --------------------------------------------------------------------------
# container slot counts

# class name -> slots. Only values read from game files (and never contradicted
# by save files) go here.
CONTAINER_SLOTS = {
    "BP_BaseBuilding_Chest_C": 48,
    "BP_BaseBuilding_Chest_Iron_C": 64,
    "BP_BaseBuilding_Chest_Small_C": 32,
    "BP_BaseBuilding_Crate_C": 16,
    "BP_BaseBuilding_PersonalChest_C": 40,
    "BP_BaseBuilding_Barrel_C": 8,
    "BP_BaseBuilding_LumberStorage_C": 48,
    "BP_BaseBuilding_OreStorage_C": 48,
    "BP_BaseBuilding_FishingBarrel_C": 48,
    "BP_BaseBuilding_TackleBox_C": 48,
    "BP_BaseBuilding_WeaponRack_C": 6,
    "BP_BaseBuilding_ArmourMannequin_C": 5,
    "BP_BaseBuilding_CapeRack_C": 1,
    "BP_BaseBuilding_CapeHook_C": 1,
    "BP_PlayerGravestone_C": 32,
    "BP_Dungeon_Treasure_Chest_C": 16,
    "BP_LootChest_Base_C": 16,
    "BP_BuriedChest_Base_C": 16,
    "BP_SpectralChest_Base_C": 16,
    "BP_DR_BuriedChest_C": 16,
    "BP_BuriedLever_DR_C": 16,
}

# Families whose children inherit the parent's InventoryComponent unchanged
# (every child blueprint checked has an empty override template).
INHERITED_PREFIXES = {
    "BP_LootChest_Prefab_": 16,
    "BP_BuriedChest_": 16,
    "BP_SpectralChest_": 16,
}

# Processing stations: class -> {inventory property: slots}. Fuel and Catalyst
# are blueprint values; Resources comes from the station data table.
STATION_SLOTS = {
    "BP_Station_Campfire_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_CharcoalKiln_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Grindstone_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Loom_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Sawmill_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Spinning_Wheel_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Tanner_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Grill_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Stonecutter_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Smelter_C": {"ResourcesInventory": 2, "FuelInventory": 1, "CatalystInventory": 2},
    "BP_Station_AdvancedSmelter_C": {"ResourcesInventory": 2, "FuelInventory": 1, "CatalystInventory": 2},
    "BP_Station_Cauldron_C": {"ResourcesInventory": 3, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_AdvancedTannery_C": {"ResourcesInventory": 3, "FuelInventory": 1, "CatalystInventory": 1},
    "BP_Station_Spinning_Wheel_Advanced_C": {"ResourcesInventory": 2, "FuelInventory": 2, "CatalystInventory": 2},
    "BP_Station_FermentationBarrel_C": {"ResourcesInventory": 1, "FuelInventory": 1, "CatalystInventory": 1},
}
# Rows of DT_ProcessingStationDataTable: (input slots, fuel slots). A station
# whose fuel value is 0 does not burn fuel, even though its FuelInventory
# component still has 1 slot.
STATION_TABLE = {
    "BP_Station_Campfire_C": (1, 1), "BP_Station_CharcoalKiln_C": (1, 0),
    "BP_Station_Grindstone_C": (1, 0), "BP_Station_Loom_C": (1, 0),
    "BP_Station_Sawmill_C": (1, 0), "BP_Station_Spinning_Wheel_C": (1, 0),
    "BP_Station_Tanner_C": (1, 0), "BP_Station_Smelter_C": (2, 1),
    "BP_Station_Cauldron_C": (3, 1), "BP_Station_Grill_C": (1, 0),
    "BP_Station_Stonecutter_C": (1, 0), "BP_Station_AdvancedSmelter_C": (2, 1),
    "BP_Station_FermentationBarrel_C": (1, 1), "BP_Station_AdvancedTannery_C": (3, 0),
    "BP_Station_Spinning_Wheel_Advanced_C": (2, 0),
}

# Character save (GameProgress.Inventory): one 152-slot inventory split into
# pages (BP_PlayerController's inventory component: total 152; page sizes
# 24 runes, 24 ammo, 72 quest items; the rest is the backpack). Page starts
# match the slots the game uses in the character save (runes from 32, ammo
# from 56, quest items from 80).
PLAYER_INVENTORY_SLOTS = 152
PLAYER_INVENTORY_PAGES = {
    "backpack": (0, 32),      # (first slot, slot count)
    "runes": (32, 24),
    "ammo": (56, 24),
    "quest": (80, 72),
}

_CONFIDENCE = {
    "game": "game file",
    "inherited": "game file (inherited from parent blueprint)",
    "table": "game file (station data table)",
    "lower": "save only: at least this many",
    "unknown": "unknown",
    "conflict": "conflict: save uses a slot past the game-file size",
}


def slots_for_class(cls, observed_max_slot=None, prop=None):
    """(slots or None, confidence) for a container class.

    prop: the inventory property for stations ("FuelInventory", ...; a dotted
    save path such as "FuelInventory.JsonInventory" works too).
    observed_max_slot: highest slot index seen in a save, used as a lower
    bound when the game files say nothing, and as a sanity check otherwise."""
    slots, how = None, "unknown"
    if cls in CONTAINER_SLOTS:
        slots, how = CONTAINER_SLOTS[cls], "game"
    elif cls in STATION_SLOTS:
        key = (prop or "").split(".")[0]
        if key in STATION_SLOTS[cls]:
            slots = STATION_SLOTS[cls][key]
            how = "table" if key == "ResourcesInventory" else "game"
    else:
        for prefix, n in INHERITED_PREFIXES.items():
            if cls.startswith(prefix):
                slots, how = n, "inherited"
                break
    if observed_max_slot is not None and observed_max_slot >= 0:
        if slots is None:
            return observed_max_slot + 1, _CONFIDENCE["lower"]
        if observed_max_slot >= slots:
            return observed_max_slot + 1, _CONFIDENCE["conflict"]
    return slots, _CONFIDENCE[how]


# --------------------------------------------------------------------------
# reading unversioned exports

def _exports(buf):
    """Exports of a zen package: dicts with name, data offset and size."""
    from .gamedata import _name_map
    hsize = struct.unpack_from("<I", buf, 4)[0]
    exp_off, bundle_off = struct.unpack_from("<ii", buf, 32)
    names = _name_map(buf)
    out = []
    for k in range((bundle_off - exp_off) // 72):
        off, size, ni = struct.unpack_from("<QQI", buf, exp_off + k * 72)
        out.append({"name": names[ni] if ni < len(names) else "", "pos": hsize + off, "size": size})
    return out


def _header(d):
    """(present schema indices, zeroed indices, offset of first value)."""
    pos = 0
    frags = []
    while True:
        (v,) = struct.unpack_from("<H", d, pos)
        pos += 2
        frags.append((v & 0x7F, bool(v & 0x80), v >> 9))
        if v & 0x100:
            break
    nzero = sum(n for _, z, n in frags if z)
    zbits = 0
    if nzero:
        nbytes = 1 if nzero <= 8 else 2 if nzero <= 16 else ((nzero + 31) // 32) * 4
        zbits = int.from_bytes(d[pos:pos + nbytes], "little")
        pos += nbytes
    props, zeros = [], set()
    idx = zi = 0
    for skip, has_zero, num in frags:
        idx += skip
        for _ in range(num):
            props.append(idx)
            if has_zero:
                if zbits >> zi & 1:
                    zeros.add(idx)
                zi += 1
            idx += 1
    return props, zeros, pos


def component_slots(buf, export_name, field_index):
    """Slot count written in a component template, or None if not overridden."""
    for e in _exports(buf):
        if e["name"] != export_name:
            continue
        d = buf[e["pos"]:e["pos"] + e["size"]]
        props, zeros, pos = _header(d)
        if props and props[0] == field_index and field_index not in zeros:
            return struct.unpack_from("<i", d, pos)[0]
    return None


# (package, export, schema index) behind each CONTAINER_SLOTS value.
_SOURCES = {cls: (cls[:-2], "BP_Components_WorldItemInventory_GEN_VARIABLE", 6)
            for cls in CONTAINER_SLOTS if cls.startswith(("BP_BaseBuilding_", "BP_PlayerGravestone",
                                                          "BP_Dungeon_"))}
_SOURCES.update({cls: (cls[:-2], "InventoryComponent", 5)
                 for cls in ("BP_LootChest_Base_C", "BP_BuriedChest_Base_C", "BP_SpectralChest_Base_C",
                             "BP_DR_BuriedChest_C", "BP_BuriedLever_DR_C")})


def scan_container_slots(gf):
    """Re-read CONTAINER_SLOTS from the game files (to check after a patch)."""
    out = {}
    for cls, (pkg, export, idx) in _SOURCES.items():
        hits = gf.find(lambda p, n=pkg: p.endswith("/" + n + ".uasset"))
        if hits:
            out[cls] = component_slots(gf.read(hits[0]), export, idx)
    return out


# --------------------------------------------------------------------------
# stack sizes and weights from item assets

_ID_RX = re.compile(rb"\x17\x00\x00\x00[A-Za-z0-9_-]{22}\x00")
# Value types of the item fields between max stack and the save ID, keyed by
# distance (in schema slots) before the save ID. Inferred from ~600 items.
_TAIL_TYPES = {23: "i4", 21: "b1", 20: "i4", 19: "tag", 18: "tag", 17: "tag", 16: "names",
               15: "tag", 14: "i4", 13: "tag", 12: "s14", 8: "i4", 7: "tag", 6: "i4",
               5: "names", 3: "i4", 1: "i4"}
STACK_REL = 24    # max stack: int, 24 slots before the save ID
WEIGHT_REL = 20   # weight: float, 20 slots before the save ID


def _size(t, d, p):
    if t == "i4":
        return 4
    if t == "b1":
        return 1
    if t == "tag":    # struct with one FName
        return 10 if d[p:p + 2] == b"\x00\x03" else None
    if t == "s14":    # struct with an object ref and an FName
        return 14 if d[p:p + 2] == b"\x00\x05" else None
    if t == "names":  # array of FName
        if p + 4 > len(d):
            return None
        (n,) = struct.unpack_from("<i", d, p)
        return 4 + 8 * n if 0 <= n < 50 else None
    return None


def _parses_to(d, p, end, rels):
    for r in rels:
        t = _TAIL_TYPES.get(r)
        s = _size(t, d, p) if t else None
        if s is None:
            return False
        p += s
        if p > end:
            return False
    return p == end


def _item_export(buf, asset):
    for e in _exports(buf):
        if e["name"] == asset:
            return buf[e["pos"]:e["pos"] + e["size"]]
    return None


def _field(d, rel, fmt="<i"):
    """('value', v) / ('absent', None) / ('unknown', None) for the int/float
    field `rel` schema slots before the save ID."""
    props, zeros, start = _header(d)
    ids = [m.start() for m in _ID_RX.finditer(d)]
    if not ids:
        return "unknown", None
    idpos = ids[-1]
    found = set()
    absent = False
    # The save ID is followed by the internal name and sometimes a few more
    # fields, so try the last few schema indices as the ID's own index.
    for k in range(1, 6):
        sid = props[-1] - k
        if sid not in props:
            continue
        target = sid - rel
        if target not in props:
            absent = True
            continue
        if target in zeros:
            found.add(0)
            continue
        rels = [sid - p for p in props if target < p < sid and p not in zeros]
        for p in range(start, idpos - 3):
            if _parses_to(d, p + 4, idpos, rels):
                found.add(struct.unpack_from(fmt, d, p)[0])
    if len(found) == 1:
        return "value", found.pop()
    if not found and absent:
        return "absent", None
    return "unknown", None


def _anchor_stack_pkg(buf, d):
    """Fallback: max stack follows the item's drop-mesh soft path (plus an
    optional bool). Only values >= 10 are trusted this way."""
    from .gamedata import _name_map
    names = _name_map(buf)
    hits = []
    for p in range(0, len(d) - 34):
        a, z1 = struct.unpack_from("<II", d, p)
        if z1 or a >= len(names) or not names[a].startswith("/"):
            continue
        b, z2, z3 = struct.unpack_from("<III", d, p + 16)
        if b < len(names) and names[a].endswith("/" + names[b]) and z2 == 0 and z3 == 0:
            v0 = struct.unpack_from("<i", d, p + 28)[0]
            v1 = struct.unpack_from("<i", d, p + 29)[0]
            hits.append(v1 if d[p + 28] in (0, 1) and 0 < v1 <= 100000 else v0)
    if len(hits) == 1 and 10 <= hits[0] <= 100000:
        return hits[0]
    return None


def read_item_stats(buf, asset):
    """{'maxStack': int|None, 'stackSource': str, 'weight': float|None} for one item package.

    stackSource: 'game' (read and structure-checked), 'game-anchor' (read next
    to the drop mesh, values >= 10 only), 'default' (not written: class
    default, i.e. not stackable for gear) or 'unknown'."""
    d = _item_export(buf, asset)
    if d is None:
        return {"maxStack": None, "stackSource": "unknown", "weight": None}
    how, v = _field(d, STACK_REL)
    out = {"maxStack": None, "stackSource": "unknown"}
    if how == "value" and 0 < v <= 100000:
        out = {"maxStack": v, "stackSource": "game"}
    else:
        a = _anchor_stack_pkg(buf, d)
        if a is not None:
            out = {"maxStack": a, "stackSource": "game-anchor"}
        elif how == "absent":
            out = {"maxStack": None, "stackSource": "default"}
    how, w = _field(d, WEIGHT_REL, "<f")
    out["weight"] = round(w, 4) if how == "value" and 0 <= w < 10000 else None
    return out


def build_stack_table(gf, log=None):
    """{asset name: maxStack} for every ITEM_* asset where the game file states it.

    Items whose stack is not written (gear, some blueprint items whose value
    comes from a parent blueprint) are left out; use the item database for
    those. read_item_stats() gives the per-item detail."""
    table = {}
    paths = gf.find(lambda p: re.search(r"/ITEM_[^/]*\.uasset$", p) is not None)
    for n, path in enumerate(sorted(paths)):
        asset = path.rsplit("/", 1)[-1][:-len(".uasset")]
        try:
            st = read_item_stats(gf.read(path), asset)
        except Exception:
            continue
        if st["maxStack"]:
            table[asset] = st["maxStack"]
        if log and n % 300 == 0:
            log(f"Stacks: {n}/{len(paths)}")
    return table
