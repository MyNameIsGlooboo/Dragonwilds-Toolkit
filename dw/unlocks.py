"""Unlock lists of a Dragonwilds character save: names, icons and safe edits.

Save ids
--------
Every gameplay asset stores its save id as a 22-char base64url FString,
followed by a lower-case debug name FString (e.g. "oY1_6kD3mPbzQ0KmJZI54g",
"recipe_dried_redberries_from_redberries"). The character save lists those ids:

    GameProgress.Progress.RecipesUnlocked / RecipesNew       RECIPE_*, DA_Recipe_*
    GameProgress.Progress.BuildingsUnlocked / BuildingPiecesNew
                                                             BUILDPIECE_*, DA_* (BaseBuilding.PieceType.*)
    GameProgress.Progress.SpellsUnlocked                     USD_* (utility spell data)
    GameProgress.Progress.SpellsNew                          PerkV2_* (perk ids, not spell ids)
    GameProgress.Journal.UnlockedEntries / UnreadEntries     JOURNAL_*
    GameProgress.QuestProgress.Quests[].QuestId              Quest_*
    GameProgress.Progress.PlayerHooksTriggered               PH_*
    GameProgress.Progress.KilledOnceAIs                      BP_AI_*_Data
    GameProgress.Character.Mount.MountsUnlockedList          "MountDataAsset:" + DA_Mount_* asset name
    ShrinesUnlocked / KebbitBurrowsFound                     32-hex actor GUIDs of placed level actors
                                                             (not resolvable from asset files)

All lists keep the game's append order (they are not sorted); a fresh unlock
is appended to the *Unlocked list and to the matching *New / Unread list.

Map fog
-------
GameProgress.RevealedFog.RevealedRegionsBitmap is an int32 bit set over the
31 BP_MapRegion actors placed in L_World. Each actor has a RegionId 1..31
(T_Map_Regions_painted encodes the same ids as red = id * 8; id 0 never
occurs). Bit (RegionId - 1) is set once the region is revealed, so "whole map
revealed" is 0x7FFFFFFF = 2147483647. See REGIONS.
"""
import os
import re
import struct
import time

from .gamedata import (ID_RX, WORDY_RX, _fstrings, _parse_string_table, _pick_icon,
                       _save_icon, _texture_to_image)
from .savefile import RawNumber

UNLOCKS_VERSION = 1
ALL_REGIONS_BITMAP = 0x7FFFFFFF
MOUNT_PREFIX = "MountDataAsset:"

# RegionId -> (string key of the region's name, English name, area). Read from the
# BP_MapRegion actors in L_World (RegionId property and RegionName FText).
REGIONS = {
    1: ("TempleWoods", "Temple Woods", "Brynmoor"),
    2: ("BramblemeadValley", "Bramblemead Valley", "Brynmoor"),
    3: ("WhisperingSwamp", "Whispering Swamp", "Brynmoor"),
    4: ("FracturedPlains", "Fractured Plains", "Ghornfell"),
    5: ("StormtouchedHighlands", "Stormtouched Highlands", "Ghornfell"),
    6: ("BloodblightSwamp", "Bloodblight Swamp", "Ghornfell"),
    7: ("Fellhollow.BleakfieldsValley", "Bleakfields Valley", "Fellhollow"),
    8: ("Fellhollow.HopesFall", "Hope's Fall", "Fellhollow"),
    9: ("Fellhollow.LakeOfLostSouls", "Lake of Lost Souls", "Fellhollow"),
    10: ("Fellhollow.CoalridgePass", "Coalridge Pass", "Fellhollow"),
    11: ("Fellhollow.SilverthornKeep", "Silverthorn Keep", "Fellhollow"),
    12: ("Fellhollow.DragonsRun", "Dragons' Run", "Fellhollow"),
    13: ("Fellhollow.Witchwillow", "Witchwillow Range", "Fellhollow"),
    14: ("Fellhollow.Emberwood", "Emberwood", "Fellhollow"),
    15: ("Fellhollow.DeathsDomain", "Forgotten Temple", "Fellhollow"),
    16: ("Approach", "The Approach", "Dowdun Reach"),
    17: ("Library", "The Library", "Dowdun Reach"),
    18: ("Courtyard", "The Courtyard", "Dowdun Reach"),
    19: ("Menagerie", "The Menagerie", "Dowdun Reach"),
    20: ("Garrison", "The Garrison", "Dowdun Reach"),
    21: ("Nexus", "The Nexus", "Dowdun Reach"),
    22: ("Pastures", "The Pastures", "Dowdun Reach"),
    23: ("Cathedral", "The Cathedral", "Dowdun Reach"),
    24: ("Bastion", "The Bastion", "Dowdun Reach"),
    25: ("GrandHall", "The Grand Hall", "Dowdun Reach"),
    26: ("Umbral.AlcarridOasis", "Alcarrid Oasis", "Umbral Sands"),
    27: ("Umbral.DunesOfUzzer", "Dunes of Uzzer", "Umbral Sands"),
    28: ("Umbral.ManafemPlanes", "Manafem Plains", "Umbral Sands"),
    29: ("Umbral.TheBurningSpire", "The Burning Spire", "Umbral Sands"),
    30: ("ScornedWilderness.RasthinsRest", "Rasthin's Rest", "Scorned Wilderness"),
    31: ("ScornedWilderness.ScornedWilderness", "Scorned Wilderness", "Scorned Wilderness"),
}
REGION_MASK_TEXTURE = "RSDragonwilds/Content/Art/UI/Map/T_Map_Regions_painted.uasset"

