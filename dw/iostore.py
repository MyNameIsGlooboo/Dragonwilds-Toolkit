"""Minimal reader for Unreal Engine 5 IoStore containers (.utoc/.ucas).

Only supports what RuneScape: Dragonwilds ships: uncompressed, unencrypted,
single-partition containers with a directory index. It returns raw package
bytes; it does not parse assets.
"""
import os
import struct

MAGIC = b"-==--==--==--==-"
FLAG_COMPRESSED = 1
FLAG_ENCRYPTED = 2
FLAG_SIGNED = 4
FLAG_INDEXED = 8


class IoStoreError(Exception):
    pass


def _read_fstring(buf, pos):
    (length,) = struct.unpack_from("<i", buf, pos)
    pos += 4
    if length == 0:
        return "", pos
    if length < 0:
        n = -length * 2
        s = buf[pos:pos + n - 2].decode("utf-16-le")
        return s, pos + n
    s = buf[pos:pos + length - 1].decode("utf-8", "replace")
    return s, pos + length


class IoStoreReader:
    def __init__(self, utoc_path):
        self.utoc_path = utoc_path
        self.ucas_path = os.path.splitext(utoc_path)[0] + ".ucas"
        with open(utoc_path, "rb") as f:
            toc = f.read()
        if toc[:16] != MAGIC:
            raise IoStoreError("not an IoStore container: " + utoc_path)

        (self.version,) = struct.unpack_from("<B", toc, 16)
        (header_size, entry_count, block_count, block_entry_size,
         method_count, method_len, self.block_size, dir_index_size,
         partition_count) = struct.unpack_from("<9I", toc, 20)
        (self.flags,) = struct.unpack_from("<B", toc, 80)
        (seed_count,) = struct.unpack_from("<I", toc, 84)
        (no_hash_count,) = struct.unpack_from("<I", toc, 96)

        if self.flags & (FLAG_COMPRESSED | FLAG_ENCRYPTED):
            # Compressed/encrypted containers are not supported.
            if method_count or self.flags & FLAG_ENCRYPTED:
                raise IoStoreError("compressed or encrypted container not supported")
        if partition_count != 1:
            raise IoStoreError("multi-partition container not supported")
        if not self.flags & FLAG_INDEXED:
            raise IoStoreError("container has no directory index")

        pos = header_size
        pos += entry_count * 12  # chunk ids (not needed)
        self._offlen = toc[pos:pos + entry_count * 10]
        pos += entry_count * 10
        pos += seed_count * 4
        pos += no_hash_count * 4
        self._blocks = toc[pos:pos + block_count * block_entry_size]
        self._block_entry_size = block_entry_size
        pos += block_count * block_entry_size
        pos += method_count * method_len
        if self.flags & FLAG_SIGNED:
            (hash_size,) = struct.unpack_from("<i", toc, pos)
            pos += 4 + hash_size * 2 + 20 * block_count
        dir_index = toc[pos:pos + dir_index_size]
        self.files = self._parse_directory_index(dir_index)

    def _parse_directory_index(self, buf):
        mount, pos = _read_fstring(buf, 0)
        (n_dirs,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        dirs = [struct.unpack_from("<4I", buf, pos + i * 16) for i in range(n_dirs)]
        pos += n_dirs * 16
        (n_files,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        files = [struct.unpack_from("<3I", buf, pos + i * 12) for i in range(n_files)]
        pos += n_files * 12
        (n_str,) = struct.unpack_from("<i", buf, pos)
        pos += 4
        strings = []
        for _ in range(n_str):
            s, pos = _read_fstring(buf, pos)
            strings.append(s)

        mount = mount.replace("../../../", "")
        result = {}
        none = 0xFFFFFFFF
        # Each stack item is (directory entry, path of its parent directory).
        stack = [(0, mount)]
        while stack:
            d, parent = stack.pop()
            name, first_child, next_sibling, first_file = dirs[d]
            path = parent if name == none else parent + strings[name] + "/"
            f = first_file
            while f != none:
                fname, next_file, user_data = files[f]
                result[path + strings[fname]] = user_data
                f = next_file
            if next_sibling != none:
                stack.append((next_sibling, parent))
            if first_child != none:
                stack.append((first_child, path))
        return result

    def _chunk_range(self, index):
        e = self._offlen[index * 10:index * 10 + 10]
        offset = int.from_bytes(e[0:5], "big")
        length = int.from_bytes(e[5:10], "big")
        return offset, length

    def read(self, path):
        index = self.files[path]
        offset, length = self._chunk_range(index)
        bs = self.block_size
        first = offset // bs
        last = (offset + length - 1) // bs if length else first
        out = bytearray()
        with open(self.ucas_path, "rb") as f:
            for b in range(first, last + 1):
                e = self._blocks[b * self._block_entry_size:(b + 1) * self._block_entry_size]
                block_off = int.from_bytes(e[0:5], "little")
                comp_size = int.from_bytes(e[5:8], "little")
                f.seek(block_off)
                out += f.read(comp_size)
        start = offset - first * bs
        return bytes(out[start:start + length])


PAK_MAGIC = 0x5A6F12E1
PAK_ENTRY_HEADER = 53  # serialized FPakEntry before each uncompressed file (pak v11)


class PakReader:
    """Reader for an uncompressed, unencrypted version 11 .pak file."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(size - 512)
            tail = f.read()
            i = tail.rfind(struct.pack("<I", PAK_MAGIC))
            if i < 1:
                raise IoStoreError("not a pak file: " + path)
            encrypted_index = tail[i - 1]
            version, index_offset, index_size = struct.unpack_from("<iqq", tail, i + 4)
            if encrypted_index or version < 10:
                raise IoStoreError("unsupported pak version or encrypted index")
            methods = tail[i + 44:i + 44 + 160]
            if methods.strip(b"\0"):
                raise IoStoreError("compressed pak not supported")
            f.seek(index_offset)
            idx = f.read(index_size)

            mount, pos = _read_fstring(idx, 0)
            pos += 4 + 8  # entry count, path hash seed
            (has_hash,) = struct.unpack_from("<I", idx, pos)
            pos += 4
            if has_hash:
                pos += 8 + 8 + 20
            (has_full,) = struct.unpack_from("<I", idx, pos)
            pos += 4
            if not has_full:
                raise IoStoreError("pak has no full directory index")
            dir_offset, dir_size = struct.unpack_from("<qq", idx, pos)
            pos += 16 + 20
            (enc_size,) = struct.unpack_from("<i", idx, pos)
            pos += 4
            self._encoded = idx[pos:pos + enc_size]
            f.seek(dir_offset)
            dbuf = f.read(dir_size)

        mount = mount.replace("../../../", "")
        self.files = {}
        (ndirs,) = struct.unpack_from("<i", dbuf, 0)
        pos = 4
        for _ in range(ndirs):
            dname, pos = _read_fstring(dbuf, pos)
            (nfiles,) = struct.unpack_from("<i", dbuf, pos)
            pos += 4
            for _ in range(nfiles):
                fname, pos = _read_fstring(dbuf, pos)
                (entry,) = struct.unpack_from("<i", dbuf, pos)
                pos += 4
                full = (mount + dname.lstrip("/") + fname).replace("//", "/")
                self.files[full] = entry

    def _decode_entry(self, off):
        e = self._encoded
        (bits,) = struct.unpack_from("<I", e, off)
        off += 4
        if (bits & 0x3F) == 0x3F:
            off += 4
        if bits & (1 << 31):
            (offset,) = struct.unpack_from("<I", e, off)
            off += 4
        else:
            (offset,) = struct.unpack_from("<Q", e, off)
            off += 8
        if bits & (1 << 30):
            (usize,) = struct.unpack_from("<I", e, off)
        else:
            (usize,) = struct.unpack_from("<Q", e, off)
        if (bits >> 23) & 0x3F:
            raise IoStoreError("compressed pak entry not supported")
        return offset, usize

    def read(self, path):
        entry = self.files[path]
        if entry < 0:
            raise IoStoreError("non-encoded pak entries not supported")
        offset, size = self._decode_entry(entry)
        with open(self.path, "rb") as f:
            f.seek(offset)
            header = f.read(PAK_ENTRY_HEADER)
            if struct.unpack_from("<q", header, 8)[0] != size:
                raise IoStoreError("unexpected pak entry header in " + path)
            return f.read(size)


class GameFiles:
    """All containers in a Paks folder, merged into one path index."""

    def __init__(self, paks_dir):
        self.readers = []
        self.index = {}
        for name in sorted(os.listdir(paks_dir)):
            low = name.lower()
            r = None
            if low.endswith(".utoc") and low != "global.utoc":
                r = IoStoreReader(os.path.join(paks_dir, name))
            elif low.endswith(".pak"):
                try:
                    r = PakReader(os.path.join(paks_dir, name))
                except IoStoreError:
                    r = None
            if r:
                self.readers.append(r)
                for p in r.files:
                    self.index.setdefault(p, r)

    def __contains__(self, path):
        return path in self.index

    def read(self, path):
        return self.index[path].read(path)

    def find(self, predicate):
        return [p for p in self.index if predicate(p)]
