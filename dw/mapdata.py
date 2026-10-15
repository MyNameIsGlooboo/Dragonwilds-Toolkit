"""World map art and world-to-map calibration for RuneScape: Dragonwilds.

The in-game map is drawn by BP_MapBackground actors placed in L_World. Each one
has an "AreaBounds" box component (centre + half extent, in world units) and the
map texture is stretched over that box, seen from above with the usual Unreal
top-view orientation: world +X to the right, world +Y downwards.

  * World map   : /Game/Art/UI/Map/T_WorldMap_Landmass  (6144x6144 DXT5, one image
                  holding the base world and the Dowdun Reach, Scorned Wilderness
                  and Umbral Sands areas)
  * Underground : /Game/Art/UI/Map/T_Map_Dowdun_Underground (592x592 BC7)

The bounds are read from L_World at build time; if that fails the values found
in the current game build are used.
"""

import io
import os
import struct
import time

try:
    from PIL import Image
except ImportError:  # pragma: no cover
    Image = None

CELL_SIZE = 12800  # world save cells "RSACell12800_X<i>_Y<j>" = floor(pos / 12800)
MAX_SIDE = 4096
SMALL_SIDE = 2048

LEVEL = "RSDragonwilds/Content/Maps/World/L_World.umap"

# Fallback bounds (centre x, centre y, half extent x, half extent y), as read
# from L_World's two BP_MapBackground actors in the 2026-10 game build.
FALLBACK_BOUNDS = {
    "world": (198000.0, 91450.0, 210000.0, 210000.0),
    "dowdun_underground": (218924.192131, 41213.289249, 16500.0, 16800.0),
}

MAPS = [
    {"id": "world", "name": "World",
     "texture": "RSDragonwilds/Content/Art/UI/Map/T_WorldMap_Landmass.uasset"},
    {"id": "dowdun_underground", "name": "Dowdun Reach (underground)",
     "texture": "RSDragonwilds/Content/Art/UI/Map/T_Map_Dowdun_Underground.uasset"},
]


# --------------------------------------------------------------------------
# transform

def make_transform(bounds, width, height):
    """Affine transform for an image of width x height covering bounds."""
    cx, cy, ex, ey = bounds
    return {
        "originX": cx - ex,           # world X at the left edge (px = 0)
        "originY": cy - ey,           # world Y at the top edge (py = 0)
        "scaleX": width / (2.0 * ex),   # pixels per world unit along X
        "scaleY": height / (2.0 * ey),  # pixels per world unit along Y
        "width": width,
        "height": height,
        "rotation": 0,
        "swapXY": False,   # px follows world X, py follows world Y
        "flipX": False,
        "flipY": False,
        "bounds": {"minX": cx - ex, "maxX": cx + ex,
                   "minY": cy - ey, "maxY": cy + ey},
    }


def world_to_map(transform, x, y, image_size=None):
    """World (x, y) -> pixel (px, py) on the map image.

    image_size=(w, h) rescales to another copy of the image (e.g. the small one).
    """
    t = transform
    u = (x - t["originX"]) * t["scaleX"]
    v = (y - t["originY"]) * t["scaleY"]
    w, h = t["width"], t["height"]
    if t.get("swapXY"):
        u, v = v * w / h, u * h / w
    if t.get("flipX"):
        u = w - u
    if t.get("flipY"):
        v = h - v
    if image_size:
        u *= image_size[0] / w
        v *= image_size[1] / h
    return u, v


def map_to_world(transform, px, py, image_size=None):
    """Inverse of world_to_map (rotation 0 transforms only)."""
    t = transform
    w, h = t["width"], t["height"]
    if image_size:
        px *= w / image_size[0]
        py *= h / image_size[1]
    if t.get("flipX"):
        px = w - px
    if t.get("flipY"):
        py = h - py
    if t.get("swapXY"):
        px, py = py * w / h, px * h / w
    return px / t["scaleX"] + t["originX"], py / t["scaleY"] + t["originY"]