# Where each editable family lives in the save: (unlocked list path, new list path or None).
SAVE_LISTS = {
    "recipes": (("GameProgress", "Progress", "RecipesUnlocked"), ("GameProgress", "Progress", "RecipesNew")),
    "buildings": (("GameProgress", "Progress", "BuildingsUnlocked"), ("GameProgress", "Progress", "BuildingPiecesNew")),
    "spells": (("GameProgress", "Progress", "SpellsUnlocked"), None),
    "journal": (("GameProgress", "Journal", "UnlockedEntries"), ("GameProgress", "Journal", "UnreadEntries")),
}
# Read-only families (names for display only).
VIEW_LISTS = {
    "perks": ("GameProgress", "Progress", "SpellsNew"),
    "hooks": ("GameProgress", "Progress", "PlayerHooksTriggered"),
    "creatures": ("GameProgress", "Progress", "KilledOnceAIs"),
}
SAFETY = {
    "recipes": "medium", "buildings": "medium", "journal": "low",
    "spells": "high", "map": "low", "mounts": "medium",
}

_FAMILY_RX = {
    "recipes": re.compile(r"/(RECIPE_[^/]*|DA_Recipe_[^/]*)\.uasset$"),
    "buildings": re.compile(r"/(BUILDPIECE_[^/]*|DA_[^/]*)\.uasset$"),
    "spells": re.compile(r"/USD_[^/]*\.uasset$"),
    "perks": re.compile(r"/PerkV2_[^/]*\.uasset$"),
    "journal": re.compile(r"/JOURNAL_[^/]*\.uasset$"),
    "quests": re.compile(r"/Quests/(.*/)?Quest_[^/]*\.uasset$"),
    "hooks": re.compile(r"/PH_[^/]*\.uasset$"),
    "creatures": re.compile(r"/AI/.*/(BP|DA)_AI_[^/]*_Data\.uasset$"),
    "mounts": re.compile(r"/Mounts/.*/DA_Mount_[A-Za-z]+_\d+_\d+\.uasset$"),
}
_BUILD_DIRS = ("/BaseBuilding", "/Buildpieces/", "/BuildPieces/")
_HIDDEN = re.compile(r"/(Deprecated|Test|Tests|Debug|Developers|Cheats?|_Old|Old|WIP)/|_Test\b|_DEPRECATED|Deprecated_",
                     re.I)
