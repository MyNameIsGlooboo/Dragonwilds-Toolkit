"""World editor logic: map view, chest edits with limits, world settings.

Builds on dw.worldsave (file format) and dw.mapdata (map art + transform).
"""
import difflib
import os

from . import worldsave as ws
from .core import DEFAULT_MAX_STACK
from .mapdata import world_to_map

# Container kinds from worldsave.list_containers -> marker kind / label
KINDS = {
    "storage": ("storage", "Storage"),
    "crafting station": ("station", "Crafting stations"),
    "loot chest": ("loot", "Loot chests"),
    "gravestone": ("grave", "Gravestones"),
}
SETTING_LABELS = {
    "name": "World name", "friendlyFire": "Friendly fire", "privacy": "Privacy", "crossplay": "Crossplay",
}
PRIVACY = ["Private", "Friends only", "Public"]


LAYER_LABELS = {
    "building": "Buildings", "dropped": "Dropped items", "lore": "Lore items", "teleport": "Teleporters",
    "resource": "Resource nodes", "trap": "Traps", "door": "Doors & switches", "npc": "Creatures & NPCs",
    "player": "Your character", "pin": "Map pins", "base": "Your base",
}


def layer_kind(actor):
    """Map layer for a view-only actor, or None to hide it (containers are drawn separately)."""
    k, cls = actor.get("kindGuess"), actor.get("class", "")
    if k in ("storage", "crafting station", "loot chest", "manager"):
        return None
    if k == "gravestone":
        return None if "Graveyard" in cls else "building"
    if k == "dropped item":
        return "dropped"
    if k == "building":
        return "building"
    if k == "resource node":
        return "resource"
    if k == "trap":
        return "trap"
    if k == "door / mechanism":
        return "teleport" if "Teleport" in cls else "door"
    if k == "creature":
        return "npc"
    if "LoreItem" in cls:
        return "lore"
    if "Teleport" in cls:
        return "teleport"
    return None


def nice_name(cls):
    """BP_BaseBuilding_Chest_Small_C -> 'Small chest', BP_LootChest_Prefab_Tier6_World_C -> 'Loot chest (tier 6)'."""
    import re
    s = re.sub(r"^BP_", "", cls)
    s = re.sub(r"_C$", "", s)
    m = re.search(r"Tier(\d+\+?)", s)
    if "LootChest" in s or "BuriedChest" in s:
        base = "Buried chest" if "Buried" in s else "Loot chest"
        return base + (f" (tier {m.group(1)})" if m else "")
    special = {"BaseBuilding_Chest": "Chest", "BaseBuilding_Chest_Small": "Small chest",
               "BaseBuilding_Crate": "Crate"}
    if s in special:
        return special[s]
    s = re.sub(r"^(BaseBuilding|Station|DR|Prefab)_", "", s)
    s = re.sub(r"([a-z])([A-Z])", r"\1 \2", s).replace("_", " ").strip()
    if cls.startswith("BP_Station_"):
        s += " (station)"
    return s[:1].upper() + s[1:]


def _capacity_of(cls):
    """Slot count for a chest class (dw.capacity), or None when not known."""
    try:
        from .capacity import slots_for_class
        return slots_for_class(cls)[0]
    except Exception:
        return None