def cell_of(x, y):
    """World save cell indices for a world position."""
    return int(x // CELL_SIZE), int(y // CELL_SIZE)


# --------------------------------------------------------------------------
# reading the bounds from L_World

def _zen_exports(buf):
    from .gamedata import _name_map
    hsize = struct.unpack_from("<I", buf, 4)[0]
    exp_off, bundle_off = struct.unpack_from("<ii", buf, 32)
    names = _name_map(buf)
    out = []
    for k in range((bundle_off - exp_off) // 72):
        off, size, ni, nn, outer = struct.unpack_from("<QQIIQ", buf, exp_off + k * 72)
        name = names[ni] if ni < len(names) else ""
        out.append({"i": k, "pos": hsize + off, "size": size, "name": name, "outer": outer})
    return out


def _plausible(v, lo, hi):
    return v == v and lo <= abs(v) <= hi


def read_level_bounds(gf, log=print):
    """{"world": (cx, cy, ex, ey), "dowdun_underground": (...)} from L_World."""
    if LEVEL not in gf:
        return {}
    buf = gf.read(LEVEL)
    exports = _zen_exports(buf)
    found = []
    for e in exports:
        if not e["name"].startswith("BP_MapBackground_C"):
            continue
        for c in exports:
            if c["outer"] != e["i"] or c["name"] != "AreaBounds" or c["size"] < 60:
                continue
            d = buf[c["pos"]:c["pos"] + c["size"]]
            ex, ey, ez = struct.unpack_from("<3d", d, 6)
            cx, cy, cz = struct.unpack_from("<3d", d, 36)
            if (_plausible(ex, 1000, 5e6) and _plausible(ey, 1000, 5e6)
                    and abs(cx) < 5e6 and abs(cy) < 5e6 and abs(ex / ey - 1) < 0.25):
                found.append((cx, cy, ex, ey))
    del buf
    if not found:
        return {}
    found.sort(key=lambda b: -b[2] * b[3])
    out = {"world": found[0]}
    if len(found) > 1:
        out["dowdun_underground"] = found[1]
    return out


# --------------------------------------------------------------------------
# texture decoding (handles sizes above 4096, unlike gamedata._texture_to_image)

_BPP = {"PF_DXT1": 0.5, "PF_DXT5": 1, "PF_BC7": 1, "PF_BC5": 1, "PF_BC4": 0.5,
        "PF_B8G8R8A8": 4, "PF_G8": 1}
_DXGI = {"PF_DXT1": 71, "PF_DXT5": 77, "PF_BC4": 80, "PF_BC5": 83, "PF_BC7": 98}


def decode_texture(gf, path):
    """Top mip of a cooked Texture2D as a PIL image, or None."""
    import re
    buf = gf.read(path)
    m = None
    for m in re.finditer(rb"PF_[A-Z0-9a-z_]+\x00", buf):
        pass
    if not m:
        return None
    fmt = m.group(0)[:-1].decode()
    w, h = struct.unpack_from("<ii", buf, m.start() - 4 - 12)
    bpp = _BPP.get(fmt)
    if bpp is None or not (0 < w <= 16384 and 0 < h <= 16384):
        return None
    block = fmt not in ("PF_B8G8R8A8", "PF_G8")
    bw, bh = ((w + 3) // 4 * 4, (h + 3) // 4 * 4) if block else (w, h)
    size = int(bw * bh * bpp)
    data = None
    tail = buf.find(struct.pack("<iii", w, h, 1), m.end())
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
    hdr = bytearray(124)
    struct.pack_into("<IIIIIII", hdr, 0, 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x80000, h, w, size, 0, 1)
    struct.pack_into("<II4s", hdr, 72, 32, 0x4, b"DX10")
    struct.pack_into("<I", hdr, 104, 0x1000)
    dds = b"DDS " + bytes(hdr) + struct.pack("<IIIII", _DXGI[fmt], 3, 0, 1, 0) + data
    img = Image.open(io.BytesIO(dds))
    img.load()
    return img


def _fit(img, side):
    w, h = img.size
    if max(w, h) <= side:
        return img
    k = side / max(w, h)
    return img.resize((max(1, round(w * k)), max(1, round(h * k))), Image.LANCZOS)


# --------------------------------------------------------------------------
# public entry point

def build_map_assets(gf, out_dir, log=print):
    """Write map images to out_dir/map/ and return {"maps": [...], "cell_size": 12800}.

    Never raises: problems are logged and the affected map is left out.
    """
    t0 = time.time()
    result = {"maps": [], "cell_size": CELL_SIZE}
    if Image is None:
        log("map: Pillow not available, no map images")
        return result
    try:
        bounds = read_level_bounds(gf, log)
        src = "L_World"
    except Exception as e:  # noqa: BLE001
        bounds, src = {}, None
        log("map: could not read map bounds from the level (%s)" % e)
    if "world" not in bounds:
        log("map: using built-in map bounds")
        bounds = dict(FALLBACK_BOUNDS)
        src = "built-in"
    for k, v in FALLBACK_BOUNDS.items():
        bounds.setdefault(k, v)

    map_dir = os.path.join(out_dir, "map")
    try:
        os.makedirs(map_dir, exist_ok=True)
    except OSError as e:
        log("map: cannot create %s (%s)" % (map_dir, e))
        return result

    for spec in MAPS:
        mid = spec["id"]
        try:
            if spec["texture"] not in gf:
                log("map: %s texture not found in game files" % mid)
                continue
            img = decode_texture(gf, spec["texture"])
            if img is None:
                log("map: %s texture format not supported" % mid)
                continue
            big = _fit(img, MAX_SIDE)
            small = _fit(img, SMALL_SIDE)
            big_name = "%s.png" % mid
            small_name = "%s_%d.png" % (mid, SMALL_SIDE)
            big.save(os.path.join(map_dir, big_name), optimize=False, compress_level=3)
            small.save(os.path.join(map_dir, small_name), optimize=False, compress_level=3)
            tr = make_transform(bounds[mid], big.size[0], big.size[1])
            result["maps"].append({
                "id": mid,
                "name": spec["name"],
                "image": "map/" + big_name,
                "small": "map/" + small_name,
                "width": big.size[0],
                "height": big.size[1],
                "small_width": small.size[0],
                "small_height": small.size[1],
                "source": spec["texture"],
                "bounds_from": src,
                "transform": tr,
                "confidence": "high" if mid == "world" else "medium",
            })
        except Exception as e:  # noqa: BLE001
            log("map: %s failed (%s)" % (mid, e))
    log("map: %d map(s) in %.1fs" % (len(result["maps"]), time.time() - t0))
    return result