_ICON_PREFER = {
    "spells": ("T_Skill_", "T_Icon_Spell", "/Icons/"),
    "perks": ("T_Skill_", "T_Icon_SkillTree_"),
    "mounts": ("T_Mount_",),
    "journal": ("T_Icon_Journal", "/Journal/", "/Icons/"),
    "buildings": ("T_Icon_",),
}
UNRELEASED_DLC = {"FutureMajorVersion"}
_PAID = re.compile(r"DeluxeEdition|Supporter|Founder", re.I)
# Icon extraction order (most useful first); later families may run out of time.
ICON_FAMILIES = ("spells", "mounts", "perks", "buildings", "recipes", "journal")
_GENERIC_DIRS = {"recipes", "recipe", "crafted", "processed", "v3", "v2", "data", "content", "gameplay",
                 "items", "equipment", "player", "character", "entries", "journaldata", "ui",
                 "buildingpieces", "basebuilding", "basebuilding_new", "decorations", "vendor"}


# --------------------------------------------------------------------------
# low-level asset reading

def _name_batches(buf):
    """Every zen name batch in a package: [own names], [imported package names]..."""
    out = []
    marker = struct.pack("<Q", 0xC1640000)
    start = 0
    while True:
        i = buf.find(marker, start)
        if i < 0:
            break
        start = i + 8
        if i < 8:
            continue
        count, nbytes = struct.unpack_from("<II", buf, i - 8)
        pos = i + 8 + count * 8
        if not 0 < count < 200000 or pos + count * 2 + nbytes > len(buf):
            continue
        heads = struct.unpack_from(">%dH" % count, buf, pos)
        pos += count * 2
        names = []
        for h in heads:
            n = h & 0x7FFF
            if h & 0x8000:
                names.append(buf[pos:pos + n * 2].decode("utf-16-le", "replace"))
                pos += n * 2
            else:
                names.append(buf[pos:pos + n].decode("latin-1"))
                pos += n
        out.append(names)
    return out


def _refs(batches):
    return [n for b in batches for n in b if n.startswith("/") and n.count("/") >= 2]


def persistence_id(buf):
    """The asset's save id. It is the 22-char FString right before the debug-name
    FString; a 22-char debug name (e.g. "recipe_t5_upgrade_core") must not win."""
    ms = list(ID_RX.finditer(buf))
    if not ms:
        return None
    if len(ms) >= 2 and ms[-2].end() == ms[-1].start():
        return ms[-2].group(1).decode()
    good = [m.group(1).decode() for m in ms if not WORDY_RX.fullmatch(m.group(1).decode())]
    return good[-1] if good else None


def _asset(path):
    return path.rsplit("/", 1)[-1].rsplit(".", 1)[0]


def _dlc(path):
    m = re.search(r"/Plugins/GameFeatures/([^/]+)/", path)
    return m.group(1) if m else "Base"


def _humanize(asset):
    s = re.sub(r"^(RECIPE_Journal_Materials_Basic|RECIPE_Journal|DA_Recipe_Vendor_UmS_Buy|DA_Recipe_Vendor"
               r"|RECIPE|BUILDPIECE|JOURNAL|USD|PerkV2|PH|Quest|DA_Recipe|DA_Mount|DA_BaseBuilding|DA|BP_AI|DA_AI)_",
               "", asset)
    s = re.sub(r"_Data$", "", s)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s.replace("_", " "))
    return re.sub(r"\s+", " ", s).strip()


def _category(path, family):
    parts = path.split("/")[:-1]
    if family == "buildings":
        for key in ("BuildingPieces", "Buildpieces", "Data", "BaseBuilding_New"):
            if key in parts:
                tail = [p for p in parts[parts.index(key) + 1:] if p.lower() not in ("recipes",)]
                if tail:
                    return " / ".join(tail[-2:]).replace("_", " ")
    if family == "journal" and "Entries" in parts:
        return " / ".join(parts[parts.index("Entries") + 1:][:2])
    if family == "recipes" and ("JournalEntries" in parts or path.rsplit("/", 1)[-1].startswith("RECIPE_Journal_")):
        return "Journal"
    if family == "recipes" and ("Vendor" in parts or "Vendors" in parts):
        i = parts.index("Vendors") if "Vendors" in parts else -1
        return "Vendor" + (" / " + parts[i + 1] if 0 <= i < len(parts) - 1 else "")
    for p in reversed(parts):
        if p.lower() not in _GENERIC_DIRS and not p.startswith(("RSDragonwilds", "GameFeatures", "Plugins")):
            return p.replace("_", " ")
    return "Other"


