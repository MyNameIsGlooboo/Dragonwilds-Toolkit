"""Read and write Dragonwilds WORLD saves (SaveGames\\*.sav) without changing their layout.

A world save is Jagex's "Dominion" persistence format (Unreal Engine 5.6). It is NOT
an Unreal .sav/GVAS file. Everything is little-endian. Format, as found in the files:

    chunk    = tag (4 ASCII upper-case letters) + u32 payload size + payload
    FString  = i32 n; n > 0: n bytes of Latin-1 incl. a trailing NUL;
                      n < 0: -n UTF-16LE code units incl. a trailing NUL; n == 0: empty

    SAVE                              whole file
      INFO   19-byte header (u16 8, u32 522 = UE4 file version, u32 1017 = UE5 file
             version, 9 constant bytes) + FString ISO time of save, then:
        CINF   key/value dictionary for the world list: u32 n + n FString keys,
               u32 n + (n+1) u32 offsets (last = data size) + data blob. Value types
               are not stored; they are known per key (see INFO_TYPES).
      GLOB   FString "L_World", then:
        META   (see below)
        GOBS   NOBJ* global objects (DomPersistence: WorldSaveSettings)
        GLAI   LVNI (u32 n + n FString cell names), then (no count) a list of
               (FString actor name, u32 cell index) pairs up to the chunk end.
      LVLS
        LEVL*  one per map cell ("L_World" or "RSACell12800_X7_Y33"):
               FString cell name, u32 522, u32 1017, then:
          META   VERS (u32 5)
                 CNIX class table: u32 n + n FString class paths
                 CLST n x CDVE: empty, or u8 0 + CDEF (FString class path, u16 n,
                      n x (u32 name index, i32 outer name index or -1, u16 type code))
                 PNIX property-name table: u32 n + n FString
          LATS   NOBJ* actors placed in the level (have a stable actor name):
                 u32 class index, FString actor name, u32 n + n u32 (class indices of
                 the nested components), u32 522, u32 1017, then chunks:
                   CORA  u32 155 + u8 has-transform + 2 bytes + quat (4 doubles XYZW)
                         + translation (3 doubles) + scale (3 doubles) + 72 bytes
                   PROP  property block (below), class = the object's class index
                   CUST* custom binary data written by the actor itself (raw)
          SATS   SPWN* actors spawned at run time (buildings, chests, ...):
                 u32 class index, 16-byte GUID, 2 x FVector (6 doubles), u8,
                 u32 522, u32 1017, u32 n + n u32 component class indices, then
                 CORA / PROP / CUST* as above
          DATS   KILL*: FString name of a placed actor that was destroyed

    PROP (property block) = u32 n, (n+1) u32 offsets (last = data size), data blob.
         Value i is data[off[i]:off[i+1]] and is typed by field i of the class's CDEF:
           0x00 bool (u8)           0x01 enum (u16)          0x02 uint32
           0x06 int32               0x07 int64 (ticks)       0x08 float
           0x14 FVector (3 doubles) 0x15 FRotator (3 doubles) 0x17 FGuid (16 bytes)
           0x1e FString             0x1f FName (as FString)   0x20 FText
           0x28 nested object (component / struct): u32 class index (-1 = none)
                + a PROP block typed by that class's CDEF
           0x40 Unreal tagged-property data (UE 5.4+ tags with complete type names)
           0x1000 | t  array: u16 count + count values of type t

Storage contents (chests, crates, stations, loot chests, gravestones) are a FString
property "JsonInventory" holding the same pretty-printed JSON as the character save
inventory ({"Version": 83, "0": {"GUID", "ItemData", "Count"|"Durability"}, ...,
"MaxSlotIndex": n}), usually inside the component "BP_Components_WorldItemInventory".

Crafting stations store an inventory up to three times (component, ProcessingStation
mirror, ProcessingStation.CustomSaveData JSON); edits are written to every copy.

No checksum or hash was found: every byte is either data or a size/offset that is
recomputed on write. Anything this module does not understand is kept as raw bytes,
so an unchanged file is written back byte for byte.

SPWN header in detail: u32 class index, 16-byte actor GUID, bounding box (min xyz,
max xyz as 6 doubles), u8 1, u32 522, u32 1017, u32 n + n component class indices.
CORA tail (72 bytes) = 9 doubles, the first 3 are the linear velocity.

Spawned actors are kept in their cell's SATS (cell = floor(x / 12800), floor(y / 12800))
or, when near a player at save time, in L_World's SATS; the game moves records between
the two by renumbering class indices. GLAI and KILL concern placed actors only.
L_World's placed actor GameplayObjectRegistry lists Gravestones / Lodestones / Beds
(Location, owner GUIDs, Identifier = the actor's GameplayObjectRegistryIdentifier).

Public API: WorldSave(path) / load(path), save.roundtrip_ok, save.write(path=None),
save_bytes(save), world_info(save), set_world_setting(save, key, value) for
EDITABLE_SETTINGS, list_cells, list_actors, list_containers(save, library_items),
get_container, set_item_count, set_item_durability, remove_item, add_item,
container_slots_used, container_free_slots, changes(save), diff(save_a, save_b),
reserialize(save); actors: find_actor, move_actor, remove_actor, collect_dropped_items,
gravestones, actor_changes; players: get_player_position, set_player_position,
list_player_positions; read-only research: world_time, loot_chests.
Actor ids are "level/index"; moving or removing a record renumbers later actors of
that level, so ids returned by these calls should be used afterwards.
"""
import hashlib
import math
import os
import re
import struct

from .savefile import _parse as _parse_json, dumps as _dump_json, new_guid


class WorldSaveError(Exception):
    pass


# ----------------------------------------------------------------- low level

class Reader:
    """Little-endian reader over a bytes object."""

    def __init__(self, data, pos=0, end=None):
        self.d = data
        self.pos = pos
        self.end = len(data) if end is None else end

    def take(self, n):
        if n < 0 or self.pos + n > self.end:
            raise WorldSaveError("read past end at %d" % self.pos)
        b = self.d[self.pos:self.pos + n]
        self.pos += n
        return b

    def unpack(self, fmt):
        size = struct.calcsize(fmt)
        v = struct.unpack_from(fmt, self.d, self.pos)
        if self.pos + size > self.end:
            raise WorldSaveError("read past end at %d" % self.pos)
        self.pos += size
        return v

    def u8(self):
        return self.unpack("<B")[0]

    def u16(self):
        return self.unpack("<H")[0]

    def u32(self):
        return self.unpack("<I")[0]

    def i32(self):
        return self.unpack("<i")[0]

    def fstring(self):
        n = self.i32()
        if n == 0:
            return ""
        if n > 0:
            if n > self.end - self.pos:
                raise WorldSaveError("bad string length at %d" % self.pos)
            raw = self.take(n)
            if raw[-1:] != b"\0":
                raise WorldSaveError("string not NUL-terminated at %d" % self.pos)
            return raw[:-1].decode("latin-1")
        n = -n
        if 2 * n > self.end - self.pos:
            raise WorldSaveError("bad string length at %d" % self.pos)
        raw = self.take(2 * n)
        if raw[-2:] != b"\0\0":
            raise WorldSaveError("string not NUL-terminated at %d" % self.pos)
        return raw[:-2].decode("utf-16-le")

    def done(self):
        return self.pos >= self.end


def p_u8(v):
    return struct.pack("<B", v)


def p_u16(v):
    return struct.pack("<H", v)


def p_u32(v):
    return struct.pack("<I", v)


def p_i32(v):
    return struct.pack("<i", v)


