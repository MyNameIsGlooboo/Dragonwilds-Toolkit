"""Editor logic: views of a save, edits, limits, gear check, bundles, backups."""
import datetime
import glob
import json
import os
import shutil
import sys

import psutil

from .savefile import CharacterSave, RawNumber, SaveError, new_guid

TESTED_SAVE_VERSION = 83
APP_DIR = os.environ.get("DW_APP_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "DragonwildsSaveEditor")
CACHE_DIR = os.path.join(APP_DIR, "library")
BACKUP_DIR = os.path.join(APP_DIR, "backups")
SETTINGS_FILE = os.path.join(APP_DIR, "settings.json")
SAVE_DIR = os.environ.get("DW_SAVE_DIR") or os.path.join(
    os.environ.get("LOCALAPPDATA", ""), "RSDragonwilds", "Saved", "SaveCharacters")
WORLD_DIR = os.path.join(os.path.dirname(SAVE_DIR), "SaveGames")
MAX_AUTO_BACKUPS = 20
# Files shipped inside the exe (UE4SS, item stats) so nothing has to be downloaded
VENDOR_DIR = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "vendor")


def set_save_root(root):
    """Point the editor at another Saved folder (holds SaveCharacters / SaveGames)."""
    global SAVE_DIR, WORLD_DIR
    SAVE_DIR = os.path.join(root, "SaveCharacters")
    WORLD_DIR = os.path.join(root, "SaveGames")


def save_root():
    return os.path.dirname(SAVE_DIR)

# Equipped slots in GameProgress.Loadout (slots 5+ are hotbar shortcuts).
EQUIP_SLOTS = {0: "Head", 1: "Body", 2: "Legs", 3: "Cape", 4: "Trinket"}
VITALS = [
    ("Health", ["Character", "Health", "CurrentValue"], None),
    ("Stamina", ["Character", "Stamina", "CurrentValue"], 100),
    ("Food", ["Character", "Sustenance", "SustenanceValue"], 100),
    ("Water", ["Character", "Hydration", "HydrationValue"], 100),
    ("Rest", ["Character", "Endurance", "EnduranceValue"], 100),
]
DEFAULT_MAX_STACK = 9999