class _Tables:
    """String table cache: table name -> {key: text}."""

    def __init__(self, gf):
        self.gf = gf
        self.cache = {}
        self._index = None

    def get(self, ref):
        name = ref.rsplit("/", 1)[-1].split(".")[0]
        if name not in self.cache:
            if self._index is None:
                self._index = {}
                for p in self.gf.index:
                    if "/ST_" in p and p.endswith(".uasset"):
                        self._index.setdefault(_asset(p), p)
            p = self._index.get(name)
            t = {}
            if p:
                try:
                    t = _parse_string_table(self.gf.read(p))
                except Exception:
                    t = {}
            self.cache[name] = t
        return self.cache[name]


_DESC_KEY = re.compile(r"(Desc|Description|Tooltip|Lore|Body|Text\d*)$", re.I)
_HEX_KEY = re.compile(r"[0-9A-F]{32}")
_NAME_KEY = re.compile(r"(Name|Title)$", re.I)
LOCRES = "RSDragonwilds/Content/Localization/Game/en/Game.locres"
_LOCRES_MAGIC = bytes.fromhex("0e14747567 4a03fc4a15909dc3377f1b".replace(" ", ""))


def _rd_fstring(b, pos):
    (n,) = struct.unpack_from("<i", b, pos)
    pos += 4
    if n == 0:
        return "", pos
    if n < 0:
        return b[pos:pos - n * 2 - 2].decode("utf-16-le", "replace"), pos - n * 2
    return b[pos:pos + n - 1].decode("utf-8", "replace"), pos + n


def read_locres(gf, path=LOCRES):
    """{key: English text} from the game's .locres (LocRes v2/v3)."""
    try:
        b = gf.read(path)
    except Exception:
        return {}
    if not b.startswith(_LOCRES_MAGIC):
        if b.startswith(_LOCRES_MAGIC[4:]):  # pak reader skipped 4 bytes
            b = _LOCRES_MAGIC[:4] + b
        else:
            return {}
    try:
        ver = b[16]
        pos = 17
        (str_off,) = struct.unpack_from("<q", b, pos)
        pos += 8
        if ver >= 2:
            pos += 4  # entry count
        (nns,) = struct.unpack_from("<I", b, pos)
        pos += 4
        keys = []
        for _ in range(nns):
            if ver >= 2:
                pos += 4
            _ns, pos = _rd_fstring(b, pos)
            (nk,) = struct.unpack_from("<I", b, pos)
            pos += 4
            for _ in range(nk):
                if ver >= 2:
                    pos += 4
                key, pos = _rd_fstring(b, pos)
                _src_hash, idx = struct.unpack_from("<Ii", b, pos)
                pos += 8
                keys.append((key, idx))
        pos = str_off
        (ns,) = struct.unpack_from("<i", b, pos)
        pos += 4
        strings = []
        for _ in range(ns):
            s, pos = _rd_fstring(b, pos)
            if ver >= 2:
                pos += 4
            strings.append(s)
        return {k: strings[i] for k, i in keys if 0 <= i < len(strings)}
    except Exception:
        return {}


def _clean(v):
    return " ".join(v.split()) if v else None


def _display_name(buf, batches, tables, locres=None):
    """Name text of an asset: string-table FText first, then inline FText
    (32-hex key, localized via the .locres or its source string)."""
    refs = [r for r in _refs(batches) if "/ST_" in r and "." not in r.rsplit("/", 1)[-1]]
    refs = [r for r in refs if "Description" not in r]
    tabs = [tables.get(r) for r in refs]
    name_tabs = [tables.get(r) for r in refs if "Name" in r.rsplit("/", 1)[-1]]
    strs = [s for s in _fstrings(buf) if not _DESC_KEY.search(s)]
    # 1. keys like "X.Name", "X_DisplayName", "X.Title" (string tables or the .locres)
    for s in strs:
        if _NAME_KEY.search(s):
            for t in tabs + [locres or {}]:
                if t.get(s):
                    return _clean(t[s])
    # 2. keys found in a names table (ST_ItemNames, ...), then 3. in any table
    for group in (name_tabs, tabs):
        for s in strs:
            for t in group:
                if t.get(s):
                    return _clean(t[s])
    strs = _fstrings(buf)
    for k, s in enumerate(strs):
        if _HEX_KEY.fullmatch(s):
            v = (locres or {}).get(s)
            if not v and k + 1 < len(strs) and not _HEX_KEY.fullmatch(strs[k + 1]):
                v = strs[k + 1]
            if v and not re.fullmatch(r"[A-Za-z0-9_-]{22}", v):
                return _clean(v)
            return None  # first inline text is the name; do not fall through to the description
    return None