def list_worlds(world_dir):
    import datetime
    out = []
    if not os.path.isdir(world_dir):
        return out
    for f in sorted(os.listdir(world_dir)):
        if f.lower().endswith(".sav"):
            st = os.stat(os.path.join(world_dir, f))
            out.append({"file": f, "name": f[:-4],
                        "modified": datetime.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")})
    return out


def _parse_vec(text):
    """'V(X=1, Y=2, Z=3)' -> (1.0, 2.0)"""
    try:
        parts = dict(p.split("=") for p in text.strip("V()").replace(" ", "").split(","))
        return float(parts["X"]), float(parts["Y"])
    except (ValueError, KeyError, AttributeError):
        return None


class WorldEditor:
    def __init__(self, library):
        self.items = library["items"]
        self.map = next((m for m in library.get("maps") or [] if m.get("id") == "world"), None)

    def max_stack(self, it):
        m = it.get("maxStack") if it else None
        return DEFAULT_MAX_STACK if m is None else max(1, m)

    def _px(self, x, y):
        if not self.map:
            return None, None
        px, py = world_to_map(self.map["transform"], x, y)
        return round(px, 1), round(py, 1)

    # ---------------------------------------------------------------- view
    def view(self, save, character=None, with_layers=True):
        info = ws.world_info(save)
        settings = []
        for key in ("name", "friendlyFire", "privacy", "crossplay"):
            f = {"key": key, "label": SETTING_LABELS[key], "editable": key in ws.EDITABLE_SETTINGS}
            if key == "name":
                f.update(type="text", value=info.get("name"))
            elif key == "privacy":
                v = info.get("privacy")
                f.update(type="choice", options=PRIVACY, value=PRIVACY[v] if isinstance(v, int) and 0 <= v < 3 else str(v))
            else:
                f.update(type="bool", value=bool(info.get(key)))
            if key == "friendlyFire" and info.get("customDifficulty", {}).get("Difficulty.Environment.FriendlyFire") is not None:
                f["editable"] = False
            settings.append(f)
        for key, label in (("owner", "Owner"), ("timeOfSaveText", "Last saved"), ("saveFileRevision", "Save count"),
                           ("passwordSet", "Password set")):
            if info.get(key) is not None:
                settings.append({"key": key, "label": label, "type": "text", "value": str(info[key]), "editable": False})

        containers, markers = {}, []
        for c in ws.list_containers(save, self.items):
            kind, _ = KINDS.get(c["kind"], ("other", "Other"))
            pos = c.get("position") or [None, None, None]
            items = []
            for it in c["items"]:
                info_it = self.items.get(it["itemId"], {})
                v = {"slot": it["slot"], "itemId": it["itemId"], "name": it.get("name") or info_it.get("name") or "Unknown item",
                     "icon": info_it.get("icon")}
                if "durability" in it:
                    v["durability"] = it["durability"]
                    v["maxDurability"] = info_it.get("maxDurability")
                else:
                    v["count"] = it.get("count", 1)
                    v["maxStack"] = self.max_stack(info_it or None)
                items.append(v)
            used = {it["slot"] for it in items}
            free = next((s for s in range(0, (c.get("maxSlotIndex") or -1) + 1) if s not in used),
                        (c.get("maxSlotIndex") or -1) + 1)
            nice = nice_name(c["class"])
            containers[c["id"]] = {
                "id": c["id"], "name": nice, "kind": kind, "items": items, "editable": c.get("editable", True),
                "readOnlyReason": c.get("readonlyReason"), "canAdd": kind in ("storage", "station"),
                "freeSlot": free, "copies": len(c.get("copies") or []),
                "where": f"X {round(pos[0])}  Y {round(pos[1])}" if pos[0] is not None else "",
                "x": pos[0], "y": pos[1], "maxSlotIndex": c.get("maxSlotIndex"),
                "class": c["class"], "capacity": _capacity_of(c["class"]), "actor": c.get("actor"),
            }
            if pos[0] is not None:
                px, py = self._px(pos[0], pos[1])
                markers.append({"id": c["id"], "kind": kind, "name": nice, "x": pos[0], "y": pos[1],
                                "px": px, "py": py, "itemCount": len(items)})

        if character is not None:
            prog = character.data.get("GameProgress", {})
            loc = _parse_vec(str(prog.get("Character", {}).get("LastAccessibleLocation", {}).get("Position", "")))
            if loc:
                px, py = self._px(*loc)
                markers.append({"id": None, "kind": "player", "name": character.name + " (last position)",
                                "x": loc[0], "y": loc[1], "px": px, "py": py})
            for pin in prog.get("MapCustomization", {}).get("Pins", []) or []:
                try:
                    x, y = float(pin["PinLocation"][0]), float(pin["PinLocation"][1])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
                px, py = self._px(x, y)
                markers.append({"id": None, "kind": "pin", "name": "Map pin", "x": x, "y": y, "px": px, "py": py})

        layers = self._layers(save) if with_layers else None
        base = self._base_center(containers, markers)
        if base:
            px, py = self._px(*base)
            markers.append({"id": None, "kind": "base", "name": "Your base (centre of your storage)",
                            "x": base[0], "y": base[1], "px": px, "py": py})

        m = None
        if self.map:
            t = self.map["transform"]
            m = {"image": self.map["image"], "width": self.map["width"], "height": self.map["height"],
                 # pixel -> world, for the coordinate readout
                 "inverse": {"a": 1 / t["scaleX"], "b": 0, "c": t["originX"],
                             "d": 0, "e": 1 / t["scaleY"], "f": t["originY"]}}
        return {
            "file": os.path.basename(save.path), "name": info.get("name"), "editable": save.roundtrip_ok,
            "readOnlyReason": None if save.roundtrip_ok else "the file did not re-write exactly",
            "settings": settings, "containers": containers, "markers": markers, "map": m,
            "kindLabels": dict({v[0]: v[1] for v in KINDS.values()}, **{k: v for k, v in LAYER_LABELS.items()}),
            "layers": layers,
            "stats": {"objects": layers["total"] if layers else None, "containers": len(containers),
                      "revision": info.get("saveFileRevision")},
        }

    def _layers(self, save):
        """View-only objects for the map, packed small: rows of [kind, px, py, label index]."""
        labels, label_ix, rows = [], {}, []
        counts = {}
        for a in ws.list_actors(save):
            pos = a.get("position")
            if not pos or all(abs(v) < 1 for v in pos[:2]):
                continue  # managers and other objects without a place
            kind = layer_kind(a)
            if not kind:
                continue
            if kind == "dropped" and a.get("item"):
                it = a["item"]
                name = (self.items.get(it.get("itemId")) or {}).get("name") or "Item"
                label = f"{name} ×{it.get('count', 1)}" if it.get("count", 1) > 1 else name
            else:
                label = nice_name(a["class"])
            if label not in label_ix:
                label_ix[label] = len(labels)
                labels.append(label)
            px, py = self._px(pos[0], pos[1])
            if px is None:
                continue
            rows.append([kind, px, py, label_ix[label], a["id"] if kind == "dropped" else None])
            counts[kind] = counts.get(kind, 0) + 1
        return {"rows": rows, "labels": labels, "counts": counts, "total": len(rows)}

    @staticmethod
    def _base_center(containers, markers):
        """Centre of the player's own storage and stations (densest cluster)."""
        pts = [(m["x"], m["y"]) for m in markers
               if m.get("id") and containers[m["id"]]["kind"] in ("storage", "station")]
        if not pts:
            return None
        # pick the point with most neighbours within 3000 units, then average that cluster
        best = max(pts, key=lambda p: sum(1 for q in pts if (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 < 9e6))
        near = [q for q in pts if (best[0] - q[0]) ** 2 + (best[1] - q[1]) ** 2 < 9e6]
        return sum(q[0] for q in near) / len(near), sum(q[1] for q in near) / len(near)

    # --------------------------------------------------------------- edits
    def apply(self, save, edits):
        warnings, errors = [], []
        for e in edits:
            try:
                self._apply_one(save, e, warnings, errors)
            except ws.WorldSaveError as ex:
                errors.append(str(ex))
            except (KeyError, ValueError, TypeError) as ex:
                errors.append(f"Could not apply change: {ex}")
        return list(dict.fromkeys(warnings)), list(dict.fromkeys(errors))

    def _item_at(self, save, cid, slot):
        c = ws.get_container(save, cid)
        e = c.data.get(str(slot))
        if not isinstance(e, dict):
            return c, None
        return c, {"slot": slot, "itemId": e.get("ItemData")}

    def _apply_one(self, save, e, warnings, errors):
        op = e["op"]
        if op == "setting":
            key, value = e["key"], e["value"]
            if key == "privacy" and isinstance(value, str):
                value = PRIVACY.index(value)
            ws.set_world_setting(save, key, value)
            if key in ("privacy", "crossplay"):
                warnings.append("Privacy and crossplay labels are read from the file; check them in the game's world settings.")
            return
        if op == "setslots":
            from .features import set_container_slots
            set_container_slots(save, e["container"], {int(k): v for k, v in e["slots"].items()})
            return
        if op == "playerpos":
            # The world keeps each character's position (its centre: feet Z + 88).
            ws.set_player_position(save, e["charGuid"], float(e["x"]), float(e["y"]),
                                   float(e["z"]) + ws.PLAYER_Z_OFFSET)
            return
        if op == "move":
            ws.move_actor(save, e["actor"], float(e["x"]), float(e["y"]), float(e["z"]))
            warnings.append("Moving objects is new: check it in game.")
            return
        if op == "removeActor":
            # Removing a record shifts later ids in the same level, so go from the back.
            def order(a):
                level, _, ix = a.rpartition("/")
                return (level, int(ix) if ix.isdigit() else 0)
            for a in sorted(e["actors"], key=order, reverse=True):
                ws.remove_actor(save, a)
            return
        if op == "collect":
            info = next((x for x in ws.list_containers(save) if x["id"] == e["container"]), {})
            cap = _capacity_of(info.get("class")) or max((info.get("maxSlotIndex") or -1) + 1, 16)
            ws.collect_dropped_items(save, e["actors"], e["container"], cap,
                                     max_stack=lambda iid: self.max_stack(self.items.get(iid)))
            warnings.append("Collecting dropped items is new: check the chest in game.")
            return
        cid, slot = e["container"], int(e["slot"])
        if op == "put":
            # Move a whole item entry in (from the backpack); keeps durability and upgrades.
            from .features import set_container_slots
            c = ws.get_container(save, cid, for_edit=True)
            slots = c.slots()
            if slot in slots:
                errors.append(f"Slot {slot} of that chest is not empty.")
                return
            from .features import normalize_entry
            slots[slot] = normalize_entry(e["entry"])
            set_container_slots(save, cid, slots)
            return
        if op in ("count", "durability"):
            _, it = self._item_at(save, cid, slot)
            if not it:
                errors.append(f"No item in slot {slot}.")
                return
            info = self.items.get(it["itemId"], {})
            value = int(e["value"])
            if op == "count":
                mx = self.max_stack(info or None)
                if not 1 <= value <= mx:
                    errors.append(f"{info.get('name', 'Item')}: amount must be 1 to {mx}.")
                    return
                ws.set_item_count(save, cid, slot, value)
            else:
                mx = info.get("maxDurability")
                if value < 1 or (mx and value > mx):
                    errors.append(f"{info.get('name', 'Item')}: durability must be 1 to {mx}.")
                    return
                ws.set_item_durability(save, cid, slot, value)
        elif op == "remove":
            ws.remove_item(save, cid, slot)
        elif op in ("add", "replace"):
            info = self.items.get(e["itemId"])
            if not info:
                errors.append("Unknown item.")
                return
            if op == "replace":
                ws.remove_item(save, cid, slot)
            if info.get("maxDurability"):
                ws.add_item(save, cid, slot, e["itemId"], durability=int(e.get("durability") or info["maxDurability"]))
            else:
                n = int(e.get("count") or 1)
                mx = self.max_stack(info)
                if not 1 <= n <= mx:
                    errors.append(f"{info['name']}: amount must be 1 to {mx}.")
                    return
                ws.add_item(save, cid, slot, e["itemId"], count=n)
            warnings.append(f"New item {info['name']} put in a chest: check it in game.")
        else:
            errors.append("Unknown change type: " + op)

    def diff_lines(self, save):
        out = []
        for change in ws.changes(save):
            cid, old, new = change[0], change[1], change[2]
            sm = difflib.SequenceMatcher(None, old.splitlines(), new.splitlines(), autojunk=False)
            a, b = old.splitlines(), new.splitlines()
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag != "equal":
                    out.append((f"[{cid}] " + " ".join(x.strip() for x in a[i1:i2]),
                                f"[{cid}] " + " ".join(x.strip() for x in b[j1:j2])))
        return out[:200]
