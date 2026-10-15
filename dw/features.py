"""Editor features that span saves: teleport height, presets, organizer, compare."""
import json
import math
import os

from . import worldsave as ws
from .core import APP_DIR, DEFAULT_MAX_STACK
from .savefile import new_guid

PRESETS_FILE = os.path.join(APP_DIR, "presets.json")


def normalize_entry(entry):
    """Item entry copied between saves: numbers arrive as text from the UI; write real numbers."""
    out = {}
    for k, v in entry.items():
        if k in ("GUID", "ItemData"):
            out[k] = v
            continue
        if isinstance(v, str):
            try:
                f = float(v)
                v = int(f) if f.is_integer() else f
            except ValueError:
                pass
        out[k] = v
    return out


def max_stack(it):
    m = (it or {}).get("maxStack")
    return DEFAULT_MAX_STACK if m is None else max(1, m)


# ------------------------------------------------------------------ teleport
def ground_height(save, x, y, radius=2500.0):
    """Height for a teleport target: from the nearest saved objects around (x, y).

    Uses the median Z of up to 5 nearest actors within the radius, plus 150 units
    so the character starts just above the ground. None when nothing is near."""
    near = []
    for a in ws.list_actors(save):
        p = a.get("position")
        if not p or all(abs(v) < 1 for v in p[:2]):
            continue
        d = math.hypot(p[0] - x, p[1] - y)
        if d <= radius:
            near.append((d, p[2], a.get("class", "")))
    if not near:
        return None
    near.sort()
    zs = sorted(z for _, z, _ in near[:5])
    return {"z": zs[len(zs) // 2] + 150.0, "nearest": near[0][2], "distance": round(near[0][0])}


# ------------------------------------------------------------------- presets
BUILTIN_PRESETS = [
    {"id": "food", "name": "Food crate", "desc": "Your best cooked foods, a full stack each.", "rule": "food"},
    {"id": "build", "name": "Builder's crate", "desc": "Stone, stone blocks, logs, planks, clay, iron bars.",
     "assets": ["ITEM_Resources_Stone", "ITEM_Resources_Stone_Block", "ITEM_Resources_Wood_Oak",
                "ITEM_Resources_Plank_Oak", "ITEM_Resources_Clay", "ITEM_Resources_IronBar"]},
    {"id": "smith", "name": "Smithing", "desc": "Ores, coal and bars for smelting and smithing.", "rule": "smith"},
    {"id": "potions", "name": "Potions", "desc": "The strongest potions of each kind.", "rule": "potions"},
    {"id": "runes", "name": "Runes", "desc": "Every elemental and catalytic rune.", "rule": "runes"},
    {"id": "ammo", "name": "Ammo", "desc": "Arrows and bolts of your best tier.", "rule": "ammo"},
]


def load_user_presets():
    try:
        with open(PRESETS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def save_user_presets(presets):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(PRESETS_FILE, "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=1)


def _best(items, pred, n):
    """Top n items matching pred, best tier / power level first."""
    pool = [it for it in items.values() if pred(it) and not it["asset"].endswith(("_Deprecated", "_Deleted"))]
    pool.sort(key=lambda it: (-(it.get("tier") or 0), -(it.get("powerLevel") or 0), it["name"]))
    seen, out = set(), []
    for it in pool:
        if it["name"] in seen:
            continue
        seen.add(it["name"])
        out.append(it)
        if len(out) >= n:
            break
    return out


def preset_items(preset, items):
    """[(item, amount)] for a built-in or user preset. Amount = full stack unless the preset says."""
    if preset.get("items"):  # user preset
        out = []
        for row in preset["items"]:
            it = items.get(row["itemId"])
            if it:
                out.append((it, row.get("count") or row.get("durability") or 1))
        return out
    if preset.get("assets"):
        by_asset = {v["asset"].lower(): v for v in items.values()}
        chosen = [by_asset[a.lower()] for a in preset["assets"] if a.lower() in by_asset]
    else:
        rule = preset.get("rule")
        cat = lambda it: (it.get("category") or "")
        a = lambda it: it["asset"]
        if rule == "food":
            bad = ("Raw", "Burnt", "Spoiled", "Rotten", "Ruined", "Uncooked")
            chosen = _best(items, lambda it: cat(it) == "Food" and not any(b in it["name"] for b in bad), 8)
        elif rule == "smith":
            chosen = [it for it in items.values() if a(it).startswith("ITEM_Resources_") and
                      (a(it).endswith("Bar") or "Ore" in a(it) or a(it).endswith("_Coal"))
                      and not a(it).endswith(("_Deprecated", "_Deleted"))][:12]
        elif rule == "potions":
            chosen = _best(items, lambda it: cat(it) == "Potions", 8)
        elif rule == "runes":
            chosen = [it for it in items.values() if a(it).startswith("ITEM_Rune_") and "Essence" not in a(it)]
        elif rule == "ammo":
            chosen = _best(items, lambda it: cat(it) == "Ammo" and ("Arrow" in a(it) or "Bolt" in a(it)), 4)
        else:
            chosen = []
    out = []
    for it in chosen:
        out.append((it, it["maxDurability"] if it.get("maxDurability") else max_stack(it)))
    return out


def plan_fill(existing, wanted, items, capacity):
    """Edits that put `wanted` [(item, amount)] into a container.

    existing: {slot: entry}. Tops up existing stacks of the same item first, then
    uses free slots below capacity. Returns (edits as dicts with op/slot/..., skipped names)."""
    edits, skipped = [], []
    slots = dict(existing)
    for it, amount in wanted:
        if it.get("maxDurability"):
            free = next((s for s in range(capacity) if s not in slots), None)
            if free is None:
                skipped.append(it["name"])
                continue
            slots[free] = {"ItemData": it["id"]}
            edits.append({"op": "add", "slot": free, "itemId": it["id"], "durability": int(amount)})
            continue
        mx = max_stack(it)
        left = int(amount)
        for s, e in sorted(slots.items()):
            if left <= 0:
                break
            if e.get("ItemData") == it["id"] and "Count" in e and int(e["Count"]) < mx:
                add = min(mx - int(e["Count"]), left)
                e = dict(e, Count=int(e["Count"]) + add)
                slots[s] = e
                edits.append({"op": "count", "slot": s, "value": e["Count"]})
                left -= add
        while left > 0:
            free = next((s for s in range(capacity) if s not in slots), None)
            if free is None:
                skipped.append(it["name"])
                break
            n = min(mx, left)
            slots[free] = {"ItemData": it["id"], "Count": n}
            edits.append({"op": "add", "slot": free, "itemId": it["id"], "count": n})
            left -= n
    # keep only the last count edit per slot
    final, seen = [], set()
    for e in reversed(edits):
        key = (e["op"], e["slot"]) if e["op"] == "count" else None
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        final.append(e)
    return list(reversed(final)), skipped


# ----------------------------------------------------------------- organizer
def _group(it):
    cat = (it or {}).get("category") or "Other"
    return {"Melee Weapons": "Weapons", "Crossbows": "Weapons", "Bows": "Weapons", "Staves": "Weapons",
            "Shields": "Weapons", "Helms": "Armour", "Chestplates": "Armour", "Leggings": "Armour",
            "Capes": "Armour", "Building Resources": "Building", "Resources": "Resources"}.get(cat, cat)


def plan_organize(save, container_ids, items, capacities):
    """New contents for each selected chest: stacks merged, items grouped by kind.

    capacities: {container id: slot count}. Returns ({cid: {slot: entry}}, summary)
    or raises ValueError when everything doesn't fit."""
    pool = []
    for cid in container_ids:
        c = ws.get_container(save, cid, for_edit=True)
        for slot, e in sorted(c.slots().items()):
            pool.append(dict(e))
    before = len(pool)
    # merge stackables of the same item
    merged, stacks = [], {}
    for e in pool:
        if "Count" in e and "Durability" not in e:
            stacks.setdefault(e["ItemData"], []).append(e)
        else:
            merged.append(e)
    for item_id, es in stacks.items():
        mx = max_stack(items.get(item_id))
        total = sum(int(x["Count"]) for x in es)
        template, first = es[0], True
        while total > 0:
            n = min(mx, total)
            e = dict(template, Count=n)
            if not first:
                e["GUID"] = new_guid()  # each extra stack needs its own instance id
            first = False
            merged.append(e)
            total -= n
    # sort: group, then name, so each kind sits together
    name = lambda e: (items.get(e["ItemData"]) or {}).get("name", "~")
    merged.sort(key=lambda e: (_group(items.get(e["ItemData"])), name(e), -int(e.get("Count", 1))))
    total_cap = sum(capacities.get(c, 0) for c in container_ids)
    if len(merged) > total_cap:
        raise ValueError(f"Not enough room: {len(merged)} stacks after merging, "
                         f"but the selected chests hold {total_cap}.")
    # Pack group by group. A group that fits whole in the next chest starts there,
    # so each kind of item ends up together; only big groups are split.
    groups = []
    for e in merged:
        g = _group(items.get(e["ItemData"]))
        if not groups or groups[-1][0] != g:
            groups.append((g, []))
        groups[-1][1].append(e)
    order = sorted(container_ids, key=lambda cid: capacities.get(cid, 0), reverse=True)
    out = {cid: {} for cid in container_ids}
    ci, used = 0, 0
    left = len(merged)
    for g, es in groups:
        room = capacities.get(order[ci], 0) - used
        rest = sum(capacities.get(c, 0) for c in order[ci + 1:])  # room in the chests after this one
        if 0 < room < len(es) and ci + 1 < len(order) and len(es) <= capacities.get(order[ci + 1], 0) \
                and rest >= left:
            ci, used = ci + 1, 0  # start this group in a fresh chest
        for e in es:
            while used >= capacities.get(order[ci], 0):
                ci, used = ci + 1, 0
            out[order[ci]][used] = e
            used += 1
            left -= 1
    return out, {"stacksBefore": before, "stacksAfter": len(merged), "chests": len(container_ids)}


def set_container_slots(save, cid, slots):
    """Replace a chest's contents with {slot: entry} (entries kept whole)."""
    c = ws.get_container(save, cid, for_edit=True)
    head = {k: v for k, v in c.data.items() if k == "Version"}
    rest = {k: v for k, v in c.data.items() if k != "Version" and not k.isdigit()}
    if "MaxSlotIndex" in rest and slots and int(rest["MaxSlotIndex"]) < max(slots):
        rest["MaxSlotIndex"] = max(slots)
    new = dict(head)
    for k in sorted(slots):
        new[str(k)] = slots[k]
    new.update(rest)
    c.data.clear()
    c.data.update(new)
    c.commit()


# ------------------------------------------------------------------- compare
def _char_summary(path, items):
    from .savefile import CharacterSave
    s = CharacterSave(path)
    p = s.progress
    inv = {}
    for key in ("Inventory", "Loadout", "PersonalInventory"):
        for e in s.container(key).values():
            if e.get("ItemData"):
                inv[e["ItemData"]] = inv.get(e["ItemData"], 0) + int(float(e.get("Count", 1)))
    skills = {x["Id"]: int(float(x.get("Xp", 0))) for x in (p.get("Skills", {}).get("Skills") or [])}
    prog = {k: len(v) for k, v in (p.get("Progress") or {}).items() if isinstance(v, list)}
    return {"items": inv, "skills": skills, "progress": prog}


def _world_summary(path):
    s = ws.WorldSave(path)
    inv = {}
    for c in ws.list_containers(s):
        if c["kind"] in ("storage", "crafting station"):
            for it in c["items"]:
                inv[it["itemId"]] = inv.get(it["itemId"], 0) + int(it.get("count", 1))
    info = ws.world_info(s)
    return {"items": inv, "revision": info.get("saveFileRevision"), "name": info.get("name")}


def compare_files(old_path, new_path, items, skills_lib):
    """Human summary of what changed between two copies of the same save."""
    name = lambda i: (items.get(i) or {}).get("name", "Unknown item")
    lines = []
    if old_path.lower().endswith(".sav"):
        a, b = _world_summary(old_path), _world_summary(new_path)
        lines.append(f"Save count {a['revision']} → {b['revision']}")
        label = "in your storage"
    else:
        a, b = _char_summary(old_path, items), _char_summary(new_path, items)
        for sid in sorted(set(a["skills"]) | set(b["skills"])):
            d = b["skills"].get(sid, 0) - a["skills"].get(sid, 0)
            if d:
                lines.append(f"{(skills_lib.get(sid) or {}).get('name', 'Skill')} XP {d:+,}")
        for k in sorted(set(a["progress"]) | set(b["progress"])):
            d = b["progress"].get(k, 0) - a["progress"].get(k, 0)
            if d:
                lines.append(f"{k}: {d:+}")
        label = "carried"
    deltas = []
    for i in set(a["items"]) | set(b["items"]):
        d = b["items"].get(i, 0) - a["items"].get(i, 0)
        if d:
            deltas.append((name(i), d))
    deltas.sort(key=lambda x: -abs(x[1]))
    for n, d in deltas[:40]:
        lines.append(f"{n} {d:+,} {label}")
    if len(deltas) > 40:
        lines.append(f"…and {len(deltas) - 40} more item changes")
    return lines or ["No differences found."]
