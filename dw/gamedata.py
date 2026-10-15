"""Build the editor's item library from the player's own game install.

Reads item, skill and icon assets straight from the IoStore containers and
writes a cache folder:
    <cache>/library.json   items, skills, XP curve
    <cache>/icons/*.png    item and skill icons
    <cache>/fonts/*.ttf    game fonts
Item stats (stack size, max durability, power level) come from a public item
database because they live in asset properties this tool cannot decode.
"""
import io
import json
import os
import re
import struct
import urllib.request

from PIL import Image

from . import core
from .version import USER_AGENT
from .iostore import GameFiles

LIBRARY_VERSION = 6
# Game UI textures reused as the editor's skin (saved to <cache>/ui/).
SKIN_TEXTURES = [
    "T_Common_ItemSlotsBackground", "T_Common_ItemSlotsHighlight", "T_ToolTip_Bg", "T_ToolTips_Title",
    "T_Tooltip_Banner_Bg", "T_Title_Divider_Line", "T_Ornate_Pattern_Divider", "T_Characters_BG_Container",
    "T_Skills_Frame", "T_Menu_Bg_Combined", "T_Craft_CraftButton", "T_Craft_CraftButtonBackground",
    "T_HUD_MenuNav_Background", "T_MainMenu_BackgroundGradiant", "T_Bg_Texture", "T_Divider_Round",
    "T_Craft_TitleBackground",
]
STATS_URL = "https://xgamingserver.com/tools/dragonwilds/items.json"
ICON_SIZE = 128

ID_RX = re.compile(rb"\x17\x00\x00\x00([A-Za-z0-9_-]{22})\x00")
WORDY_RX = re.compile(r"[a-z_]+|([A-Z][a-z]+)+")
ICON_SKIP = ("Buff", "/Tags/", "Journal", "Notification", "Watermark", "_Bg", "/HUD/")


def _persistence_id(buf):
    """The asset's save-file ID: a 22-char base64 string (last one wins)."""
    ids = [m.group(1).decode() for m in ID_RX.finditer(buf)]
    ids = [s for s in ids if not WORDY_RX.fullmatch(s)]
    return ids[-1] if ids else None


def _pick_icon(refs, prefer=()):
    textures = [r for r in refs if "/T_" in r]
    for key in prefer:
        hit = next((r for r in textures if key in r), None)
        if hit:
            return hit
    good = [r for r in textures if not any(s in r for s in ICON_SKIP)]
    return next((r for r in good if "/Icons/" in r), None) or next(iter(good), None) \
        or next(iter(textures), None)


def find_game_paks(game_dir):
    for root in [game_dir, os.path.join(game_dir, "RSDragonwilds")]:
        p = os.path.join(root, "Content", "Paks")
        if os.path.isfile(os.path.join(p, "RSDragonwilds-Windows.utoc")):
            return p
    p = os.path.join(game_dir, "RSDragonwilds", "Content", "Paks")
    return p if os.path.isdir(p) else None


def guess_game_dirs():
    """Common install locations to try before asking the user."""
    cands = []
    for drive in "CDEFG":
        subs = [r"Program Files (x86)\Steam\steamapps\common\RuneScape Dragonwilds",
                r"SteamLibrary\steamapps\common\RuneScape Dragonwilds", r"RuneScape Dragonwilds"]
        try:
            # Game, Games, Games2 ... folders at the drive root
            subs += [os.path.join(n, "RuneScape Dragonwilds") for n in os.listdir(f"{drive}:\\")
                     if re.match(r"(?i)^games?\d*$", n)]
        except OSError:
            pass
        for sub in subs:
            cands.append(f"{drive}:\\{sub}")
    return [c for c in cands if find_game_paks(c)]


def _name_map(buf):
    """Names of a zen (IoStore) package, read from its name batch."""
    i = buf.find(struct.pack("<Q", 0xC1640000))
    if i < 8:
        return []
    count, nbytes = struct.unpack_from("<II", buf, i - 8)
    pos = i + 8 + count * 8
    headers = [struct.unpack_from(">H", buf, pos + k * 2)[0] for k in range(count)]
    pos += count * 2
    names = []
    for h in headers:
        utf16 = bool(h & 0x8000)
        length = h & 0x7FFF
        if utf16:
            names.append(buf[pos:pos + length * 2].decode("utf-16-le", "replace"))
            pos += length * 2
        else:
            names.append(buf[pos:pos + length].decode("latin-1"))
            pos += length
    return names


def _game_refs(buf):
    """Package paths referenced by an asset (/Game/... or /<Plugin>/...)."""
    return [n for n in _name_map(buf) if n.startswith("/") and n.count("/") >= 2]