# --------------------------------------------------------------------------
# recipe output item (by name similarity of the recipe and its ITEM_ imports)

_STOP = {"recipe", "da", "vendor", "ums", "buy", "sell", "process", "journal", "item", "from", "x"}


def _tokens(s):
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s)
    out = set()
    for t in re.split(r"[_\W]+", s.lower()):
        if not t or t in _STOP:
            continue
        if t.endswith("ies") and len(t) > 4:
            t = t[:-3] + "y"
        elif t.endswith("s") and len(t) > 3 and not t.endswith("ss"):
            t = t[:-1]
        out.add(t)
    return out


def _recipe_output(asset, item_paths):
    """Best guess of the item a recipe makes, from the ITEM_ packages it imports."""
    base = re.sub(r"^(RECIPE_|DA_Recipe_)", "", asset)
    base = re.split(r"_From_", base, flags=re.I)[0]
    want = _tokens(base)
    best, score = None, 0.0
    for p in item_paths:
        name = p.rsplit("/", 1)[-1]
        have = _tokens(name)
        s = len(want & have) - 0.05 * len(have - want)
        if "Currency" in name:
            s -= 2
        if s > score:
            best, score = p, s
    if best is None:
        others = [p for p in item_paths if "Currency" not in p]
        if len(others) == 1:  # e.g. vendor "buy" recipes: price + the item
            best = others[0]
    return best


def _pkg_to_file(gf, game_path):
    if game_path.startswith("/Game/"):
        rel = "RSDragonwilds/Content/" + game_path[len("/Game/"):] + ".uasset"
    else:
        plugin, _, rest = game_path[1:].partition("/")
        rel = f"RSDragonwilds/Plugins/GameFeatures/{plugin}/Content/{rest}.uasset"
    return rel if rel in gf else None


# --------------------------------------------------------------------------
# building the maps

def _icon(gf, game_path, icon_dir, done):
    if not game_path:
        return None
    name = game_path.rsplit("/", 1)[-1]
    if name in done:
        return done[name]
    if os.path.isfile(os.path.join(icon_dir, name + ".png")):
        done[name] = name + ".png"
        return done[name]
    try:
        return _save_icon(gf, game_path, icon_dir, done)
    except Exception:
        done[name] = None
        return None