def game_processes():
    """Names of running game processes (the game can linger a while after its window closes)."""
    if os.environ.get("DW_IGNORE_GAME") == "1":  # developer testing on a copied save
        return []
    names = []
    for p in psutil.process_iter(["name"]):
        try:
            name = p.info["name"] or ""
            if name.lower().startswith("rsdragonwilds"):
                names.append(name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    return names


def game_running():
    return bool(game_processes())


def load_settings():
    try:
        with open(SETTINGS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_settings(s):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
        json.dump(s, f, indent=1)


def list_characters():
    out = []
    for p in sorted(glob.glob(os.path.join(SAVE_DIR, "*.json"))):
        st = os.stat(p)
        out.append({"file": os.path.basename(p), "name": os.path.basename(p)[:-5],
                    "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    return out


def level_for_xp(curve, xp):
    if not curve:
        return None
    lvl = 1
    for i, need in enumerate(curve):
        if xp >= need:
            lvl = i + 1
    return lvl


def _num(v, default=0):
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return default


def _get(d, path):
    for k in path:
        if not isinstance(d, dict) or k not in d:
            return None
        d = d[k]
    return d


def _set(d, path, value):
    for k in path[:-1]:
        d = d.setdefault(k, {})
    d[path[-1]] = value


class Editor:
    def __init__(self, library):
        self.lib = library
        self.items = library["items"]
        self.skills = library["skills"]
        self.curve = library.get("xpCurve")

    # ---------------------------------------------------------------- views
    def item_info(self, item_id):
        it = self.items.get(item_id)
        if not it:
            return {"id": item_id, "name": "Unknown item", "icon": None, "unknown": True,
                    "category": "Unknown", "slot": "Other", "description": "This item is not in the library."}
        return it

    def max_stack(self, it):
        m = it.get("maxStack")
        if m is None:
            return DEFAULT_MAX_STACK  # not in the stats database
        return max(1, m)  # 0 means "does not stack"

    def _entry_view(self, slot, e):
        if "PlayerInventoryItemIndex" in e:
            return {"slot": slot, "ref": int(e["PlayerInventoryItemIndex"])}
        it = self.item_info(e.get("ItemData"))
        v = {"slot": slot, "itemId": e.get("ItemData"), "name": it["name"], "icon": it.get("icon"),
             "category": it.get("category"), "description": it.get("description", ""),
             "powerLevel": it.get("powerLevel"), "skill": it.get("skill"),
             "unknown": bool(it.get("unknown"))}
        if "Count" in e or "Durability" not in e:
            v["count"] = _num(e.get("Count", 1), 1)
            v["maxStack"] = self.max_stack(it)
        if "Durability" in e:
            v["durability"] = _num(e["Durability"])
            v["maxDurability"] = it.get("maxDurability")
        return v

    def view(self, save):
        p = save.progress
        inv = save.container("Inventory")
        loadout = save.container("Loadout")
        skills = []
        for s in _get(p, ["Skills", "Skills"]) or []:
            info = self.skills.get(s["Id"], {"name": "Unknown skill", "icon": None})
            xp = _num(s.get("Xp"))
            skills.append({"id": s["Id"], "name": info["name"], "icon": info.get("icon"), "xp": xp,
                           "level": level_for_xp(self.curve, xp)})
        vitals = []
        for label, path, mx in VITALS:
            v = _get(p, path)
            if v is not None:
                vitals.append({"key": label, "value": round(_num(v), 1), "max": mx})
        prog = p.get("Progress", {})
        version = _num(save.data.get("Version"))
        return {
            "character": save.name,
            "charGuid": save.data.get("meta_data", {}).get("char_guid"),
            "file": os.path.basename(save.path),
            "version": version,
            "versionWarning": version != TESTED_SAVE_VERSION,
            "editable": save.roundtrip_ok,
            "playtimeHours": round(_num(_get(p, ["Character", "Playtime_wall"])) / 3600, 1),
            "inventory": [self._entry_view(s, inv[s]) for s in sorted(inv)],
            "inventoryMax": _num(p.get("Inventory", {}).get("MaxSlotIndex", -1)),
            "equipment": [dict(self._entry_view(s, loadout[s]), equipSlot=EQUIP_SLOTS.get(s))
                          for s in sorted(loadout) if s in EQUIP_SLOTS],
            "hotbar": [self._entry_view(s, loadout[s]) for s in sorted(loadout) if s not in EQUIP_SLOTS],
            "skills": skills,
            "vitals": vitals,
            "mounts": [m.split(":")[-1] for m in _get(p, ["Character", "Mount", "MountsUnlockedList"]) or []],
            "progress": {k: len(v) for k, v in prog.items() if isinstance(v, list)},
            "quests": len(_get(p, ["QuestProgress", "Quests"]) or []),
        }

    # ---------------------------------------------------------- gear check
    def _best_power(self, save, slot_type, skill):
        best = 0
        for key in ("Inventory", "Loadout"):
            for e in save.container(key).values():
                it = self.items.get(e.get("ItemData"))
                if not it or not it.get("powerLevel"):
                    continue
                if it.get("slot") == slot_type and (slot_type != "Held" or it.get("skill") == skill):
                    best = max(best, it["powerLevel"])
        return best

    def gear_check(self, save, item_id, container=None, slot=None):
        """Warnings (list of str) and blocking errors for putting this item somewhere."""
        it = self.items.get(item_id)
        warnings, errors = [], []
        if not it:
            errors.append("Unknown item.")
            return warnings, errors
        if container == "Loadout" and slot in EQUIP_SLOTS and it.get("slot") != EQUIP_SLOTS[slot]:
            errors.append(f"{it['name']} cannot be worn in the {EQUIP_SLOTS[slot]} slot.")
        pl = it.get("powerLevel") or 0
        if pl and it.get("slot") in ("Head", "Body", "Legs", "Cape", "Trinket", "Held"):
            best = self._best_power(save, it["slot"], it.get("skill"))
            if best and pl - best >= 2:
                what = f"{it['skill']} weapon" if it.get("slot") == "Held" and it.get("skill") else it["slot"].lower() + " gear"
                warnings.append(f"{it['name']} is Power Level {pl}; your best {what} is Power Level {best}. "
                                f"That is a big jump ahead of your progress.")

        if "Quest" in (it.get("category") or "") or it.get("slot") == "Quest":
            warnings.append(f"{it['name']} is a quest item. Adding or removing it can break quest progress.")
        return warnings, errors

    # --------------------------------------------------------------- edits
    def apply(self, save, edits):
        """Apply edits in memory. Returns (warnings, errors)."""
        warnings, errors = [], []
        for e in edits:
            try:
                w, er = self._apply_one(save, e)
                warnings += w
                errors += er
            except (KeyError, ValueError, TypeError) as ex:
                errors.append(f"Could not apply change: {ex}")
        return list(dict.fromkeys(warnings)), list(dict.fromkeys(errors))

    def _apply_one(self, save, e):
        op = e["op"]
        w, er = [], []
        if op in ("count", "durability"):
            c = save.container(e["container"])
            entry = c[int(e["slot"])]
            it = self.item_info(entry.get("ItemData"))
            value = int(e["value"])
            if op == "count":
                mx = self.max_stack(it)
                if not 1 <= value <= mx:
                    er.append(f"{it['name']}: amount must be 1 to {mx}.")
                    return w, er
                entry["Count"] = value
            else:
                mx = it.get("maxDurability")
                if value < 1 or (mx and value > mx):
                    er.append(f"{it['name']}: durability must be 1 to {mx}.")
                    return w, er
                if not mx:
                    w.append(f"{it['name']}: max durability is unknown, check it in game.")
                entry["Durability"] = value
        elif op in ("add", "replace"):
            container, slot = e["container"], int(e["slot"])
            item_id = e["itemId"]
            it = self.items.get(item_id)
            gw, ge = self.gear_check(save, item_id, container, slot)
            w += gw
            er += ge
            if ge:
                return w, er
            entry = {"GUID": new_guid(), "ItemData": item_id}
            if it.get("maxDurability"):
                entry["Durability"] = int(e.get("durability") or it["maxDurability"])
                if it.get("powerLevel") is not None and it.get("slot") in ("Held", "Head", "Body", "Legs", "Trinket"):
                    entry["NumUpgradesApplied"] = 0
            else:
                n = int(e.get("count") or 1)
                mx = self.max_stack(it)
                if not 1 <= n <= mx:
                    er.append(f"{it['name']}: amount must be 1 to {mx}.")
                    return w, er
                if n > 1 or mx > 1:
                    entry["Count"] = n
            if op == "add" and slot in save.container(container):
                er.append(f"Slot {slot} is not empty.")
                return w, er
            save.set_entry(container, slot, entry)
            w.append(f"New item {it['name']} added: adding items is new, check it in game.")
        elif op == "remove":
            container, slot = e["container"], int(e["slot"])
            entry = save.container(container).get(slot)
            if entry and entry.get("ItemData"):
                gw, _ = self.gear_check(save, entry["ItemData"])
                w += [x for x in gw if "quest item" in x]
            save.set_entry(container, slot, None)
            if container == "Inventory":
                for hb, ref in save.hotbar_refs().items():
                    if ref == slot:
                        save.set_entry("Loadout", hb, None)
        elif op == "xp":
            mx = self.curve[-1] if self.curve else 1000000
            value = int(e["value"])
            if not 0 <= value <= mx:
                er.append(f"XP must be 0 to {mx}.")
                return w, er
            for s in save.progress["Skills"]["Skills"]:
                if s["Id"] == e["skillId"]:
                    s["Xp"] = value
        elif op == "vital":
            for label, path, mx in VITALS:
                if label == e["key"]:
                    value = float(e["value"])
                    if value < 0 or (mx and value > mx):
                        er.append(f"{label} must be 0 to {mx}.")
                        return w, er
                    _set(save.progress, path, int(value) if value.is_integer() else value)
        elif op == "teleport":
            x, y, z = float(e["x"]), float(e["y"]), float(e["z"])
            loc = save.progress.setdefault("Character", {}).setdefault("LastAccessibleLocation", {})
            loc["Position"] = f"V(X={x:.2f}, Y={y:.2f}, Z={z:.2f})"
            w.append("Teleport is new: if you load in mid-air or underground, restore the backup.")
        elif op == "pins":
            pins = []
            for p in e["pins"][:50]:
                pins.append({"PinType": int(p.get("type", 0)),
                             "PinLocation": [float(p["x"]), float(p["y"])]})
            save.progress.setdefault("MapCustomization", {})["Pins"] = pins
        elif op == "put":
            # Move a whole item entry in (keeps durability, upgrades and other fields).
            container, slot = e["container"], int(e["slot"])
            if slot in save.container(container):
                er.append(f"Slot {slot} is not empty.")
                return w, er
            from .features import normalize_entry
            entry = normalize_entry(e["entry"])
            entry.setdefault("GUID", new_guid())
            save.set_entry(container, slot, entry)
        elif op == "unlock":
            from . import unlocks
            unlocks.apply_unlock(save.data, e, self.lib)
            w.append("Unlocks are new: check them in game.")
        else:
            er.append("Unknown change type: " + op)
        return w, er

    # ------------------------------------------------------------- bundles
    BUNDLES = [
        {"id": "repair", "name": "Full repair", "desc": "Every tool, weapon and armour piece back to full durability."},
        {"id": "runes", "name": "Rune pouch top-up", "desc": "Every rune stack you carry set to the amount you choose.", "amount": 1000},
        {"id": "ammo", "name": "Quiver refill", "desc": "Every arrow and bolt stack set to the amount you choose.", "amount": 500},
        {"id": "survival", "name": "Fed and rested", "desc": "Food, water, rest and stamina meters filled."},
        {"id": "materials", "name": "Builder's crate", "desc": "Adds stone, stone blocks, oak logs, oak planks, clay and iron bars to empty backpack slots. Adds new items, so check them in game.", "amount": 99, "experimental": True},
    ]

    def bundle_edits(self, save, bundle_id, amount=None):
        edits = []
        if bundle_id == "repair":
            for key in ("Inventory", "Loadout"):
                for slot, e in save.container(key).items():
                    it = self.items.get(e.get("ItemData"))
                    if it and "Durability" in e and it.get("maxDurability") and _num(e["Durability"]) < it["maxDurability"]:
                        edits.append({"op": "durability", "container": key, "slot": slot, "value": it["maxDurability"]})
        elif bundle_id in ("runes", "ammo"):
            for slot, e in save.container("Inventory").items():
                it = self.items.get(e.get("ItemData"))
                if not it or "Count" not in e:
                    continue
                is_rune = it["asset"].startswith("ITEM_Rune_") and "Essence" not in it["asset"]
                is_ammo = it.get("slot") == "Ammo" and ("Arrow" in it["asset"] or "Bolt" in it["asset"])
                if (bundle_id == "runes" and is_rune) or (bundle_id == "ammo" and is_ammo):
                    edits.append({"op": "count", "container": "Inventory", "slot": slot,
                                  "value": min(int(amount or 1000), self.max_stack(it))})
        elif bundle_id == "survival":
            for label, path, mx in VITALS:
                if mx:
                    edits.append({"op": "vital", "key": label, "value": mx})
        elif bundle_id == "materials":
            wanted = ["ITEM_Resources_Stone", "ITEM_Resources_Stone_Block", "ITEM_Resources_Wood_Oak",
                      "ITEM_Resources_Plank_Oak", "ITEM_Resources_Clay", "ITEM_Resources_IronBar"]
            by_asset = {v["asset"].lower(): v for v in self.items.values()}
            inv = save.container("Inventory")
            free = [s for s in range(0, 32) if s not in inv]
            for asset in wanted:
                it = by_asset.get(asset.lower())
                if not it or not free:
                    continue
                edits.append({"op": "add", "container": "Inventory", "slot": free.pop(0), "itemId": it["id"],
                              "count": min(int(amount or 99), self.max_stack(it))})
        return edits


# ------------------------------------------------------------------ backups
def backup_key(save_path):
    """Backup folder name: character name, or 'World - <name>' for world saves."""
    base = os.path.basename(save_path)
    if base.endswith(".sav"):
        return "World - " + base[:-4]
    return base[:-5]


def make_backup(save_path, reason="auto"):
    name = backup_key(save_path)
    stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    dest = os.path.join(BACKUP_DIR, name, f"{stamp}_{reason}")
    os.makedirs(dest, exist_ok=True)
    for p in (save_path, save_path + ".backup"):
        if os.path.isfile(p):
            shutil.copy2(p, dest)
    _prune(os.path.join(BACKUP_DIR, name))
    return dest


def _prune(folder):
    autos = sorted(d for d in os.listdir(folder) if d.endswith("_auto"))
    for d in autos[:-MAX_AUTO_BACKUPS]:
        shutil.rmtree(os.path.join(folder, d), ignore_errors=True)


def list_backups(char_name):
    folder = os.path.join(BACKUP_DIR, char_name)
    if not os.path.isdir(folder):
        return []
    out = []
    for d in sorted(os.listdir(folder), reverse=True):
        stamp, _, reason = d.rpartition("_")
        day, _, clock = stamp.partition("_")
        files = os.listdir(os.path.join(folder, d))
        out.append({"id": d, "when": f"{day} {clock.replace('-', ':')}", "reason": reason, "files": files})
    return out


def restore_backup(key, backup_id):
    """key: character name or 'World - <name>' (see backup_key)."""
    src = os.path.join(BACKUP_DIR, key, backup_id)
    if not os.path.isdir(src) or os.path.dirname(os.path.abspath(src)) != os.path.abspath(os.path.join(BACKUP_DIR, key)):
        raise SaveError("Backup not found.")
    if key.startswith("World - "):
        dest_dir = WORLD_DIR
        target = os.path.join(WORLD_DIR, key[len("World - "):] + ".sav")
    else:
        dest_dir = SAVE_DIR
        target = os.path.join(SAVE_DIR, key + ".json")
    if os.path.isfile(target):
        make_backup(target, "before-restore")
    for f in os.listdir(src):
        shutil.copy2(os.path.join(src, f), os.path.join(dest_dir, f))