def _fstrings(buf):
    """All length-prefixed ASCII strings in a package, in file order."""
    out = []
    i = 0
    n = len(buf)
    while i < n - 5:
        (length,) = struct.unpack_from("<i", buf, i)
        if 2 <= length <= 400 and i + 4 + length <= n and buf[i + 3 + length] == 0:
            s = buf[i + 4:i + 3 + length]
            if all(32 <= c < 127 for c in s):
                out.append(s.decode())
                i += 4 + length
                continue
        i += 1
    return out


def _parse_string_table(buf):
    """Key -> value pairs of a StringTable asset (longest run of FString pairs)."""
    best = []
    i = 0
    n = len(buf)
    while i < n - 8:
        (count,) = struct.unpack_from("<i", buf, i)
        if 1 <= count <= 20000:
            pos = i + 4
            pairs = []
            ok = True
            for _ in range(count):
                vals = []
                for _ in range(2):
                    if pos + 4 > n:
                        ok = False
                        break
                    (length,) = struct.unpack_from("<i", buf, pos)
                    pos += 4
                    if length == 0:
                        vals.append("")
                    elif 0 < length < 10000 and pos + length <= n:
                        vals.append(buf[pos:pos + length - 1].decode("utf-8", "replace"))
                        pos += length
                    elif -10000 < length < 0 and pos - length * 2 <= n:
                        vals.append(buf[pos:pos - length * 2 - 2].decode("utf-16-le", "replace"))
                        pos += -length * 2
                    else:
                        ok = False
                        break
                if not ok:
                    break
                pairs.append(tuple(vals))
            if ok and len(pairs) > len(best):
                best = pairs
                i = pos
                continue
        i += 1
    return dict(best)


