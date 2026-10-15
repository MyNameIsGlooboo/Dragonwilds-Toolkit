"""Read and write Dragonwilds character saves without changing their layout.

The game writes JSON with Unreal's pretty-print style (tabs, CRLF, braces on
their own line). This module parses numbers as their original text and writes
the same style back, so an unedited save round-trips byte for byte. Saving is
refused if that round trip ever fails.
"""
import base64
import hashlib
import json
import os


class RawNumber(str):
    """A JSON number kept as its original text."""


def _parse(text):
    return json.loads(text, parse_int=RawNumber, parse_float=RawNumber,
                      parse_constant=RawNumber)


def _esc(s):
    return json.dumps(s, ensure_ascii=False)


def _scalar(v):
    if isinstance(v, RawNumber):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    return _esc(v)


def _is_number(v):
    return isinstance(v, (RawNumber, int, float)) and not isinstance(v, bool)


def _write(v, depth, out, nl):
    tab = "\t"
    if isinstance(v, dict):
        out.append("{")
        first = True
        for k, val in v.items():
            out.append("" if first else ",")
            first = False
            out.append(nl + tab * (depth + 1) + _esc(k) + ":")
            if isinstance(val, dict):
                out.append(nl + tab * (depth + 1))
            else:
                out.append(" ")
            _write(val, depth + 1, out, nl)
        out.append(nl + tab * depth + "}")
    elif isinstance(v, list):
        if not v:
            out.append("[]")
        elif all(_is_number(x) for x in v):
            out.append("[ " + ", ".join(_scalar(x) for x in v) + " ]")
        else:
            out.append("[")
            for i, x in enumerate(v):
                if i:
                    out.append(",")
                out.append(nl + tab * (depth + 1))
                _write(x, depth + 1, out, nl)
            out.append(nl + tab * depth + "]")
    else:
        out.append(_scalar(v))


def dumps(data, nl="\r\n"):
    out = []
    _write(data, 0, out, nl)
    return "".join(out)


def new_guid():
    """A fresh item instance GUID in the save's base64url style (22 chars)."""
    return base64.urlsafe_b64encode(os.urandom(16)).decode().rstrip("=")


class SaveError(Exception):
    pass


class CharacterSave:
    def __init__(self, path):
        self.path = path
        self.reload()

    def reload(self):
        with open(self.path, "rb") as f:
            raw = f.read()
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        text = raw[3:].decode("utf-8") if self.bom else raw.decode("utf-8")
        self.nl = "\r\n" if "\r\n" in text else "\n"
        self.data = _parse(text)
        self.loaded_hash = hashlib.sha256(raw).hexdigest()
        self.roundtrip_ok = dumps(self.data, self.nl) == text
        self.original_text = text

    # --- state checks -------------------------------------------------
    def changed_on_disk(self):
        with open(self.path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest() != self.loaded_hash

    def render(self):
        return dumps(self.data, self.nl)

    def diff_lines(self):
        """Changed lines as (old, new) pairs, for the confirm dialog."""
        import difflib
        old = self.original_text.split(self.nl)
        new = self.render().split(self.nl)
        out = []
        for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if tag != "equal":
                out.append(("\n".join(x.strip() for x in old[i1:i2]),
                            "\n".join(x.strip() for x in new[j1:j2])))
        return out

    def write(self):
        if not self.roundtrip_ok:
            raise SaveError("This save's format is not fully understood, so the editor will not write it.")
        if self.changed_on_disk():
            raise SaveError("The game changed this save after it was opened. Reload it first.")
        text = self.render()
        _parse(text)  # must still be valid JSON
        data = text.encode("utf-8")
        if self.bom:
            data = b"\xef\xbb\xbf" + data
        tmp = self.path + ".editor-tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, self.path)
        self.reload()

    # --- convenient views ----------------------------------------------
    @property
    def progress(self):
        return self.data["GameProgress"]

    @property
    def name(self):
        return self.data.get("meta_data", {}).get("char_name", os.path.basename(self.path))

    def container(self, key):
        """'Inventory', 'Loadout' or 'PersonalInventory' as {slot:int -> entry}."""
        c = self.progress.get(key, {})
        return {int(k): v for k, v in c.items() if k != "MaxSlotIndex"}

    def set_entry(self, key, slot, entry):
        c = self.progress.setdefault(key, {"MaxSlotIndex": RawNumber("-1")})
        items = {int(k): v for k, v in c.items() if k != "MaxSlotIndex"}
        if entry is None:
            items.pop(slot, None)
        else:
            items[slot] = entry
        new = {str(k): items[k] for k in sorted(items)}
        new["MaxSlotIndex"] = int(max(items)) if items else -1
        old_max = c.get("MaxSlotIndex")
        # Keep the game's own MaxSlotIndex when it is still large enough.
        if old_max is not None and items and int(old_max) >= max(items):
            new["MaxSlotIndex"] = old_max
        c.clear()
        c.update(new)

    def hotbar_refs(self):
        """{loadout slot: inventory slot} for hotbar shortcuts."""
        return {s: int(e["PlayerInventoryItemIndex"]) for s, e in self.container("Loadout").items()
                if "PlayerInventoryItemIndex" in e}