def p_fstring(s):
    """Encode like Unreal: Latin-1 when every char is < 0x80, else UTF-16."""
    if s == "":
        return p_i32(0)
    if all(ord(c) < 0x80 for c in s):
        return p_i32(len(s) + 1) + s.encode("latin-1") + b"\0"
    raw = s.encode("utf-16-le")
    return p_i32(-(len(raw) // 2 + 1)) + raw + b"\0\0"


def p_fstring_list(items):
    return p_u32(len(items)) + b"".join(p_fstring(s) for s in items)


def read_fstring_list(data):
    r = Reader(data)
    out = [r.fstring() for _ in range(r.u32())]
    if not r.done():
        raise WorldSaveError("trailing bytes after string list")
    return out


# ----------------------------------------------------------------- chunk layer

_TAG = re.compile(rb"[A-Z]{4}\Z")
# Chunks whose payload is a list of sub-chunks with no header.
_PLAIN_CONTAINERS = {"SAVE", "GOBS", "LVLS", "LATS", "SATS", "DATS", "META", "CLST"}


def _head_len(tag, p):
    """Length of the non-chunk header in front of a container's sub-chunks, or None
    when the chunk is a leaf (its payload is kept as bytes)."""
    if tag in _PLAIN_CONTAINERS or tag == "GLAI":
        return 0
    r = Reader(p)
    if tag == "INFO":
        r.take(19)
        r.fstring()
    elif tag == "GLOB":
        r.fstring()
    elif tag == "LEVL":
        r.fstring()
        r.take(8)
    elif tag == "CDVE":
        if not p:
            return None
        r.take(1)
    elif tag == "NOBJ":
        r.u32()
        r.fstring()
        r.take(4 * r.u32())
        r.take(8)
    elif tag == "SPWN":
        r.take(4 + 16 + 48 + 1 + 8)
        r.take(4 * r.u32())
    else:
        return None
    return r.pos


def _chunk_chain(p, start, allow_tail):
    """Split p[start:] into (tag, payload) chunks. Returns (list, tail start) or None."""
    out = []
    pos = start
    while pos < len(p):
        if pos + 8 > len(p) or not _TAG.match(p[pos:pos + 4]):
            break
        n = struct.unpack_from("<I", p, pos + 4)[0]
        if pos + 8 + n > len(p):
            break
        out.append((p[pos:pos + 4].decode("ascii"), p[pos + 8:pos + 8 + n]))
        pos += 8 + n
    if pos != len(p) and not allow_tail:
        return None
    return out, pos


class Chunk:
    """One chunk. A container has head bytes + children (+ tail bytes, GLAI only);
    a leaf keeps its payload in .head and has children None. Sizes are not stored:
    to_bytes() recomputes them, so editing .head of a leaf is all an edit needs."""

    __slots__ = ("tag", "head", "children", "tail", "parent")

    def __init__(self, tag, head=b"", children=None, tail=b""):
        self.tag = tag
        self.head = head
        self.children = children
        self.tail = tail
        self.parent = None

    @classmethod
    def parse(cls, tag, payload):
        try:
            h = _head_len(tag, payload)
        except WorldSaveError:
            h = None
        if h is not None:
            chain = _chunk_chain(payload, h, allow_tail=(tag == "GLAI"))
            if chain is not None and (chain[0] or h == len(payload) == 0 or tag in _PLAIN_CONTAINERS):
                subs, tail_at = chain
                c = cls(tag, payload[:h], [], payload[tail_at:])
                for t, sub in subs:
                    ch = Chunk.parse(t, sub)
                    ch.parent = c
                    c.children.append(ch)
                return c
        return cls(tag, payload, None)

    def payload(self):
        if self.children is None:
            return self.head
        return self.head + b"".join(c.to_bytes() for c in self.children) + self.tail

    def to_bytes(self):
        p = self.payload()
        return self.tag.encode("ascii") + p_u32(len(p)) + p

    def find(self, tag):
        for c in self.children or ():
            if c.tag == tag:
                return c
        return None

    def find_all(self, tag):
        return [c for c in (self.children or ()) if c.tag == tag]

    def walk(self):
        yield self
        for c in self.children or ():
            yield from c.walk()


# ----------------------------------------------------------------- level metadata

class Meta:
    """Class and property-name tables of one level (or GLOB)."""

    def __init__(self, chunk):
        self.chunk = chunk
        v = chunk.find("VERS")
        self.version = struct.unpack("<I", v.head)[0] if v else None
        self.classes = read_fstring_list(chunk.find("CNIX").head)
        self.names = read_fstring_list(chunk.find("PNIX").head)
        self.defs = []          # per class index: None or (class path, [(name, outer, type)])
        for cdve in chunk.find("CLST").children:
            cdef = cdve.find("CDEF") if cdve.children is not None else None
            self.defs.append(_read_cdef(cdef.head) if cdef else None)

    def class_name(self, idx):
        if 0 <= idx < len(self.classes):
            return self.classes[idx]
        return "?%d" % idx

    def fields(self, idx):
        """[(display name, type code)] for a class index; [] when no definition."""
        if not (0 <= idx < len(self.defs)) or self.defs[idx] is None:
            return None
        out = []
        for ni, oi, ty in self.defs[idx][1]:
            name = self.names[ni]
            if oi >= 0:
                name = self.names[oi] + "." + name
            out.append((name, ty))
        return out


def _read_cdef(b):
    r = Reader(b)
    path = r.fstring()
    fields = [r.unpack("<IiH") for _ in range(r.u16())]
    if not r.done():
        raise WorldSaveError("trailing bytes in CDEF")
    return path, fields


def _write_cdef(path, fields):
    return p_fstring(path) + p_u16(len(fields)) + b"".join(struct.pack("<IiH", *f) for f in fields)


# ----------------------------------------------------------------- property blocks

def split_block(b):
    """PROP block -> list of value byte strings (raises if the layout is unusual)."""
    r = Reader(b)
    n = r.u32()
    if n > len(b):
        raise WorldSaveError("bad property count")
    offs = r.unpack("<%dI" % (n + 1))
    base = r.pos
    if offs[0] != 0 or any(offs[i] > offs[i + 1] for i in range(n)) or base + offs[-1] != len(b):
        raise WorldSaveError("bad property offsets")
    return [b[base + offs[i]:base + offs[i + 1]] for i in range(n)]


def join_block(values):
    offs = [0]
    for v in values:
        offs.append(offs[-1] + len(v))
    return p_u32(len(values)) + struct.pack("<%dI" % len(offs), *offs) + b"".join(values)


T_BOOL, T_ENUM, T_UINT32, T_INT32, T_INT64, T_FLOAT = 0x00, 0x01, 0x02, 0x06, 0x07, 0x08
T_VECTOR, T_ROTATOR, T_GUID, T_STR, T_NAME, T_TEXT = 0x14, 0x15, 0x17, 0x1E, 0x1F, 0x20
T_OBJECT, T_TAGGED, T_ARRAY = 0x28, 0x40, 0x1000
TYPE_NAMES = {T_BOOL: "bool", T_ENUM: "enum", T_UINT32: "uint32", T_INT32: "int32", T_INT64: "int64",
              T_FLOAT: "float", T_VECTOR: "vector", T_ROTATOR: "rotator", T_GUID: "guid", T_STR: "str",
              T_NAME: "name", T_TEXT: "text", T_OBJECT: "object", T_TAGGED: "tagged"}
_FIXED = {T_BOOL: "<B", T_ENUM: "<H", T_UINT32: "<I", T_INT32: "<i", T_INT64: "<q", T_FLOAT: "<f",
          T_VECTOR: "<3d", T_ROTATOR: "<3d"}


def type_name(ty):
    if ty & T_ARRAY:
        return "array<%s>" % type_name(ty & ~T_ARRAY)
    return TYPE_NAMES.get(ty, "0x%x" % ty)


class Raw(bytes):
    """Bytes this module keeps without interpreting."""


class Text:
    """FText. Only the empty / culture-invariant form is decoded; else raw."""

    def __init__(self, flags, string):
        self.flags = flags
        self.string = string

    def __repr__(self):
        return "Text(%r)" % self.string


class Node:
    """A decoded nested object (type 0x28): class index + its properties."""

    def __init__(self, cls_index, cls_name, props):
        self.cls_index = cls_index
        self.cls_name = cls_name
        self.props = props      # list of Prop

    def get(self, name):
        for p in self.props:
            if p.name == name:
                return p
        return None


class Prop:
    def __init__(self, name, ty, value):
        self.name = name
        self.ty = ty
        self.value = value

    def __repr__(self):
        return "Prop(%s:%s=%r)" % (self.name, type_name(self.ty), self.value)


def _read_text(r):
    flags = r.u32()
    history = r.unpack("<b")[0]
    if history != -1:
        raise WorldSaveError("FText history %d not decoded" % history)
    has = r.i32()
    if has not in (0, 1):
        raise WorldSaveError("bad FText")
    return Text(flags, r.fstring() if has else None)


def _write_text(t):
    out = p_u32(t.flags) + struct.pack("<b", -1)
    if t.string is None:
        return out + p_i32(0)
    return out + p_i32(1) + p_fstring(t.string)


def decode_value(ty, b, meta):
    """Value bytes -> Python value. Raw() for anything not understood."""
    try:
        r = Reader(b)
        v = _read_value(ty, r, meta, top=True)
        if not r.done():
            raise WorldSaveError("trailing bytes")
        return v
    except (WorldSaveError, struct.error, UnicodeDecodeError):
        return Raw(b)


def _read_value(ty, r, meta, top=False):
    if ty & T_ARRAY:
        inner = ty & ~T_ARRAY
        return [_read_value(inner, r, meta) for _ in range(r.u16())]
    if ty in _FIXED:
        v = r.unpack(_FIXED[ty])
        return list(v) if len(v) > 1 else v[0]
    if ty == T_GUID:
        return r.take(16).hex()
    if ty in (T_STR, T_NAME):
        return r.fstring()
    if ty == T_TEXT:
        return _read_text(r)
    if ty == T_OBJECT:
        ci = r.i32()
        if ci == -1:
            return None
        if not top:
            raise WorldSaveError("nested object inside array")
        fields = meta.fields(ci)
        if fields is None:
            raise WorldSaveError("no class definition")
        vals = split_block(r.take(r.end - r.pos))
        if len(vals) != len(fields):
            raise WorldSaveError("field count mismatch")
        return Node(ci, meta.class_name(ci),
                    [Prop(n, t, decode_value(t, v, meta)) for (n, t), v in zip(fields, vals)])
    if ty == T_TAGGED and top:
        return decode_tagged_blob(r.take(r.end - r.pos))
    raise WorldSaveError("unknown type 0x%x" % ty)


def encode_value(ty, v):
    if isinstance(v, Raw):
        return bytes(v)
    if ty & T_ARRAY:
        inner = ty & ~T_ARRAY
        return p_u16(len(v)) + b"".join(encode_value(inner, x) for x in v)
    if ty in _FIXED:
        return struct.pack(_FIXED[ty], *(v if isinstance(v, list) else [v]))
    if ty == T_GUID:
        return bytes.fromhex(v)
    if ty in (T_STR, T_NAME):
        return p_fstring(v)
    if ty == T_TEXT:
        return _write_text(v)
    if ty == T_OBJECT:
        if v is None:
            return p_i32(-1)
        return p_i32(v.cls_index) + join_block([encode_value(p.ty, p.value) for p in v.props])
    if ty == T_TAGGED:
        return encode_tagged_blob(v)
    raise WorldSaveError("cannot encode type 0x%x" % ty)


# ----------------------------------------------------------------- Unreal tagged properties
# UE 5.4+ property tags (complete type names), as written by Dominion for type 0x40:
#   FString name ("None" ends a struct), type name tree (FString name, i32 param count,
#   params...), i32 value size, u8 flags (1 array index, 2 property GUID,
#   4 extensions, 8 binary/native struct, 0x10 bool value true), [i32 index], [16-byte
#   guid], value. Containers inside values have no per-element sizes, so they are only
#   decoded when every element type is understood; otherwise the value stays raw.

_NATIVE_STRUCTS = {"Guid": 16, "Vector": 24, "Vector2D": 16, "Rotator": 24, "Quat": 32,
                   "DateTime": 8, "Timespan": 8, "LinearColor": 16, "Color": 4,
                   "IntPoint": 8, "IntVector": 12, "Vector4": 32}


class UTag:
    """One tagged property. value is decoded, or Raw when not understood."""

    def __init__(self, name, tname, flags, index, guid, value):
        self.name = name
        self.tname = tname        # (type name, [params])
        self.flags = flags
        self.index = index
        self.guid = guid
        self.value = value

    def __repr__(self):
        return "UTag(%s:%s=%r)" % (self.name, self.tname[0], self.value)


class UStruct(list):
    """A tagged property stream (list of UTag) ended by "None"."""


class UBlob:
    """Decoded 0x40 value. kind: 'struct' (items = UStruct), 'array' (items = list),
    'set' / 'map' (removed count is 0; items = list of keys or (key, value))."""

    def __init__(self, kind, items, ekind=None):
        self.kind = kind
        self.items = items
        self.ekind = ekind        # element codecs used for array/set/map

    def __repr__(self):
        return "UBlob(%s, %d items)" % (self.kind, len(self.items))


def _read_tname(r, depth=0):
    if depth > 8:
        raise WorldSaveError("type name too deep")
    name = r.fstring()
    n = r.i32()
    if not 0 <= n <= 4:
        raise WorldSaveError("bad type name")
    return (name, [_read_tname(r, depth + 1) for _ in range(n)])


def _write_tname(t):
    return p_fstring(t[0]) + p_i32(len(t[1])) + b"".join(_write_tname(x) for x in t[1])


def _read_ustruct(r, depth=0):
    if depth > 16:
        raise WorldSaveError("struct too deep")
    out = UStruct()
    while True:
        name = r.fstring()
        if name == "None":
            return out
        if not name or not name.isprintable():
            raise WorldSaveError("bad tag name")
        tname = _read_tname(r)
        size = r.i32()
        flags = r.u8()
        if flags & ~0x1B:
            raise WorldSaveError("tag flags 0x%x not supported" % flags)
        index = r.i32() if flags & 1 else 0
        guid = r.take(16) if flags & 2 else None
        if size < 0 or size > r.end - r.pos:
            raise WorldSaveError("bad tag size")
        raw = r.take(size)
        value = Raw(raw)
        try:
            sub = Reader(raw)
            v = _read_tvalue(tname, sub, flags, depth, top=True)
            if sub.done():
                value = v
        except (WorldSaveError, struct.error, UnicodeDecodeError):
            pass
        out.append(UTag(name, tname, flags, index, guid, value))


def _write_ustruct(s):
    out = []
    for t in s:
        v = bytes(t.value) if isinstance(t.value, Raw) else _write_tvalue(t.tname, t.value, t.flags, True)
        out.append(p_fstring(t.name) + _write_tname(t.tname) + p_i32(len(v)) + p_u8(t.flags))
        if t.flags & 1:
            out.append(p_i32(t.index))
        if t.flags & 2:
            out.append(t.guid)
        out.append(v)
    out.append(p_fstring("None"))
    return b"".join(out)


_SIMPLE_T = {"IntProperty": "<i", "Int64Property": "<q", "UInt32Property": "<I", "UInt64Property": "<Q",
             "FloatProperty": "<f", "DoubleProperty": "<d", "Int16Property": "<h", "UInt16Property": "<H",
             "Int8Property": "<b"}


def _struct_name(tname):
    return tname[1][0][0] if tname[1] else ""


def _read_tvalue(tname, r, flags, depth, top=False):
    t = tname[0]
    if t == "BoolProperty":
        if top:
            return bool(flags & 0x10)
        return bool(r.u8())
    if t in _SIMPLE_T:
        return r.unpack(_SIMPLE_T[t])[0]
    if t in ("StrProperty", "NameProperty", "EnumProperty"):
        return r.fstring()
    if t == "ByteProperty":
        return r.fstring() if tname[1] else r.u8()
    if t == "StructProperty":
        sn = _struct_name(tname)
        if (flags & 8) or (not top and sn in _NATIVE_STRUCTS):
            n = _NATIVE_STRUCTS.get(sn)
            if n is None:
                raise WorldSaveError("native struct %s" % sn)
            return Raw(r.take(n))
        return _read_ustruct(r, depth + 1)
    if t in ("ArrayProperty", "SetProperty") and top:
        if t == "SetProperty" and r.i32() != 0:
            raise WorldSaveError("set with removed keys")
        return [_read_tvalue(tname[1][0], r, 0, depth + 1) for _ in range(r.i32())]
    if t == "MapProperty" and top:
        if r.i32() != 0:
            raise WorldSaveError("map with removed keys")
        out = []
        for _ in range(r.i32()):
            k = _read_tvalue(tname[1][0], r, 0, depth + 1)
            out.append((k, _read_tvalue(tname[1][1], r, 0, depth + 1)))
        return out
    raise WorldSaveError("property type %s not decoded" % t)


def _write_tvalue(tname, v, flags, top=False):
    t = tname[0]
    if isinstance(v, Raw):
        return bytes(v)
    if t == "BoolProperty":
        return b"" if top else p_u8(1 if v else 0)
    if t in _SIMPLE_T:
        return struct.pack(_SIMPLE_T[t], v)
    if t in ("StrProperty", "NameProperty", "EnumProperty"):
        return p_fstring(v)
    if t == "ByteProperty":
        return p_fstring(v) if tname[1] else p_u8(v)
    if t == "StructProperty":
        return _write_ustruct(v)
    if t == "ArrayProperty":
        return p_i32(len(v)) + b"".join(_write_tvalue(tname[1][0], x, 0) for x in v)
    if t == "SetProperty":
        return p_i32(0) + p_i32(len(v)) + b"".join(_write_tvalue(tname[1][0], x, 0) for x in v)
    if t == "MapProperty":
        return p_i32(0) + p_i32(len(v)) + b"".join(
            _write_tvalue(tname[1][0], k, 0) + _write_tvalue(tname[1][1], x, 0) for k, x in v)
    raise WorldSaveError("cannot write %s" % t)


# Element codecs for the top level of a 0x40 value (its Unreal type is not stored).
def _el_read(kind, r):
    if kind == "struct":
        return _read_ustruct(r)
    if kind == "str":
        return r.fstring()
    if kind.startswith("raw"):
        return Raw(r.take(int(kind[3:])))
    raise WorldSaveError(kind)


def _el_write(kind, v):
    if kind == "struct":
        return _write_ustruct(v)
    if kind == "str":
        return p_fstring(v)
    return bytes(v)


_KEY_KINDS = ("struct", "str", "raw16", "raw4", "raw8")
# Fixed sizes come before "str" so 4 zero bytes read as a value, not an empty string.
_VAL_KINDS = ("struct", "raw4", "raw8", "raw16", "str", "raw1")


def decode_tagged_blob(b):
    """Best-effort decode of a 0x40 value; Raw(b) when no layout re-encodes exactly."""
    cands = []
    cands.append(("struct", None))
    cands.append(("array", "struct"))
    cands.append(("array", "str"))
    for k in _KEY_KINDS:
        cands.append(("set", k))
        for v in _VAL_KINDS:
            cands.append(("map", (k, v)))
    for kind, ek in cands:
        try:
            r = Reader(b)
            if kind == "struct":
                blob = UBlob(kind, _read_ustruct(r))
            elif kind == "array":
                n = r.i32()
                if not 0 <= n <= len(b):
                    continue
                blob = UBlob(kind, [_el_read(ek, r) for _ in range(n)], ek)
            else:
                if r.i32() != 0:
                    continue
                n = r.i32()
                if not 0 <= n <= len(b):
                    continue
                if kind == "set":
                    blob = UBlob(kind, [_el_read(ek, r) for _ in range(n)], ek)
                else:
                    blob = UBlob(kind, [(_el_read(ek[0], r), _el_read(ek[1], r)) for _ in range(n)], ek)
            if r.done() and encode_tagged_blob(blob) == b:
                return blob
        except (WorldSaveError, struct.error, UnicodeDecodeError):
            continue
    return Raw(b)


def encode_tagged_blob(v):
    if isinstance(v, Raw):
        return bytes(v)
    if v.kind == "struct":
        return _write_ustruct(v.items)
    if v.kind == "array":
        return p_i32(len(v.items)) + b"".join(_el_write(v.ekind, x) for x in v.items)
    if v.kind == "set":
        return p_i32(0) + p_i32(len(v.items)) + b"".join(_el_write(v.ekind, x) for x in v.items)
    return p_i32(0) + p_i32(len(v.items)) + b"".join(
        _el_write(v.ekind[0], k) + _el_write(v.ekind[1], x) for k, x in v.items)


# ----------------------------------------------------------------- INFO dictionary

# Value type of each known INFO key: 'u8', 'u32', 'i64', 'str'. Unknown keys stay raw.
INFO_TYPES = {
    "VERSION": "u32", "GUID_A": "u32", "GUID_B": "u32", "GUID_C": "u32", "GUID_D": "u32",
    "WorldName": "str", "WorldMapName": "str", "FriendlyFire": "u8", "SurvivalDifficulty": "u32",
    "HardcoreState": "u32", "Difficulty.Environment.FriendlyFire": "u32", "TimeOfSave": "i64",
    "SessionPrivacy": "u32", "SessionPasswd": "str", "CrossplayEnabled": "u32",
    "WorldOwnerId": "str", "WorldNameOwner": "str", "LastSavedBy": "str",
    "Meta_SaveFileRevision": "u32",
}
_INFO_FMT = {"u8": "<B", "u32": "<I", "i64": "<q"}


def read_dict(b):
    """CINF / generic key-value blob -> ordered list of [key, value bytes]."""
    r = Reader(b)
    keys = [r.fstring() for _ in range(r.u32())]
    n = r.u32()
    if n != len(keys):
        raise WorldSaveError("CINF key/value count mismatch")
    vals = split_block(b[r.pos - 4:])
    return [[k, v] for k, v in zip(keys, vals)]


def write_dict(entries):
    return p_fstring_list([k for k, _ in entries]) + join_block([v for _, v in entries])


def info_decode(key, v):
    t = INFO_TYPES.get(key)
    try:
        if t == "str":
            r = Reader(v)
            s = r.fstring()
            if r.done():
                return s
        elif t in _INFO_FMT and len(v) == struct.calcsize(_INFO_FMT[t]):
            return struct.unpack(_INFO_FMT[t], v)[0]
    except (WorldSaveError, UnicodeDecodeError):
        pass
    return Raw(v)


def info_encode(key, value):
    if isinstance(value, Raw):
        return bytes(value)
    t = INFO_TYPES[key]
    if t == "str":
        return p_fstring(value)
    return struct.pack(_INFO_FMT[t], value)


# ----------------------------------------------------------------- actors

_CELL = re.compile(r"RSACell(\d+)_X(-?\d+)_Y(-?\d+)$")


class Actor:
    """One saved actor (NOBJ placed in the level, or SPWN spawned at run time)."""

    def __init__(self, save, level, chunk, index):
        self.save = save
        self.level = level            # Level
        self.chunk = chunk
        self.index = index            # position within the level (stable id part)
        self.kind = "placed" if chunk.tag == "NOBJ" else "spawned"
        r = Reader(chunk.head)
        self.cls_index = r.u32()
        if chunk.tag == "NOBJ":
            self.name = r.fstring()
            self.guid = None
        else:
            self.guid = r.take(16).hex()
            self.name = None
            self.spawn_vectors = list(r.unpack("<6d"))
        self.cls_path = level.meta.class_name(self.cls_index)
        self.cls_name = self.cls_path.rsplit(".", 1)[-1]
        self._props = None

    @property
    def id(self):
        return "%s/%d" % (self.level.name, self.index)

    def transform(self):
        """{'rotation': [x,y,z,w], 'location': [x,y,z], 'scale': [x,y,z]} or None."""
        c = self.chunk.find("CORA")
        if c is None or len(c.head) != 159:
            return None
        size, has = struct.unpack_from("<IB", c.head, 0)
        if size != 155 or not has:
            return None
        v = struct.unpack_from("<10d", c.head, 7)
        return {"rotation": list(v[0:4]), "location": list(v[4:7]), "scale": list(v[7:10])}

    def location(self):
        t = self.transform()
        return t["location"] if t else None

    # --- properties -----------------------------------------------------
    def prop_chunk(self):
        return self.chunk.find("PROP")

    def props(self):
        """Decoded properties (list of Prop), cached. [] when the class has none."""
        if self._props is None:
            pc = self.prop_chunk()
            fields = self.level.meta.fields(self.cls_index)
            if pc is None or fields is None:
                self._props = []
            else:
                vals = split_block(pc.head)
                if len(vals) != len(fields):
                    raise WorldSaveError("%s: %d values for %d fields" % (self.id, len(vals), len(fields)))
                self._props = [Prop(n, t, decode_value(t, v, self.level.meta)) for (n, t), v in zip(fields, vals)]
        return self._props

    def commit(self):
        """Write the decoded properties back into the PROP chunk."""
        if self._props is not None:
            self.prop_chunk().head = join_block([encode_value(p.ty, p.value) for p in self._props])

    def set_prop_bytes(self, path, new):
        """Replace the raw bytes of one value. path = [index, index-in-nested-object, ...]."""
        pc = self.prop_chunk()
        pc.head = _replace_in_block(pc.head, list(path), new)
        self._props = None


def _replace_in_block(block, path, new):
    vals = split_block(block)
    i = path[0]
    if len(path) == 1:
        vals[i] = new
    else:
        v = vals[i]
        vals[i] = v[:4] + _replace_in_block(v[4:], path[1:], new)
    return join_block(vals)


def kind_guess(cls_name):
    n = cls_name.lower()
    rules = [
        ("dropped item", ("runtimespawnedworlditem",)),
        ("storage", ("basebuilding_chest", "basebuilding_crate", "storage")),
        ("loot chest", ("lootchest", "buriedchest", "spectralchest", "dr_buriedchest")),
        ("gravestone", ("gravestone", "graveyard")),
        ("crafting station", ("station_", "crafting_", "upgradeprop")),
        ("building", ("basebuilding", "_t1_", "_t2_", "_t3_", "building", "farm_")),
        ("resource node", ("orenode", "miningrock", "tree", "bush", "geyser", "fishingnode", "foliage",
                           "spawner_", "vine", "plant", "rock")),
        ("creature", ("_ai_", "bp_ai", "npc", "character")),
        ("trap", ("trap",)),
        ("door / mechanism", ("door", "lever", "switch", "mechanism", "puzzle")),
        ("manager", ("manager", "system", "actor_c", "director", "subsystem")),
    ]
    for kind, keys in rules:
        if any(k in n for k in keys):
            return kind
    return "other"


class Level:
    def __init__(self, save, chunk, index):
        self.save = save
        self.chunk = chunk
        self.index = index
        r = Reader(chunk.head)
        self.name = r.fstring()
        self.meta = Meta(chunk.find("META"))
        m = _CELL.match(self.name)
        self.cell = (int(m.group(2)), int(m.group(3))) if m else None
        self.cell_size = int(m.group(1)) if m else None
        self.actors = []
        for tag in ("LATS", "SATS"):
            box = chunk.find(tag)
            for c in (box.children if box is not None else ()):
                if c.tag in ("NOBJ", "SPWN") and c.children is not None:
                    self.actors.append(Actor(save, self, c, len(self.actors)))
        dats = chunk.find("DATS")
        self.destroyed = [Reader(k.head).fstring() for k in (dats.children if dats else ()) if k.tag == "KILL"]


# ----------------------------------------------------------------- containers (item stacks)

class JsonValue:
    """A JSON document stored in a FString property of an actor."""

    def __init__(self, actor, path, prop_name, text):
        self.actor = actor
        self.path = path              # value path for Actor.set_prop_bytes
        self.prop_name = prop_name
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.data = _parse_json(text)
        self.original_text = text

    @property
    def id(self):
        return self.actor.id + ":" + ".".join(str(i) for i in self.path)

    def render(self):
        return _dump_json(self.data, self.nl)

    def commit(self):
        """Write the JSON back into the actor's property block."""
        self.actor.set_prop_bytes(self.path, p_fstring(self.render()))


class Container(JsonValue):
    """A JsonInventory found on an actor (directly or inside a nested component).

    Crafting stations keep the same inventory up to three times: the component
    (e.g. FuelInventory), a mirror inside ProcessingStation (ProcessingStation.Fuel)
    and a copy without "Version" under a key of ProcessingStation.CustomSaveData.
    Those copies are found at load, checked to be identical, and every edit is
    written to all of them. If they disagree the container is read-only."""

    def __init__(self, actor, path, prop_name, text):
        JsonValue.__init__(self, actor, path, prop_name, text)
        self.mirrors = []             # Containers with the same full JSON
        self.embeds = []              # (JsonValue CustomSaveData, key)
        self.editable = True
        self.readonly_reason = None

    def _body(self):
        return {k: v for k, v in self.data.items() if k != "Version"}

    def link(self, mirrors, embeds):
        for m in mirrors:
            if m.original_text != self.original_text:
                self.editable, self.readonly_reason = False, "copy %s differs" % m.prop_name
        for cv, key in embeds:
            if _dump_json(cv.data[key], "\n") != _dump_json(self._body(), "\n"):
                self.editable, self.readonly_reason = False, "copy %s[%s] differs" % (cv.prop_name, key)
        self.mirrors, self.embeds = mirrors, embeds

    def slots(self):
        return {int(k): v for k, v in self.data.items() if k.isdigit()}

    def items(self):
        out = []
        for slot, e in sorted(self.slots().items()):
            it = {"slot": slot, "itemId": e.get("ItemData"), "guid": e.get("GUID")}
            if "Count" in e or "Durability" not in e:
                it["count"] = int(e.get("Count", 1))   # same rule as the character editor
            if "Durability" in e:
                it["durability"] = _num(e["Durability"])
            extra = {k: v for k, v in e.items() if k not in ("ItemData", "GUID", "Count", "Durability")}
            if extra:
                it["extra"] = extra
            out.append(it)
        return out

    def commit(self):
        """Write the JSON back, and the same contents into every copy."""
        if not self.editable:
            raise WorldSaveError("Container %s is read-only: %s" % (self.id, self.readonly_reason))
        JsonValue.commit(self)
        text = self.render()
        for m in self.mirrors:
            m.data = _parse_json(text)
            JsonValue.commit(m)
        for cv, key in self.embeds:
            cv.data[key] = _parse_json(_dump_json(self._body(), "\n"))
            cv.commit()

    def info(self, library=None):
        loc = self.actor.location()
        out = {"id": self.id, "actor": self.actor.id, "class": self.actor.cls_name,
               "kind": kind_guess(self.actor.cls_name), "cell": self.actor.level.name,
               "position": loc, "property": self.prop_name,
               "copies": [m.prop_name for m in self.mirrors] + ["%s[%s]" % (cv.prop_name, k) for cv, k in self.embeds],
               "editable": self.editable, "readonlyReason": self.readonly_reason,
               "maxSlotIndex": int(self.data["MaxSlotIndex"]) if "MaxSlotIndex" in self.data else None,
               "items": self.items()}
        if library:
            for it in out["items"]:
                lib = library.get(it["itemId"])
                it["name"] = lib["name"] if lib else None
        return out


def _num(v):
    f = float(v)
    return int(f) if f.is_integer() else f


def _json_strings(actor):
    """Yield (path, dotted prop name, text) for every JSON-looking string on an actor."""
    def walk(props, path, names):
        for i, p in enumerate(props):
            if isinstance(p.value, Node):
                yield from walk(p.value.props, path + [i], names + [p.name])
            elif p.ty == T_STR and isinstance(p.value, str) and p.value.startswith("{"):
                yield path + [i], ".".join(names + [p.name]), p.value
    yield from walk(actor.props(), [], [])


_PRIMARY = re.compile(r"(\w+)Inventory\.JsonInventory$")          # FuelInventory.JsonInventory
_MIRROR = re.compile(r"\w+\.(\w+)\.JsonInventory$")                 # ProcessingStation.Fuel.JsonInventory
_CUSTOM = re.compile(r"\w+\.CustomSaveData$")                      # ProcessingStation.CustomSaveData


def _actor_containers(actor):
    """Containers of one actor with their copies linked (copies are not returned)."""
    invs, customs = [], []
    for path, name, text in _json_strings(actor):
        try:
            if name.endswith("JsonInventory"):
                invs.append(Container(actor, path, name, text))
            elif _CUSTOM.match(name):
                customs.append(JsonValue(actor, path, name, text))
        except ValueError:
            pass  # not JSON after all
    roles = {}
    for c in invs:
        m = _PRIMARY.match(c.prop_name)
        if m:
            roles.setdefault(m.group(1), c)
    out = []
    mirrors_of = {}
    for c in invs:
        m = _MIRROR.match(c.prop_name)
        if m and m.group(1) in roles and roles[m.group(1)] is not c:
            mirrors_of.setdefault(id(roles[m.group(1)]), []).append(c)
        else:
            out.append(c)
    for c in out:
        m = _PRIMARY.match(c.prop_name) or _MIRROR.match(c.prop_name)
        role = m.group(1) if m else None
        embeds = [(cv, role) for cv in customs if role and isinstance(cv.data.get(role), dict)]
        c.link(mirrors_of.get(id(c), []), embeds)
    return out


# ----------------------------------------------------------------- the save

class WorldSave:
    def __init__(self, path=None, data=None):
        self.path = path
        if data is None:
            with open(path, "rb") as f:
                data = f.read()
        self.original = data
        self.loaded_hash = hashlib.sha256(data).hexdigest()
        if data[:4] != b"SAVE" or struct.unpack_from("<I", data, 4)[0] != len(data) - 8:
            raise WorldSaveError("Not a Dragonwilds world save (missing SAVE header).")
        self.root = Chunk.parse("SAVE", data[8:])
        self.roundtrip_ok = self.root.to_bytes() == data
        self._levels = None
        self._containers = None
        self._orig_json = {}          # (actor chunk, value path) -> JSON text at load
        self.actor_log = []           # moves / removals, for a review screen

    def _invalidate(self):
        """Forget derived views after records were added, moved or removed."""
        self._levels = None
        self._containers = None

    # --- structure ------------------------------------------------------
    def chunk(self, *tags):
        c = self.root
        for t in tags:
            c = c.find(t) if c is not None else None
        return c

    def levels(self):
        if self._levels is None:
            lv = self.chunk("LVLS")
            self._levels = [Level(self, c, i) for i, c in enumerate(lv.find_all("LEVL"))] if lv else []
        return self._levels

    def glob_meta(self):
        return Meta(self.chunk("GLOB", "META"))

    def glob_object(self):
        """(meta, NOBJ chunk) for DomPersistence (WorldSaveSettings)."""
        meta = self.glob_meta()
        for c in self.chunk("GLOB", "GOBS").find_all("NOBJ"):
            return meta, c
        return meta, None

    def glob_props(self):
        meta, c = self.glob_object()
        r = Reader(c.head)
        ci = r.u32()
        vals = split_block(c.find("PROP").head)
        fields = meta.fields(ci)
        return [Prop(n, t, decode_value(t, v, meta)) for (n, t), v in zip(fields, vals)], (meta, c, ci)

    def info_entries(self):
        return read_dict(self.chunk("INFO", "CINF").head)

    def actor_index(self):
        """GLAI table: {actor name: cell name}."""
        g = self.chunk("GLOB", "GLAI")
        cells = read_fstring_list(g.find("LVNI").head)
        r = Reader(g.tail)
        out = {}
        while not r.done():
            name = r.fstring()
            out[name] = cells[r.u32()]
        return out

    # --- writing ----------------------------------------------------------
    def to_bytes(self):
        return self.root.to_bytes()

    def changed_on_disk(self):
        with open(self.path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() != self.loaded_hash

    def write(self, path=None):
        """Write to path (default: the file it came from), refusing when unsafe."""
        if not self.roundtrip_ok:
            raise WorldSaveError("This world's format is not fully understood, so the editor will not write it.")
        target = path or self.path
        if path is None and self.changed_on_disk():
            raise WorldSaveError("The game changed this world after it was opened. Reload it first.")
        data = self.to_bytes()
        WorldSave(data=data)          # must parse again
        tmp = target + ".editor-tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, target)


def load(path):
    return WorldSave(path)


def save_bytes(save):
    return save.to_bytes()


# ----------------------------------------------------------------- forced re-serialization

def reserialize(save):
    """Rebuild every chunk this module understands from its decoded form (not from
    the raw spans) and return the file bytes. Used by the tests to prove the decoders
    are complete; the save object itself is not changed."""
    def leaf(c, meta, owner_cls):
        t, b = c.tag, c.head
        if t in ("CNIX", "PNIX", "LVNI"):
            return p_fstring_list(read_fstring_list(b))
        if t == "VERS":
            return p_u32(struct.unpack("<I", b)[0])
        if t == "CDEF":
            return _write_cdef(*_read_cdef(b))
        if t == "KILL":
            return p_fstring(Reader(b).fstring())
        if t == "CINF":
            ents = read_dict(b)
            return write_dict([[k, info_encode(k, info_decode(k, v)) if k in INFO_TYPES else v]
                               for k, v in ents])
        if t == "PROP" and meta is not None:
            fields = meta.fields(owner_cls)
            if fields is None:
                return b
            vals = split_block(b)
            return join_block([encode_value(ty, decode_value(ty, v, meta)) for (n, ty), v in zip(fields, vals)])
        return b

    def rebuild(c, meta=None, owner_cls=None):
        if c.children is None:
            p = leaf(c, meta, owner_cls)
        else:
            if c.tag in ("LEVL", "GLOB"):
                meta = Meta(c.find("META"))
            if c.tag in ("NOBJ", "SPWN"):
                owner_cls = struct.unpack_from("<I", c.head, 0)[0]
            p = c.head + b"".join(rebuild(x, meta, owner_cls) for x in c.children) + c.tail
        return c.tag.encode() + p_u32(len(p)) + p
    return rebuild(save.root)


# ----------------------------------------------------------------- semantic API

PRIVACY = {0: "Private", 1: "Friends only", 2: "Public"}   # labels are a guess; raw value is authoritative


def world_info(save):
    """World settings from INFO and WorldSaveSettings (GLOB)."""
    info = {k: info_decode(k, v) for k, v in save.info_entries()}
    glob, _ = save.glob_props()
    g = {p.name.split(".")[-1]: p.value for p in glob}
    custom = {}
    cds = g.get("CustomDifficultySettings")
    if isinstance(cds, UBlob) and cds.kind == "map":
        for k, v in cds.items:
            tag = k[0].value if isinstance(k, UStruct) and k else k
            custom[str(tag)] = bytes(v).hex() if isinstance(v, (bytes, Raw)) else v
    tos = info.get("TimeOfSave")
    out = {
        "name": info.get("WorldName"),
        "slotName": g.get("WorldSlotName"),
        "map": info.get("WorldMapName"),
        "friendlyFire": bool(info.get("FriendlyFire")) if "FriendlyFire" in info else g.get("bFriendlyFire"),
        "survivalDifficulty": info.get("SurvivalDifficulty"),
        "hardcoreState": info.get("HardcoreState"),
        "customDifficulty": custom,
        "privacy": info.get("SessionPrivacy"),
        "privacyLabel": PRIVACY.get(info.get("SessionPrivacy")),
        "passwordSet": bool(info.get("SessionPasswd")),
        "crossplay": bool(info.get("CrossplayEnabled")) if "CrossplayEnabled" in info else None,
        "owner": info.get("WorldNameOwner"),
        "ownerId": info.get("WorldOwnerId"),
        "lastSavedBy": info.get("LastSavedBy"),
        "timeOfSaveText": _info_time_text(save),
        "timeOfSaveTicks": tos,
        "saveFileRevision": info.get("Meta_SaveFileRevision"),
        "version": info.get("VERSION"),
        "worldGuid": g.get("WorldSaveGuid"),
    }
    return out


def _info_time_text(save):
    r = Reader(save.chunk("INFO").head)
    r.take(19)
    return r.fstring()


# key -> (INFO key, GLOB property name or None, python type)
_SETTINGS = {
    "name": ("WorldName", "WorldSaveSettings.WorldName", str),
    "friendlyFire": ("FriendlyFire", "WorldSaveSettings.bFriendlyFire", bool),
    "privacy": ("SessionPrivacy", None, int),
    "crossplay": ("CrossplayEnabled", None, bool),
}
EDITABLE_SETTINGS = tuple(_SETTINGS)


def set_world_setting(save, key, value):
    """Change one safe world setting everywhere the file stores it."""
    if key not in _SETTINGS:
        raise WorldSaveError("%s cannot be edited (editable: %s)" % (key, ", ".join(_SETTINGS)))
    ikey, gname, typ = _SETTINGS[key]
    if typ is str:
        value = str(value).strip()
        if not value or len(value) > 64:
            raise WorldSaveError("World name must be 1-64 characters.")
    elif typ is bool:
        value = bool(value)
    else:
        value = int(value)
        if value not in PRIVACY:
            raise WorldSaveError("privacy must be 0, 1 or 2")
    ents = save.info_entries()
    keys = [k for k, _ in ents]
    if ikey not in keys:
        raise WorldSaveError("%s is not in this world's INFO block" % ikey)
    if key == "friendlyFire" and "Difficulty.Environment.FriendlyFire" in keys:
        # Custom difficulty also stores friendly fire, in an encoding that is not
        # proven; refuse rather than leave the two copies disagreeing.
        raise WorldSaveError("This world uses custom difficulty for friendly fire; editing it is not supported.")
    # INFO
    for e in ents:
        if e[0] == ikey:
            old = info_decode(ikey, e[1])
            if isinstance(old, Raw):
                raise WorldSaveError("%s has an unexpected format" % ikey)
            e[1] = info_encode(ikey, int(value) if typ is bool else value)
    # GLOB copy
    if gname:
        props, (meta, nobj, ci) = save.glob_props()
        idx = [i for i, p in enumerate(props) if p.name == gname]
        if len(idx) != 1:
            raise WorldSaveError("%s not found in WorldSaveSettings" % gname)
        p = props[idx[0]]
        if isinstance(p.value, Raw):
            raise WorldSaveError("%s has an unexpected format" % gname)
        prop = nobj.find("PROP")
        vals = split_block(prop.head)
        vals[idx[0]] = encode_value(p.ty, int(value) if typ is bool else value)
        prop.head = join_block(vals)
    save.chunk("INFO", "CINF").head = write_dict(ents)


def list_cells(save):
    out = []
    for lv in save.levels():
        out.append({"name": lv.name, "cell": lv.cell, "cellSize": lv.cell_size,
                    "placed": sum(1 for a in lv.actors if a.kind == "placed"),
                    "spawned": sum(1 for a in lv.actors if a.kind == "spawned"),
                    "destroyed": len(lv.destroyed), "bytes": len(lv.chunk.payload())})
    return out


def list_actors(save):
    out = []
    for lv in save.levels():
        for a in lv.actors:
            d = {"id": a.id, "class": a.cls_name, "classPath": a.cls_path, "name": a.name,
                 "guid": a.guid, "kind": a.kind, "kindGuess": kind_guess(a.cls_name),
                 "cell": lv.name, "position": a.location()}
            it = dropped_item(a)
            if it:
                d["item"] = it
            out.append(d)
    return out


def dropped_item(actor):
    """Item stack lying on the ground (BP_RuntimeSpawnedWorldItem_C), read-only."""
    for p in actor.props():
        if isinstance(p.value, Node):
            item = p.value.get("SpawnedItem")
            if item is not None and isinstance(item.value, Node):
                f = {q.name: q.value for q in item.value.props}
                if "ItemDataPersistenceId" in f:
                    out = {"itemId": f["ItemDataPersistenceId"], "count": f.get("Count"), "guid": f.get("Guid")}
                    if isinstance(f.get("Durability"), int) and f["Durability"] >= 0:
                        out["durability"] = f["Durability"]
                    return out
    return None


def containers(save):
    """Container objects (cached so edits accumulate)."""
    if save._containers is None:
        save._containers = []
        orig = save._orig_json
        for lv in save.levels():
            for a in lv.actors:
                for c in _actor_containers(a):
                    # Keep the text seen at load across re-scans (after moves/removals),
                    # so changes() still shows every edit.
                    c.original_text = orig.setdefault((a.chunk, tuple(c.path)), c.original_text)
                    save._containers.append(c)
    return save._containers


def list_containers(save, library=None):
    """Every actor holding item stacks, as plain dicts. library = library['items']."""
    return [c.info(library) for c in containers(save)]


def get_container(save, cid, for_edit=False):
    c = None
    if isinstance(cid, Container):
        c = cid
    else:
        for x in containers(save):
            if x.id == cid:
                c = x
    if c is None:
        raise WorldSaveError("No container %s" % cid)
    if for_edit and not c.editable:
        raise WorldSaveError("Container %s is read-only: %s" % (c.id, c.readonly_reason))
    return c


def _slot_entry(c, slot):
    e = c.data.get(str(slot))
    if not isinstance(e, dict):
        raise WorldSaveError("Slot %s of %s is empty" % (slot, c.id))
    return e


def set_item_count(save, cid, slot, count):
    c = get_container(save, cid, for_edit=True)
    e = _slot_entry(c, slot)
    count = int(count)
    if count < 1:
        raise WorldSaveError("Count must be at least 1 (use remove_item to empty a slot).")
    if "Count" not in e and "Durability" in e:
        raise WorldSaveError("This item has durability, not a count.")
    e["Count"] = count
    c.commit()


def set_item_durability(save, cid, slot, durability):
    c = get_container(save, cid, for_edit=True)
    e = _slot_entry(c, slot)
    if "Durability" not in e:
        raise WorldSaveError("This item has no durability.")
    d = float(durability)
    e["Durability"] = int(d) if d.is_integer() else d
    c.commit()


def remove_item(save, cid, slot):
    c = get_container(save, cid, for_edit=True)
    _slot_entry(c, slot)
    del c.data[str(slot)]
    c.commit()


def add_item(save, cid, slot, item_id, count=None, durability=None):
    """Put a new stack in an empty slot. Give count (stackable) or durability (gear)."""
    c = get_container(save, cid, for_edit=True)
    slot = int(slot)
    if slot < 0 or str(slot) in c.data:
        raise WorldSaveError("Slot %s is not free" % slot)
    if not re.fullmatch(r"[A-Za-z0-9_-]{22}", item_id or ""):
        raise WorldSaveError("Item id must be a 22-character persistence id.")
    entry = {"GUID": new_guid(), "ItemData": item_id}
    if durability is not None:
        d = float(durability)
        entry["Durability"] = int(d) if d.is_integer() else d
    else:
        entry["Count"] = int(count if count is not None else 1)
    # Rebuild in the game's order: Version, slots ascending, then the other keys.
    slots = c.slots()
    slots[slot] = entry
    head = {k: v for k, v in c.data.items() if k == "Version"}
    rest = {k: v for k, v in c.data.items() if k != "Version" and not k.isdigit()}
    if "MaxSlotIndex" in rest and int(rest["MaxSlotIndex"]) < slot:
        rest["MaxSlotIndex"] = slot
    new = dict(head)
    for k in sorted(slots):
        new[str(k)] = slots[k]
    new.update(rest)
    c.data.clear()
    c.data.update(new)
    c.commit()


def leaf_map(save):
    """{path: bytes} for every leaf chunk, with PROP blocks split into named values
    (nested objects expanded). Two saves can be compared with diff()."""
    out = {}

    def props(prefix, block, meta, ci):
        fields = meta.fields(ci) if meta else None
        try:
            vals = split_block(block)
        except WorldSaveError:
            fields = None
        if fields is None or len(fields) != len(vals):
            out[prefix] = block
            return
        for (name, ty), v in zip(fields, vals):
            key = prefix + "/" + name
            if ty == T_OBJECT and len(v) > 4:
                out[key + "#class"] = v[:4]
                props(key, v[4:], meta, struct.unpack_from("<i", v, 0)[0])
            else:
                out[key] = v

    def walk(c, path, meta, ci):
        if c.children is None:
            if c.tag == "PROP" and meta is not None:
                props(path, c.head, meta, ci)
            else:
                out[path] = c.head
            return
        if c.tag in ("LEVL", "GLOB"):
            meta = Meta(c.find("META"))
        if c.tag in ("NOBJ", "SPWN"):
            ci = struct.unpack_from("<I", c.head, 0)[0]
        out[path + "#head"] = c.head
        out[path + "#tail"] = c.tail
        seen = {}
        for ch in c.children:
            n = seen[ch.tag] = seen.get(ch.tag, -1) + 1
            walk(ch, "%s/%s[%d]" % (path, ch.tag, n), meta, ci)
    walk(save.root, "SAVE", None, None)
    return out


def diff(a, b):
    """Paths whose bytes differ between two saves (WorldSave objects)."""
    ma, mb = leaf_map(a), leaf_map(b)
    return sorted(k for k in set(ma) | set(mb) if ma.get(k) != mb.get(k))


def changes(save):
    """Edited containers as [(container id, old json, new json)] for a review screen."""
    return [(c.id, c.original_text, c.render()) for c in containers(save) if c.render() != c.original_text]


def container_slots_used(save, cid):
    """Number of occupied slots of a container."""
    return len(get_container(save, cid).slots())


def container_free_slots(save, cid, capacity):
    """Free slot numbers below capacity (the real slot count of the chest class)."""
    c = get_container(save, cid)
    return [i for i in range(int(capacity)) if str(i) not in c.data]


# ----------------------------------------------------------------- actor edits: move / remove
# Facts these edits rely on (checked on 6 world saves written by the game):
#  * A spawned actor (SPWN) stores its position twice: CORA translation and the
#    6 doubles of the SPWN header, which are its bounding box (min xyz, max xyz;
#    689 of 690 boxes contain the location). Nothing else in the record repeats it.
#  * Gravestones, lodestones and beds are also listed in the GameplayObjectRegistry
#    (placed actor in L_World): entry.Identifier == actor.GameplayObjectRegistryIdentifier,
#    entry.Location == CORA location (always identical in every save looked at).
#  * Spawned actors live in the SATS of either their map cell (RSACell12800_X<i>_Y<j>,
#    i = floor(x / 12800)) or the persistent level L_World. Between two saves the game
#    itself moves records from L_World to a cell and back (dropped items, gravestones,
#    logs): CORA, header and decoded properties stay identical, only the class indices
#    are renumbered into the new level's class table, which the game extends at the end.
#  * GLAI lists placed actors only; KILL lists placed actors that were destroyed. A
#    spawned actor that is picked up / killed simply has no record in the next save.

CELL_SIZE = 12800
_SPWN_FIXED = 4 + 16 + 48 + 1 + 8        # class, guid, bounds, u8, 522, 1017
REGISTRY_LISTS = ("Gravestones", "Lodestones", "Beds")
_KNOWN_TYPES = set(TYPE_NAMES)


def cell_name(x, y, size=CELL_SIZE):
    return "RSACell%d_X%d_Y%d" % (size, math.floor(x / size), math.floor(y / size))


def _cell_of(loc, size):
    return (math.floor(loc[0] / size), math.floor(loc[1] / size))


def find_actor(save, actor_id):
    """Actor by id ("level/index", as list_actors gives it) or an Actor object."""
    if isinstance(actor_id, Actor):
        ch = actor_id.chunk
        if ch.parent is not None and any(c is ch for c in ch.parent.children):
            for lv in save.levels():
                for a in lv.actors:
                    if a.chunk is ch:
                        return a
        raise WorldSaveError("Actor %s is no longer in the save" % actor_id.id)
    name, _, idx = str(actor_id).rpartition("/")
    for lv in save.levels():
        if lv.name == name and idx.isdigit() and int(idx) < len(lv.actors):
            return lv.actors[int(idx)]
    raise WorldSaveError("No actor %s" % actor_id)


def persistent_level(save):
    name = Reader(save.chunk("GLOB").head).fstring()
    for lv in save.levels():
        if lv.name == name:
            return lv
    raise WorldSaveError("No persistent level %s" % name)


def _level_named(save, name):
    for lv in save.levels():
        if lv.name == name:
            return lv
    return None


def actor_changes(save):
    """Moves and removals done in this session, oldest first (for a review screen)."""
    return list(save.actor_log)


# --- GameplayObjectRegistry

def _registry(save):
    for lv in save.levels():
        for a in lv.actors:
            if a.kind == "placed" and a.cls_name == "GameplayObjectRegistry":
                return a
    return None


def registry_id(actor):
    """GameplayObjectRegistryIdentifier of an actor (hex), or None when absent / zero."""
    for p in actor.props():
        if p.name == "GameplayObjectRegistryIdentifier" and isinstance(p.value, str):
            return None if p.value == "0" * 32 else p.value
    return None


def _tagmap(st):
    return {t.name: t for t in st} if isinstance(st, UStruct) else {}


def _vec_get(tag):
    v = tag.value
    if isinstance(v, Raw) and len(v) == 24:
        return list(struct.unpack("<3d", v))
    d = _tagmap(v)
    if all(k in d and isinstance(d[k].value, float) for k in "XYZ"):
        return [d["X"].value, d["Y"].value, d["Z"].value]
    raise WorldSaveError("unexpected vector layout in registry")


def _vec_set(tag, xyz):
    if isinstance(tag.value, Raw):
        tag.value = Raw(struct.pack("<3d", *xyz))
    else:
        d = _tagmap(tag.value)
        for k, v in zip("XYZ", xyz):
            d[k].value = float(v)


def _guid_ue(raw16):
    """FGuid bytes -> the text form used by the character file (meta_data.char_guid)."""
    return "".join("%08X" % x for x in struct.unpack("<4I", bytes(raw16)))


def _entry_ident(e):
    t = _tagmap(e).get("Identifier")
    return bytes(t.value) if t is not None and isinstance(t.value, (bytes, Raw)) else None


def _registry_plan(save, ident):
    """Fresh decoded registry lists holding entries with this Identifier:
    (registry actor, [(prop index, list name, UBlob, [item indices])])."""
    reg = _registry(save)
    if reg is None or ident is None:
        return reg, []
    want = bytes.fromhex(ident)
    out = []
    vals = split_block(reg.prop_chunk().head)
    for i, (name, ty) in enumerate(reg.level.meta.fields(reg.cls_index)):
        if name not in REGISTRY_LISTS:
            continue
        blob = decode_value(ty, vals[i], reg.level.meta)       # fresh copy, safe to edit
        if not isinstance(blob, UBlob) or blob.kind != "array":
            raise WorldSaveError("registry list %s has an unexpected layout" % name)
        hits = [j for j, e in enumerate(blob.items) if _entry_ident(e) == want]
        if hits:
            out.append((i, name, blob, hits))
    return reg, out


def gravestones(save, library=None):
    """Player gravestones with their owner (from the registry) and contents.
    ownerCharGuid has the character file's form (meta_data.char_guid)."""
    reg = _registry(save)
    owners = {}
    if reg is not None:
        for p in reg.props():
            if p.name == "Gravestones" and isinstance(p.value, UBlob):
                for e in p.value.items:
                    ident = _entry_ident(e)
                    if ident is None:
                        continue
                    d = _tagmap(e)
                    og = _tagmap(d["OwningCharGuid"].value).get("InnerGuid") if "OwningCharGuid" in d else None
                    owners[ident.hex()] = {
                        "ownerCharGuid": _guid_ue(og.value) if og is not None and isinstance(og.value, (bytes, Raw)) else None,
                        "ownerAccount": d["OwningAccountGuidStr"].value if "OwningAccountGuidStr" in d else None,
                        "ownerName": d["OwningCharDisplayName"].value if "OwningCharDisplayName" in d else None,
                        "claimedBy": d["ClaimingCharName"].value if "ClaimingCharName" in d else None,
                        "registryLocation": _vec_get(d["Location"]) if "Location" in d else None}
    out = []
    for lv in save.levels():
        for a in lv.actors:
            if a.cls_name == "BP_PlayerGravestone_C":
                rid = registry_id(a)
                cs = [c for c in containers(save) if c.actor.chunk is a.chunk]
                g = {"id": a.id, "cell": lv.name, "position": a.location(), "registryId": rid,
                     "containers": [c.info(library) for c in cs]}
                g.update(owners.get(rid, {"ownerCharGuid": None}))
                out.append(g)
    return out


# --- moving records between levels

def _remap_value(ty, b, meta, cmap):
    """Value bytes with every class index renumbered through cmap (nested objects)."""
    if ty & T_ARRAY:
        if (ty & ~T_ARRAY) == T_OBJECT:
            raise WorldSaveError("array of objects")
        return b
    if ty not in _KNOWN_TYPES:
        raise WorldSaveError("unknown value type 0x%x" % ty)
    if ty != T_OBJECT:
        return b
    ci = struct.unpack_from("<i", b, 0)[0]
    if ci == -1:
        if len(b) != 4:
            raise WorldSaveError("bad empty object")
        return b
    fields = meta.fields(ci)
    vals = split_block(b[4:])
    if fields is None or len(fields) != len(vals):
        raise WorldSaveError("object without a matching class definition")
    return p_i32(cmap(ci)) + join_block([_remap_value(t, v, meta, cmap) for (_, t), v in zip(fields, vals)])


def _norm(props):
    """Comparable form of decoded properties (class paths instead of indices)."""
    out = []
    for p in props:
        v = p.value
        if isinstance(v, Raw):
            raise WorldSaveError("property %s is not decoded" % p.name)
        if isinstance(v, Node):
            out.append((p.name, p.ty, v.cls_name, _norm(v.props)))
        elif isinstance(v, UBlob):
            out.append((p.name, p.ty, encode_tagged_blob(v)))
        elif isinstance(v, Text):
            out.append((p.name, p.ty, v.flags, v.string))
        else:
            out.append((p.name, p.ty, v))
    return out


def _spwn_comps(head):
    n = struct.unpack_from("<I", head, _SPWN_FIXED)[0]
    if len(head) != _SPWN_FIXED + 4 + 4 * n:
        raise WorldSaveError("unexpected SPWN header")
    return list(struct.unpack_from("<%dI" % n, head, _SPWN_FIXED + 4))


def _transfer_spawned(save, a, dst):
    """Move a spawned record into another level's SATS, renumbering class indices the
    way the game does (missing classes / property names appended to the tables).
    Verified after the move; everything is restored if the check fails."""
    src = a.level
    if a.kind != "spawned":
        raise WorldSaveError("Only spawned actors can change level.")
    if a.chunk.find_all("CUST"):
        raise WorldSaveError("%s has custom binary data (CUST) that may hold level indices; "
                             "it cannot change cell." % a.cls_name)
    if any(c.tag not in ("CORA", "PROP") for c in a.chunk.children):
        raise WorldSaveError("unexpected chunk in %s" % a.id)
    sm, dmc = src.meta, dst.chunk.find("META")
    dm = Meta(dmc)
    if len(dm.classes) != len(dm.defs) or len(sm.classes) != len(sm.defs):
        raise WorldSaveError("class table and definitions differ in length")
    sats = dst.chunk.find("SATS")
    if sats is None:
        raise WorldSaveError("%s has no spawned-actor list" % dst.name)
    before = (a.cls_path, [sm.class_name(i) for i in _spwn_comps(a.chunk.head)], _norm(a.props()))

    classes, names = list(dm.classes), list(dm.names)
    cidx = {c: i for i, c in enumerate(classes)}
    nidx = {n: i for i, n in enumerate(names)}
    new_cdves, cache = [], {}

    def name_i(n):
        if n not in nidx:
            nidx[n] = len(names)
            names.append(n)
        return nidx[n]

    def cmap(i):
        if i in cache:
            return cache[i]
        path = sm.class_name(i)
        if path in cidx:
            j = cidx[path]
            if sm.fields(i) != dm.fields(j):
                raise WorldSaveError("class %s is defined differently in %s" % (path, dst.name))
        else:
            j = cidx[path] = len(classes)
            classes.append(path)
            d = sm.defs[i]
            if d is None:
                new_cdves.append(Chunk("CDVE", b"", None))
            else:
                fl = [(name_i(sm.names[ni]), name_i(sm.names[oi]) if oi >= 0 else -1, t) for ni, oi, t in d[1]]
                cdve = Chunk("CDVE", b"\0", [Chunk("CDEF", _write_cdef(d[0], fl), None)])
                cdve.children[0].parent = cdve
                new_cdves.append(cdve)
        cache[i] = j
        return j

    head = a.chunk.head
    comps = _spwn_comps(head)
    new_head = p_u32(cmap(a.cls_index)) + head[4:_SPWN_FIXED] + p_u32(len(comps)) + \
        b"".join(p_u32(cmap(c)) for c in comps)
    pc = a.prop_chunk()
    new_prop = None
    if pc is not None:
        fields = sm.fields(a.cls_index)
        vals = split_block(pc.head)
        if fields is None or len(fields) != len(vals):
            raise WorldSaveError("%s: properties do not match the class definition" % a.id)
        new_prop = join_block([_remap_value(t, v, sm, cmap) for (_, t), v in zip(fields, vals)])

    # apply (with undo information)
    cnix, pnix, clst = dmc.find("CNIX"), dmc.find("PNIX"), dmc.find("CLST")
    old_parent = a.chunk.parent
    undo = (cnix.head, pnix.head, list(clst.children), list(sats.children),
            list(old_parent.children), head, pc.head if pc else None)
    try:
        cnix.head = p_fstring_list(classes)
        pnix.head = p_fstring_list(names)
        for c in new_cdves:
            c.parent = clst
            clst.children.append(c)
        a.chunk.head = new_head
        if pc is not None:
            pc.head = new_prop
        old_parent.children = [c for c in old_parent.children if c is not a.chunk]
        sats.children.append(a.chunk)
        a.chunk.parent = sats
        # check: the record decodes in its new level exactly as before
        nl = Level(save, dst.chunk, dst.index)
        na = [x for x in nl.actors if x.chunk is a.chunk]
        if len(na) != 1:
            raise WorldSaveError("moved record not found")
        na = na[0]
        after = (na.cls_path, [nl.meta.class_name(i) for i in _spwn_comps(na.chunk.head)], _norm(na.props()))
        if after != before:
            raise WorldSaveError("record changed while moving it to %s" % dst.name)
    except Exception:
        cnix.head, pnix.head, clst.children, sats.children = undo[0], undo[1], undo[2], undo[3]
        old_parent.children = undo[4]
        a.chunk.parent = old_parent
        a.chunk.head = undo[5]
        if pc is not None:
            pc.head = undo[6]
        raise
    return {"addedClasses": classes[len(dm.classes):], "addedNames": names[len(dm.names):]}


def _yaw_of(q):
    return math.degrees(2 * math.atan2(q[2], q[3]))


def move_actor(save, actor_id, x, y, z=None, yaw=None, level=None):
    """Move an actor to (x, y, z) (z None = keep height), optionally setting its yaw
    (degrees; only for actors that are not tilted).

    Edits: CORA translation (+ rotation), the SPWN bounding box (shifted; widened when
    turned), the GameplayObjectRegistry entry (gravestones / lodestones / beds: Location,
    and RespawnLocation shifted by the same amount). A spawned actor that leaves its
    cell is moved into the persistent level L_World (always loaded, and where the game
    itself keeps spawned actors near the players), or into level= if given (L_World or
    the existing cell of the new position). Placed actors cannot leave their cell.
    Returns a report dict; the actor's id may change (report["id"])."""
    a = find_actor(save, actor_id)
    t = a.transform()
    if t is None:
        raise WorldSaveError("%s has no saved transform" % a.id)
    old = t["location"]
    new = [float(x), float(y), float(old[2] if z is None else z)]
    if not all(math.isfinite(v) and abs(v) < 2e7 for v in new):
        raise WorldSaveError("position out of range")
    rot = t["rotation"]
    new_rot, turn = rot, 0.0
    if yaw is not None:
        if abs(rot[0]) > 1e-6 or abs(rot[1]) > 1e-6:
            raise WorldSaveError("%s is tilted; only its position can be changed" % a.cls_name)
        r = math.radians(float(yaw))
        new_rot = [0.0, 0.0, math.sin(r / 2), math.cos(r / 2)]
        turn = r - math.radians(_yaw_of(rot))
    src = a.level
    target = src
    if a.kind == "placed":
        if level not in (None, src.name):
            raise WorldSaveError("Placed actors belong to their map cell and cannot change level.")
        if src.cell and _cell_of(new, src.cell_size) != src.cell:
            raise WorldSaveError("%s is part of the map in %s; the new position is in %s. Placed actors are "
                                 "matched to the map by name per cell, so they cannot leave their cell."
                                 % (a.cls_name, src.name, cell_name(new[0], new[1], src.cell_size)))
    else:
        home = persistent_level(save)
        if level is not None:
            target = _level_named(save, level)
            ok_cell = cell_name(new[0], new[1], src.cell_size or CELL_SIZE)
            if target is None or (target is not home and target.name != ok_cell):
                raise WorldSaveError("level must be %s or %s" % (home.name, ok_cell))
        elif src.cell is not None and _cell_of(new, src.cell_size) != src.cell:
            target = home
    dx, dy, dz = (n - o for n, o in zip(new, old))

    # registry entries (prepared on a fresh decode; nothing is changed yet)
    reg, plan = _registry_plan(save, registry_id(a))
    reg_vals = []
    for pi, name, blob, hits in plan:
        for j in hits:
            d = _tagmap(blob.items[j])
            if "Location" in d:
                _vec_set(d["Location"], new)
            if "RespawnLocation" in d:
                rl = _vec_get(d["RespawnLocation"])
                _vec_set(d["RespawnLocation"], [rl[0] + dx, rl[1] + dy, rl[2] + dz])
        reg_vals.append((pi, name, encode_value(T_TAGGED, blob)))

    report = {"from": old, "to": new, "class": a.cls_name, "oldId": a.id, "level": target.name,
              "edited": ["CORA location" + (" + rotation" if yaw is not None else "")]}
    if target is not src:
        report["transfer"] = _transfer_spawned(save, a, target)
        report["edited"].append("record moved %s -> %s" % (src.name, target.name))
    cora = a.chunk.find("CORA")
    h = cora.head
    cora.head = h[:7] + struct.pack("<10d", *(list(new_rot) + new + t["scale"])) + h[87:]
    if a.kind == "spawned":
        hb = a.chunk.head
        b = struct.unpack_from("<6d", hb, 20)
        lo, hi = [b[i] - old[i] for i in range(3)], [b[i + 3] - old[i] for i in range(3)]
        if turn:
            c, s = math.cos(turn), math.sin(turn)
            pts = [(px * c - py * s, px * s + py * c) for px in (lo[0], hi[0]) for py in (lo[1], hi[1])]
            lo[0], hi[0] = min(p[0] for p in pts), max(p[0] for p in pts)
            lo[1], hi[1] = min(p[1] for p in pts), max(p[1] for p in pts)
        nb = [new[i] + lo[i] for i in range(3)] + [new[i] + hi[i] for i in range(3)]
        a.chunk.head = hb[:20] + struct.pack("<6d", *nb) + hb[68:]
        report["edited"].append("SPWN bounds")
    for pi, name, v in reg_vals:
        reg.set_prop_bytes([pi], v)
        report["edited"].append("GameplayObjectRegistry." + name)
    save._invalidate()
    report["id"] = find_actor(save, a).id
    save.actor_log.append(dict(report, op="move"))
    return report


def _is_dropped(a):
    return a.cls_name.startswith("BP_RuntimeSpawnedWorldItem")


def remove_actor(save, actor_id, force=False):
    """Delete a spawned actor: its SPWN record and its GameplayObjectRegistry entry.
    Spawned actors have no other references (GLAI and KILL only hold placed actors;
    the game itself just leaves a picked-up item out of the next save). Allowed for
    dropped items and gravestones; other spawned classes need force=True."""
    a = find_actor(save, actor_id)
    if a.kind != "spawned":
        raise WorldSaveError("%s is part of the map (placed). The game records a destroyed map actor in "
                             "the level's KILL list; removing placed actors is not supported." % a.cls_name)
    if not force and not (_is_dropped(a) or a.cls_name == "BP_PlayerGravestone_C"):
        raise WorldSaveError("Removing %s is not supported (only dropped items and gravestones)." % a.cls_name)
    reg, plan = _registry_plan(save, registry_id(a))
    reg_vals = []
    for pi, name, blob, hits in plan:
        blob.items = [e for j, e in enumerate(blob.items) if j not in hits]
        reg_vals.append((pi, name, encode_value(T_TAGGED, blob)))
    report = {"op": "remove", "id": a.id, "class": a.cls_name, "position": a.location(),
              "item": dropped_item(a), "registry": [n for _, n, _ in reg_vals]}
    parent = a.chunk.parent
    parent.children = [c for c in parent.children if c is not a.chunk]
    a.chunk.parent = None
    for pi, _, v in reg_vals:
        reg.set_prop_bytes([pi], v)
    save._invalidate()
    save.actor_log.append(report)
    return report


def _spawned_item_fields(a):
    for p in a.props():
        if isinstance(p.value, Node):
            it = p.value.get("SpawnedItem")
            if it is not None and isinstance(it.value, Node):
                return {q.name: q.value for q in it.value.props}
    return {}


def collect_dropped_items(save, actor_ids, container_id, capacity, max_stack=None, allow_locked=False):
    """Move dropped items (BP_RuntimeSpawnedWorldItem_C) into a container and remove
    them from the ground. capacity = real slot count of the container. max_stack(item_id)
    -> int or None: when given, counts first top up existing plain stacks of the same
    item, then fill new slots up to that size. All or nothing: refuses before changing
    anything if an item cannot be represented or does not fit.
    Returns {"container": new container id, "placed": [...], "removed": [...]}."""
    c = get_container(save, container_id, for_edit=True)
    if c.data.get("AllowAdds") is False and not allow_locked:
        raise WorldSaveError("%s does not accept items (AllowAdds is false)." % c.id)
    if capacity is None:
        raise WorldSaveError("capacity (slot count of this container) is required")
    actors, seen = [], set()
    for aid in actor_ids:
        a = find_actor(save, aid)
        if id(a.chunk) in seen:
            continue
        seen.add(id(a.chunk))
        if not _is_dropped(a) or a.kind != "spawned":
            raise WorldSaveError("%s is not a dropped item" % a.id)
        f = _spawned_item_fields(a)
        item_id, count, dur = f.get("ItemDataPersistenceId"), f.get("Count"), f.get("Durability")
        if not isinstance(item_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{22}", item_id) or \
                not isinstance(count, int) or count < 1 or not isinstance(dur, int):
            raise WorldSaveError("%s: item fields not understood" % a.id)
        extras = []
        if f.get("EnchantmentGameplayEffects"):
            extras.append("enchantments")
        if f.get("CurrentVitalShield", 0.0) not in (0, 0.0):
            extras.append("shield")
        if f.get("NumMasterworkUpgradesApplied"):
            extras.append("masterwork upgrades")
        for k in ("GameplayTags.GameplayTags", "GameplayTags.ParentTags"):
            v = f.get(k)
            if not isinstance(v, UBlob) or v.items:
                extras.append("gameplay tags")
        if dur >= 0 and count != 1:
            extras.append("durability with count %d" % count)
        if extras:
            raise WorldSaveError("%s (%s) has %s, which a chest entry cannot hold yet."
                                 % (a.id, item_id, ", ".join(sorted(set(extras)))))
        actors.append((a, item_id, count, dur))

    # plan on a copy of the slots
    slots = {k: dict(v) for k, v in c.slots().items()}
    free = [i for i in range(int(capacity)) if i not in slots]
    placed = []
    for a, item_id, count, dur in actors:
        if dur >= 0:
            if not free:
                raise WorldSaveError("Not enough free slots in %s" % c.id)
            s = free.pop(0)
            slots[s] = {"GUID": new_guid(), "ItemData": item_id, "Durability": dur}
            placed.append({"actor": a.id, "itemId": item_id, "slot": s, "durability": dur})
            continue
        mx = max_stack(item_id) if max_stack else None
        mx = int(mx) if mx else None
        left = count
        if mx:
            for s in sorted(slots):
                e = slots[s]
                if left and set(e) == {"GUID", "ItemData", "Count"} and e["ItemData"] == item_id \
                        and int(e["Count"]) < mx:
                    add = min(mx - int(e["Count"]), left)
                    e["Count"] = int(e["Count"]) + add
                    left -= add
                    placed.append({"actor": a.id, "itemId": item_id, "slot": s, "count": add, "merged": True})
        while left:
            if not free:
                raise WorldSaveError("Not enough free slots in %s" % c.id)
            s = free.pop(0)
            n = min(left, mx) if mx else left
            slots[s] = {"GUID": new_guid(), "ItemData": item_id, "Count": n}
            left -= n
            placed.append({"actor": a.id, "itemId": item_id, "slot": s, "count": n})

    # apply: container JSON (game order: Version, slots ascending, other keys), then removals
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
    chest_chunk, path = c.actor.chunk, list(c.path)
    removed = [remove_actor(save, a)["id"] for a, _, _, _ in actors]
    cid = [x.id for x in containers(save) if x.actor.chunk is chest_chunk and x.path == path]
    return {"container": cid[0] if cid else None, "placed": placed, "removed": removed}


# ----------------------------------------------------------------- player position in the world
# L_World's SavedCharacterTransformsManager.SavedCharacterTransforms: one entry per
# character that played in this world: CharacterGuid.InnerGuid (FGuid; its text form is
# the character file's meta_data.char_guid), Transform {Rotation quat xyzw, Translation,
# Scale3D} (native structs, 4/3/3 doubles), LastUpdated (seconds into the session).
# Translation is the character's centre: in every pair seen it is the character file's
# LastAccessibleLocation Position with Z + 88.00 (X / Y equal to the file's 2 decimals).

PLAYER_Z_OFFSET = 88.0                # world Translation.Z - character file Position.Z


def _norm_guid(g):
    g = re.sub(r"[^0-9A-Fa-f]", "", str(g or "")).upper()
    if len(g) != 32:
        raise WorldSaveError("character GUID must be 32 hex digits")
    return g


def _char_transforms(save):
    """(manager actor, prop index, fresh UBlob) or (None, None, None)."""
    lv = persistent_level(save)
    for a in lv.actors:
        if a.kind == "placed" and a.cls_name == "SavedCharacterTransformsManager":
            vals = split_block(a.prop_chunk().head)
            for i, (name, ty) in enumerate(lv.meta.fields(a.cls_index)):
                if name == "SavedCharacterTransforms":
                    blob = decode_value(ty, vals[i], lv.meta)
                    if not isinstance(blob, UBlob) or blob.kind != "array":
                        raise WorldSaveError("SavedCharacterTransforms has an unexpected layout")
                    return a, i, blob
    return None, None, None


def _char_entry(blob, guid):
    for e in blob.items:
        d = _tagmap(e)
        cg = _tagmap(d["CharacterGuid"].value).get("InnerGuid") if "CharacterGuid" in d else None
        if cg is not None and isinstance(cg.value, (bytes, Raw)) and _guid_ue(cg.value) == guid:
            tr = _tagmap(d["Transform"].value) if "Transform" in d else {}
            if not all(isinstance(tr.get(k, UTag("", ("", []), 0, 0, None, None)).value, (bytes, Raw))
                       for k in ("Rotation", "Translation")):
                raise WorldSaveError("character transform has an unexpected layout")
            if len(tr["Rotation"].value) != 32 or len(tr["Translation"].value) != 24:
                raise WorldSaveError("character transform has an unexpected layout")
            return d, tr
    return None, None


def list_player_positions(save):
    """[{"charGuid", "position" [x,y,z], "yaw", "lastUpdated"}] for every stored character."""
    _, _, blob = _char_transforms(save)
    out = []
    for e in (blob.items if blob else ()):
        d = _tagmap(e)
        cg = _tagmap(d["CharacterGuid"].value).get("InnerGuid") if "CharacterGuid" in d else None
        if cg is None:
            continue
        g = _guid_ue(cg.value)
        _, tr = _char_entry(blob, g)
        q = struct.unpack("<4d", tr["Rotation"].value)
        out.append({"charGuid": g, "position": list(struct.unpack("<3d", tr["Translation"].value)),
                    "yaw": _yaw_of(q), "lastUpdated": d["LastUpdated"].value if "LastUpdated" in d else None})
    return out


def get_player_position(save, char_guid):
    """(x, y, z) of the character's saved transform in this world, or None."""
    _, _, blob = _char_transforms(save)
    if blob is None:
        return None
    _, tr = _char_entry(blob, _norm_guid(char_guid))
    return tuple(struct.unpack("<3d", tr["Translation"].value)) if tr else None


def set_player_position(save, char_guid, x, y, z, yaw=None):
    """Set the character's saved translation (and yaw, degrees) in this world. Only
    that value changes. z is the world value (character file Z + PLAYER_Z_OFFSET).
    Characters without an entry are refused (adding entries is not supported)."""
    guid = _norm_guid(char_guid)
    new = [float(x), float(y), float(z)]
    if not all(math.isfinite(v) and abs(v) < 2e7 for v in new):
        raise WorldSaveError("position out of range")
    actor, pi, blob = _char_transforms(save)
    if blob is None:
        raise WorldSaveError("This world has no saved character transforms.")
    d, tr = _char_entry(blob, guid)
    if tr is None:
        raise WorldSaveError("Character %s has no saved position in this world." % guid)
    old = list(struct.unpack("<3d", tr["Translation"].value))
    tr["Translation"].value = Raw(struct.pack("<3d", *new))
    if yaw is not None:
        q = struct.unpack("<4d", tr["Rotation"].value)
        if abs(q[0]) > 1e-6 or abs(q[1]) > 1e-6:
            raise WorldSaveError("character rotation is tilted; only the position can be changed")
        r = math.radians(float(yaw))
        tr["Rotation"].value = Raw(struct.pack("<4d", q[0], q[1], math.sin(r / 2), math.cos(r / 2)))
    actor.set_prop_bytes([pi], encode_value(T_TAGGED, blob))
    save.actor_log.append({"op": "player", "charGuid": guid, "from": old, "to": new, "yaw": yaw})
    return {"charGuid": guid, "from": old, "to": new}


# ----------------------------------------------------------------- read-only research views

TICKS_PER_SECOND = 10 ** 7            # Unreal FDateTime / FTimespan ticks
GAME_SPEED = 60                       # in-game seconds per StoredTime second (derived, see world_time)


def world_time(save):
    """World clock (read-only). BP_InGameTimeActor_C.StoredTime is real seconds the
    world has run (grew 2.8 s / 74 s between saves made 2.8 s / 74 s apart). Derived,
    matching both test worlds: in-game seconds = StoredTime * 60, and the weather
    DAY_COUNT = floor(StoredTime * 60 / 86400). Loot-chest respawn timestamps are
    in-game time in 100 ns ticks (StoredTime * 60 * 1e7)."""
    lv = persistent_level(save)
    stored, weather = None, []
    for a in lv.actors:
        if a.cls_name == "BP_InGameTimeActor_C":
            stored = next((p.value for p in a.props() if p.name == "StoredTime"), None)
        elif a.cls_name == "BP_WeatherActor_C":
            for p in a.props():
                if p.name == "WeathersJSONData" and isinstance(p.value, str):
                    try:
                        for d in _parse_json(p.value).get("Definitions", []):
                            wd = d.get("WeatherData", {})
                            weather.append({"region": d.get("WeatherName"), "type": wd.get("TYPE"),
                                            "day": int(wd["DAY_COUNT"]) if "DAY_COUNT" in wd else None,
                                            "remaining": _num(wd["REMAINING_TIME"]) if "REMAINING_TIME" in wd else None})
                    except ValueError:
                        pass
    out = {"storedTime": stored, "weather": weather}
    if isinstance(stored, float):
        game = stored * GAME_SPEED
        out.update({"gameSeconds": game, "day": int(game // 86400),
                    "timeOfDayHours": (game % 86400) / 3600.0, "gameTicks": int(game * TICKS_PER_SECOND)})
    return out


CHEST_STATES = {0: "untouched", 1: "opened", 2: "emptied"}


def loot_chests(save):
    """Loot / buried chests with their state (read-only). ChestState 0 = not opened
    since the last refill (respawn timer -1), 1 = opened (timer = in-game ticks when
    opened; stays -1 when bRespawnDisabled), 2 = opened and emptied (no slots, MaxSlotIndex -1). The game refills a
    chest later and resets it to 0 (seen between two saves); the delay is not known."""
    out = []
    by_chunk = {}
    for c in containers(save):
        by_chunk.setdefault(id(c.actor.chunk), []).append(c)
    for lv in save.levels():
        for a in lv.actors:
            pr = {p.name: p.value for p in a.props()}
            if "ChestState" not in pr:
                continue
            start = pr.get("RespawnTimerInGameTimeStartTicks")
            inv = by_chunk.get(id(a.chunk), [])
            out.append({"id": a.id, "class": a.cls_name, "name": a.name, "cell": lv.name,
                        "position": a.location(), "state": pr["ChestState"],
                        "stateLabel": CHEST_STATES.get(pr["ChestState"]),
                        "respawnTimerStartTicks": start,
                        "openedAtStoredTime": start / TICKS_PER_SECOND / GAME_SPEED
                        if isinstance(start, int) and start > 0 else None,
                        "respawnDisabled": bool(pr.get("bRespawnDisabled")),
                        "stacks": sum(len(c.slots()) for c in inv)})
    return out