def _region_centres(gf):
    """{region id: (u, v)} centre of each region on the map image, as 0..1 fractions."""
    try:
        img = _texture_to_image(gf, REGION_MASK_TEXTURE)
    except Exception:
        img = None
    if img is None:
        return {}
    img = img.convert("RGBA")
    w, h = img.size
    step = 4
    small = img.resize((w // step, h // step), 0)  # nearest: keeps exact ids
    sw, sh = small.size
    acc = {}
    for i, (r, g, b, _a) in enumerate(small.getdata() if not hasattr(small, "get_flattened_data")
                                      else small.get_flattened_data()):
        if b == 255 and g == 0 and r % 8 == 0 and r:
            t = acc.setdefault(r // 8, [0, 0, 0])
            t[0] += i % sw
            t[1] += i // sw
            t[2] += 1
    return {k: (round(t[0] / t[2] / sw, 4), round(t[1] / t[2] / sh, 4)) for k, t in acc.items()}


def build_unlocks(gf, out_dir, log=print, time_budget=45.0, items=None):
    """Names/icons for every unlock family. Never raises; icons go to out_dir/icons/.

    Returns {"version", "families": {family: {id: entry}}, "regions": [...],
    "allRegionsBitmap", "errors": [...]}; entry = {id, name, icon, category, asset,
    path, dlc, hidden, paid} (+ "output" item asset for recipes). Mount ids are
    "MountDataAsset:<asset>" as in the save. items: the item library
    ({id: {asset, name, icon}}) to name recipes after the item they make.
    """
    t0 = time.time()
    result = {"version": UNLOCKS_VERSION, "families": {}, "regions": [],
              "allRegionsBitmap": ALL_REGIONS_BITMAP, "errors": []}
    try:
        icon_dir = os.path.join(out_dir, "icons")
        os.makedirs(icon_dir, exist_ok=True)
        tables = _Tables(gf)
        locres = read_locres(gf)
        done = {}
        by_family = {f: [] for f in _FAMILY_RX}
        for p in gf.index:
            if not p.endswith(".uasset"):
                continue
            for fam, rx in _FAMILY_RX.items():
                if rx.search(p):
                    if fam == "buildings" and (not any(d in p for d in _BUILD_DIRS) or "DerivedData" in p):
                        continue
                    by_family[fam].append(p)
                    break
        item_cache = {}

        def item_info(pkg):
            if pkg not in item_cache:
                info = (None, None)
                f = _pkg_to_file(gf, pkg)
                if f:
                    try:
                        b = gf.read(f)
                        bt = _name_batches(b)
                        info = (_display_name(b, bt, tables, locres), _pick_icon(_refs(bt)))
                    except Exception:
                        pass
                item_cache[pkg] = info
            return item_cache[pkg]

        lib_items = {v.get("asset"): v for v in (items or {}).values() if isinstance(v, dict)}
        icon_refs = {}  # (family, id) -> texture package path
        for fam in by_family:
            out = {}
            for path in sorted(by_family[fam]):
                try:
                    buf = gf.read(path)
                    asset = _asset(path)
                    batches = _name_batches(buf)
                    names0 = batches[0] if batches else []
                    if (fam == "buildings" and asset.startswith("DA_")
                            and not any(n.startswith("BaseBuilding.PieceType") for n in names0)):
                        continue
                    pid = MOUNT_PREFIX + asset if fam == "mounts" else persistence_id(buf)
                    if not pid:
                        continue
                    name = _display_name(buf, batches, tables, locres)
                    refs = _refs(batches)
                    icon_ref = _pick_icon(refs, prefer=_ICON_PREFER.get(fam, ()))
                    entry = {"id": pid, "asset": asset, "path": path, "dlc": _dlc(path),
                             "category": _category(path, fam),
                             "hidden": bool(_HIDDEN.search(path)), "icon": None}
                    if fam == "recipes":
                        item_refs = [r for r in refs if r.rsplit("/", 1)[-1].startswith("ITEM_")]
                        outp = _recipe_output(asset, item_refs)
                        if outp:
                            out_asset = outp.rsplit("/", 1)[-1]
                            entry["output"] = out_asset
                            lib_item = lib_items.get(out_asset)
                            if lib_item and lib_item.get("name"):
                                name = name or lib_item["name"]
                                entry["icon"] = lib_item.get("icon")
                            iname, iicon = item_info(outp)
                            name = name or iname
                            icon_ref = None if entry["icon"] else (iicon or icon_ref)
                    if fam == "mounts" and name:
                        kind = "Flying carpet" if "FlyingCarpet" in asset else "Terrorbird"
                        name = f"{kind}: {name}"
                    entry["name"] = name or _humanize(asset)
                    # Unreleased (FutureMajorVersion) or deprecated content stays hidden;
                    # Deluxe Edition rewards are paid extras the player may not own.
                    if entry["dlc"] in UNRELEASED_DLC or re.search(r"deprecated|\bunused\b", entry["name"], re.I):
                        entry["hidden"] = True
                    entry["paid"] = bool(_PAID.search(path))
                    out[pid] = entry
                    if icon_ref and fam in ICON_FAMILIES:
                        icon_refs[(fam, pid)] = icon_ref
                except Exception as e:  # one bad asset must not stop the build
                    if len(result["errors"]) < 50:
                        result["errors"].append(f"{path}: {e}")
            result["families"][fam] = out
            log(f"Unlocks: {len(out)} {fam}")

        centres = _region_centres(gf)  # cheap; before icons so the time budget never drops it
        skipped = 0
        for fam in ICON_FAMILIES:
            for (f, pid), ref in icon_refs.items():
                if f != fam:
                    continue
                if time.time() - t0 > time_budget:
                    skipped += 1
                    continue
                result["families"][fam][pid]["icon"] = _icon(gf, ref, icon_dir, done)
        if skipped:
            result["errors"].append(f"time budget reached: {skipped} icons skipped (run again to finish)")

        names = tables.get("/Game/UI/ST_UI_MapRegions")
        for rid, (key, name, area) in REGIONS.items():
            result["regions"].append({"id": rid, "bit": rid - 1, "key": key, "name": name, "area": area,
                                      "centre": centres.get(rid)})
        result["regionNamesTable"] = len(names)
    except Exception as e:
        result["errors"].append(f"build failed: {e}")
        log(f"Unlocks build failed: {e}")
    result["seconds"] = round(time.time() - t0, 1)
    log(f"Unlocks ready in {result['seconds']} s")
    return result


# --------------------------------------------------------------------------
# reading a save against the maps

def _get(data, path):
    d = data
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _list(data, path, create=True):
    d = data
    for k in path[:-1]:
        d = d.setdefault(k, {}) if create else d.get(k, {})
    lst = d.get(path[-1])
    if lst is None and create:
        lst = d[path[-1]] = []
    return lst


def _families(lib):
    """Accept the unlocks dict itself or an editor library holding it under "unlocks"."""
    if not lib:
        return {}
    if "families" in lib:
        return lib["families"]
    return (lib.get("unlocks") or {}).get("families", {})


def save_ids(data, family):
    if family in SAVE_LISTS:
        return list(_get(data, SAVE_LISTS[family][0]) or [])
    if family in VIEW_LISTS:
        return list(_get(data, VIEW_LISTS[family]) or [])
    if family == "quests":
        return [q.get("QuestId") for q in (_get(data, ("GameProgress", "QuestProgress", "Quests")) or [])]
    if family == "mounts":
        return list(_get(data, ("GameProgress", "Character", "Mount", "MountsUnlockedList")) or [])
    return []


def coverage(data, lib):
    """{family: (resolved, total)} for the ids in a save."""
    fams = _families(lib)
    out = {}
    for fam in list(SAVE_LISTS) + list(VIEW_LISTS) + ["quests", "mounts"]:
        ids = [i for i in save_ids(data, fam) if i]
        known = fams.get(fam, {})
        out[fam] = (sum(1 for i in ids if i in known), len(ids))
    return out


def status(data, lib, family):
    """Every known entry of a family with "unlocked" (and "new") flags, for the UI."""
    known = _families(lib).get(family, {})
    have = set(save_ids(data, family))
    new = set()
    if family in SAVE_LISTS and SAVE_LISTS[family][1]:
        new = set(_get(data, SAVE_LISTS[family][1]) or [])
    rows = []
    for pid, e in known.items():
        rows.append(dict(e, unlocked=pid in have, new=pid in new))
    for pid in have - set(known):
        if pid:
            rows.append({"id": pid, "name": pid, "asset": None, "icon": None, "category": "Unknown",
                         "unlocked": True, "new": pid in new, "hidden": False})
    return rows


def revealed_regions(data):
    """[(region id, name, revealed?)] from RevealedRegionsBitmap."""
    v = _get(data, ("GameProgress", "RevealedFog", "RevealedRegionsBitmap"))
    bits = int(str(v)) if v is not None else 0
    return [(rid, REGIONS[rid][1], bool(bits >> (rid - 1) & 1)) for rid in sorted(REGIONS)]


# --------------------------------------------------------------------------
# edits (on CharacterSave.data; the caller renders, diffs and writes)

def unlock_ids(data, family, ids, mark_new=True, fill_selected=True):
    """Append ids missing from a family's unlocked list (game order: append at the
    end). New ids also go to the *New / Unread list, like fresh in-game unlocks.
    For spells the first empty Spellcasting.SelectedSpells slots are filled too
    (the game keeps unlocked spells there in unlock order). Returns added ids."""
    if family == "mounts":
        return [a for a in ids if add_mount(data, a)]
    if family not in SAVE_LISTS:
        raise ValueError("family cannot be edited: " + str(family))
    upath, npath = SAVE_LISTS[family]
    lst = _list(data, upath)
    have = set(lst)
    added = []
    for i in ids:
        if isinstance(i, str) and i and i not in have:
            lst.append(i)
            have.add(i)
            added.append(i)
    if added and mark_new and npath:
        new = _list(data, npath)
        seen = set(new)
        new.extend(i for i in added if i not in seen)
    if added and family == "spells" and fill_selected:
        sel = _get(data, ("GameProgress", "Spellcasting", "SelectedSpells"))
        if isinstance(sel, list):
            present = set(sel)
            for i in added:
                if i in present:
                    continue
                try:
                    k = sel.index("")
                except ValueError:
                    break
                sel[k] = i
    return added


def unlock_all(data, family, lib, include_hidden=False, dlc=None, mark_new=True, include_paid=False):
    """Unlock every known entry of a family. Left out unless asked: hidden
    entries (deprecated, test or unreleased assets) and paid Deluxe Edition
    extras. dlc: optional set of DLC names to limit to ("Base", "UmbralSands",
    ...). Returns the added ids."""
    known = _families(lib).get(family, {})
    ids = [pid for pid, e in sorted(known.items(), key=lambda kv: kv[1].get("path", ""))
           if (include_hidden or not e.get("hidden")) and (include_paid or not e.get("paid"))
           and (dlc is None or e.get("dlc") in dlc)]
    if family == "mounts":
        return [a for a in ids if add_mount(data, a)]
    return unlock_ids(data, family, ids, mark_new=mark_new)


def reveal_map(data, value=ALL_REGIONS_BITMAP):
    """Set RevealedRegionsBitmap (an int, or a list of region ids). Returns the old value."""
    if isinstance(value, (list, tuple, set)):
        bits = 0
        for rid in value:
            if int(rid) in REGIONS:
                bits |= 1 << (int(rid) - 1)
        value = bits
    value = int(value)
    if not -2 ** 31 <= value < 2 ** 32:
        raise ValueError("bitmap out of range")
    fog = data.setdefault("GameProgress", {}).setdefault("RevealedFog", {})
    old = fog.get("RevealedRegionsBitmap")
    fog["RevealedRegionsBitmap"] = RawNumber(str(value))
    return old


def add_mount(data, asset):
    """Add a mount ("DA_Mount_Terrorbird_01_03" or "MountDataAsset:DA_...") to
    MountsUnlockedList. Returns True if it was added."""
    if not asset:
        return False
    mid = asset if asset.startswith(MOUNT_PREFIX) else MOUNT_PREFIX + asset
    mount = data.setdefault("GameProgress", {}).setdefault("Character", {}).setdefault("Mount", {})
    lst = mount.setdefault("MountsUnlockedList", [])
    if mid in lst:
        return False
    lst.append(mid)
    return True


def apply_unlock(data, edit, lib):
    """One editor change: {"family": f, "ids": [...]} or {"family": f, "all": True,
    "dlc": [...], "includeHidden": False, "includePaid": False}; {"family": "map", "value": int | [ids]};
    {"family": "mounts", "asset": name}. Returns the added ids (or old bitmap)."""
    fam = edit.get("family")
    if fam == "map":
        return reveal_map(data, edit.get("value", ALL_REGIONS_BITMAP))
    if fam == "mounts" and edit.get("asset"):
        return [edit["asset"]] if add_mount(data, edit["asset"]) else []
    if edit.get("all"):
        dlc = set(edit["dlc"]) if edit.get("dlc") else None
        return unlock_all(data, fam, lib, include_hidden=bool(edit.get("includeHidden")), dlc=dlc,
                          mark_new=edit.get("markNew", True), include_paid=bool(edit.get("includePaid")))
    return unlock_ids(data, fam, edit.get("ids") or [], mark_new=edit.get("markNew", True))