def _texture_to_image(gf, path):
    """Decode a cooked Texture2D (top mip) into a PIL image, or None."""
    buf = gf.read(path)
    m = None
    for m in re.finditer(rb"PF_[A-Z0-9a-z_]+\x00", buf):
        pass
    if not m:
        return None
    fmt = m.group(0)[:-1].decode()
    str_start = m.start() - 4
    w, h = struct.unpack_from("<ii", buf, str_start - 12)
    if not (0 < w <= 4096 and 0 < h <= 4096):
        return None
    bpp = {"PF_DXT1": 0.5, "PF_DXT5": 1, "PF_BC7": 1, "PF_BC5": 1,
           "PF_B8G8R8A8": 4, "PF_G8": 1}.get(fmt)
    if bpp is None:
        return None
    bw, bh = (w, h) if fmt in ("PF_B8G8R8A8", "PF_G8") else (max(4, (w + 3) // 4 * 4), max(4, (h + 3) // 4 * 4))
    size = int(bw * bh * bpp)
    tail = buf.find(struct.pack("<iii", w, h, 1), m.end())
    data = None
    if tail != -1 and tail - m.end() >= size:
        data = buf[tail - size:tail]
    else:
        ubulk = path[:-len(".uasset")] + ".ubulk"
        if ubulk in gf:
            bulk = gf.read(ubulk)
            if len(bulk) >= size:
                data = bulk[:size]
    if data is None:
        return None
    if fmt == "PF_B8G8R8A8":
        return Image.frombytes("RGBA", (w, h), data, "raw", "BGRA")
    if fmt == "PF_G8":
        return Image.frombytes("L", (w, h), data)
    dxgi = {"PF_DXT1": 71, "PF_DXT5": 77, "PF_BC5": 83, "PF_BC7": 98}[fmt]
    dds = bytearray(b"DDS ")
    hdr = bytearray(124)
    struct.pack_into("<IIIIIII", hdr, 0, 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, h, w, size, 0, 1)
    struct.pack_into("<II4s", hdr, 72, 32, 0x4, b"DX10")
    struct.pack_into("<I", hdr, 104, 0x1000)
    dds += hdr
    dds += struct.pack("<IIIII", dxgi, 3, 0, 1, 0)
    dds += data
    img = Image.open(io.BytesIO(bytes(dds)))
    img.load()
    return img


def _save_icon(gf, game_path, out_dir, done):
    """game_path like /Game/Art/UI/Icons/X/T_Icon_Y -> icons/T_Icon_Y.png"""
    if not game_path:
        return None
    name = game_path.rsplit("/", 1)[-1]
    if name in done:
        return done[name]
    rel = None
    if game_path.startswith("/Game/"):
        rel = "RSDragonwilds/Content/" + game_path[len("/Game/"):] + ".uasset"
    else:
        plugin, rest = game_path[1:].split("/", 1)
        rel = f"RSDragonwilds/Plugins/GameFeatures/{plugin}/Content/{rest}.uasset"
    if rel not in gf:
        # Plugin content (game features) is mounted under Plugins/...
        hits = gf.find(lambda p: p.endswith("/" + name + ".uasset"))
        rel = hits[0] if hits else None
    file = None
    if rel:
        try:
            img = _texture_to_image(gf, rel)
            if img is not None:
                img = img.convert("RGBA")
                img.thumbnail((ICON_SIZE, ICON_SIZE), Image.LANCZOS)
                file = name + ".png"
                img.save(os.path.join(out_dir, file), optimize=True)
        except Exception:
            file = None
    done[name] = file
    return file


def _category_from_path(path):
    p = path.lower()
    rules = [
        ("/equipment/head/", "Head"), ("/equipment/body/", "Body"), ("/equipment/legs/", "Legs"),
        ("/equipment/cape/", "Cape"), ("/equipment/ammo/", "Ammo"), ("trinket", "Trinket"),
        ("/held/farming/seeds/", "Seeds"), ("/held/", "Held"), ("/consumables/food/", "Food"),
        ("/consumables/", "Consumable"), ("/quest", "Quest"), ("/resources/", "Resource"),
    ]
    for key, cat in rules:
        if key in p:
            return cat
    return "Other"


def _fetch_stats(log):
    bundled = os.path.join(core.VENDOR_DIR, "items.json")
    if os.path.isfile(bundled):
        try:
            with open(bundled, encoding="utf-8") as f:
                rows = json.load(f)
            log(f"Loaded stats for {len(rows)} items")
            return {row["id"].lower(): row for row in rows}
        except (OSError, ValueError, KeyError) as e:
            log(f"Built-in item stats unreadable ({e}); trying the internet")
    try:
        req = urllib.request.Request(STATS_URL, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read().decode("utf-8"))
        log(f"Downloaded stats for {len(rows)} items")
        return {row["id"].lower(): row for row in rows}
    except Exception as e:
        log(f"Item stats download failed ({e}); limits will be estimated")
        return {}


def _xp_curve(gf):
    hits = gf.find(lambda p: p.endswith("/Skills/CT_XPByLevel.uasset"))
    if not hits:
        return None
    buf = gf.read(hits[0])
    best = None
    # Rows: count, 8 bytes, then (level, xp) float pairs starting at (1, 0).
    for off in range(16, len(buf) - 16):
        if buf[off:off + 8] == struct.pack("<ff", 1.0, 0.0):
            (count,) = struct.unpack_from("<i", buf, off - 12)
            if 10 <= count <= 200 and off + count * 8 <= len(buf):
                keys = [struct.unpack_from("<ff", buf, off + k * 8) for k in range(count)]
                keys = [(int(a), int(b)) for a, b in keys if a < 1000]
                best = keys  # last full table in the file is the current one
    return [xp for _, xp in best] if best else None


def build_library(game_dir, cache_dir, log=print):
    paks = find_game_paks(game_dir)
    if not paks:
        raise FileNotFoundError("Could not find Content\\Paks in " + game_dir)
    log("Reading game containers...")
    gf = GameFiles(paks)
    icon_dir = os.path.join(cache_dir, "icons")
    font_dir = os.path.join(cache_dir, "fonts")
    os.makedirs(icon_dir, exist_ok=True)
    os.makedirs(font_dir, exist_ok=True)

    stats = _fetch_stats(log)
    try:
        from .capacity import build_stack_table
        game_stacks = {k.lower(): v for k, v in build_stack_table(gf).items()}
        log(f"Read stack sizes for {len(game_stacks)} items from the game files")
    except Exception as e:
        log(f"Stack sizes from game files skipped: {e}")
        game_stacks = {}
    tables = {}

    def table(game_path):
        name = game_path.rsplit("/", 1)[-1].split(".")[0]
        if name not in tables:
            hits = gf.find(lambda p: p.endswith("/" + name + ".uasset"))
            tables[name] = _parse_string_table(gf.read(hits[0])) if hits else {}
        return tables[name]

    icons_done = {}
    items = {}
    item_paths = gf.find(lambda p: re.search(r"/ITEM_[^/]*\.uasset$", p) is not None)
    log(f"Found {len(item_paths)} item assets")
    for n, path in enumerate(sorted(item_paths)):
        buf = gf.read(path)
        pid = _persistence_id(buf)
        if not pid:
            continue
        asset = path.rsplit("/", 1)[-1][:-len(".uasset")]
        refs = _game_refs(buf)
        icon = _pick_icon(refs)
        st_paths = [r for r in refs if "/ST_" in r and "." not in r]
        name_tables = [table(r) for r in st_paths if "Description" not in r]
        desc_tables = [table(r) for r in st_paths if "Description" in r]
        strs = _fstrings(buf)
        name = desc = None
        for s in strs:
            if name is None:
                name = next((t[s] for t in name_tables if s in t), None)
            if desc is None:
                desc = next((t[s] for t in desc_tables if s in t), None)
        st = stats.get(asset.lower(), {})
        items[pid] = {
            "id": pid,
            "asset": asset,
            "name": name or st.get("name") or asset.replace("ITEM_", "").replace("_", " "),
            "description": desc or st.get("description") or "",
            "category": st.get("category") or _category_from_path(path),
            "slot": _category_from_path(path),
            "path": path,
            "icon": _save_icon(gf, icon, icon_dir, icons_done),
            "maxStack": game_stacks.get(asset.lower(), st.get("maxStack")),
            "maxDurability": st.get("durability") or None,
            "powerLevel": st.get("powerLevel"),
            "tier": st.get("tier"),
            "skill": st.get("skill"),
            "weight": st.get("weight"),
        }
        if n % 200 == 0:
            log(f"Items: {n}/{len(item_paths)}")

    skills = {}
    ui_dir = os.path.join(cache_dir, "ui")
    os.makedirs(ui_dir, exist_ok=True)
    for name in SKIN_TEXTURES:
        hits = gf.find(lambda p, n=name: p.endswith("/" + n + ".uasset") and "/Art/UI/" in p)
        if hits:
            try:
                img = _texture_to_image(gf, hits[0])
                if img is not None:
                    img.convert("RGBA").save(os.path.join(ui_dir, name + ".png"), optimize=True)
            except Exception:
                pass

    for path in gf.find(lambda p: re.search(r"/Player/Skills/SKILL_[^/]*\.uasset$", p) is not None):
        buf = gf.read(path)
        sid = _persistence_id(buf)
        if not sid:
            continue
        asset = path.rsplit("/", 1)[-1][:-len(".uasset")]
        refs = _game_refs(buf)
        icon = _pick_icon(refs, prefer=("T_Icon_Tag_Skill_", "T_Notification_Skill_", "T_Icon_SkillTree_", "T_SW_"))
        skills[sid] = {
            "id": sid,
            "asset": asset,
            "name": asset.replace("SKILL_", ""),
            "icon": _save_icon(gf, icon, icon_dir, icons_done),
        }

    ui_dir = os.path.join(cache_dir, "ui")
    os.makedirs(ui_dir, exist_ok=True)
    for name in SKIN_TEXTURES:
        hits = gf.find(lambda p, n=name: p.endswith("/" + n + ".uasset") and "/Art/UI/" in p)
        if hits:
            try:
                img = _texture_to_image(gf, hits[0])
                if img is not None:
                    img.convert("RGBA").save(os.path.join(ui_dir, name + ".png"), optimize=True)
            except Exception:
                pass

    for path in gf.find(lambda p: re.search(r"/UI/Fonts/(Jancient|Poppins-Regular|Poppins-Medium|BonaNovaSC-Regular)\.ufont$", p)):
        name = path.rsplit("/", 1)[-1][:-len(".ufont")]
        raw = gf.read(path)
        # .ufont = u32 length + font file (+ padding)
        (n,) = struct.unpack_from("<I", raw, 0)
        if 0 < n <= len(raw) - 4:
            raw = raw[4:4 + n]
        with open(os.path.join(font_dir, name + ".ttf"), "wb") as f:
            f.write(raw)

    try:
        from .mapdata import build_map_assets
        log("Extracting world map...")
        maps = build_map_assets(gf, cache_dir, log=log).get("maps", [])
    except Exception as e:  # the map is optional; the editor works without it
        log(f"World map skipped: {e}")
        maps = []

    try:
        from .unlocks import build_unlocks
        log("Reading recipes, buildings, spells, journal and mounts...")
        unlock_lib = build_unlocks(gf, cache_dir, log=log, items=items)
    except Exception as e:  # optional, like the map
        log(f"Unlocks skipped: {e}")
        unlock_lib = None

    lib = {
        "version": LIBRARY_VERSION,
        "maps": maps,
        "unlocks": unlock_lib,
        "gameDir": game_dir,
        "pakStamp": _pak_stamp(paks),
        "items": items,
        "skills": skills,
        "xpCurve": _xp_curve(gf),
        "fonts": sorted(os.listdir(font_dir)),
    }
    with open(os.path.join(cache_dir, "library.json"), "w", encoding="utf-8") as f:
        json.dump(lib, f, indent=1)
    log(f"Library ready: {len(items)} items, {len(skills)} skills, "
        f"{sum(1 for v in icons_done.values() if v)} icons")
    return lib


def _pak_stamp(paks):
    p = os.path.join(paks, "RSDragonwilds-Windows.utoc")
    st = os.stat(p)
    return f"{st.st_size}-{int(st.st_mtime)}"


def load_library(cache_dir):
    p = os.path.join(cache_dir, "library.json")
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        lib = json.load(f)
    if lib.get("version") != LIBRARY_VERSION:
        return None
    return lib


def library_is_stale(lib):
    paks = find_game_paks(lib.get("gameDir", ""))
    return not paks or _pak_stamp(paks) != lib.get("pakStamp")
