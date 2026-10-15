"""Live cheats: install UE4SS + the editor's script mod, and talk to it while the game runs.

The script mod (dw/cheatmod) turns on the game's own developer cheat menu and runs
commands this module appends to commands.txt in the mod folder. Nothing here touches
save files. Install and uninstall need the game closed (the DLL is in use otherwise).
"""
import hashlib
import json
import os
import shutil
import time
import urllib.request
import zipfile

from . import core
from .version import USER_AGENT

UE4SS_FILE = "UE4SS_v3.0.1-1176-g1ba38db4.zip"
UE4SS_URL = "https://github.com/UE4SS-RE/RE-UE4SS/releases/download/experimental-latest/" + UE4SS_FILE
UE4SS_SIZE = 8741395
UE4SS_SHA256 = "E615EE7A2C24344EA5AEEF85183D6CFD550F4F6352225976679F9FEFFFD0B9A5"
MOD_NAME = "DWEditorCheats"
MANIFEST = os.path.join(core.APP_DIR, "cheats-install.json")
MOD_SOURCE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cheatmod")
ENABLED_MODS = {MOD_NAME: 1, "ConsoleEnablerMod": 1, "Keybinds": 1}
STOCK_MODS = ["CheatManagerEnablerMod", "ConsoleCommandsMod", "ConsoleEnablerMod", "SplitScreenMod",
              "LineTraceMod", "BPML_GenericFunctions", "BPModLoaderMod", "Keybinds"]


class CheatError(Exception):
    pass


def win64_dir(game_dir):
    if not game_dir:
        return None
    for p in (os.path.join(game_dir, "RSDragonwilds", "Binaries", "Win64"), os.path.join(game_dir, "Binaries", "Win64")):
        if os.path.isfile(os.path.join(p, "RSDragonwilds-Win64-Shipping.exe")):
            return p
    return None


def mod_dir(win64):
    return os.path.join(win64, "ue4ss", "Mods", MOD_NAME)


def _sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest().upper()


def ue4ss_zip(log=print):
    """Path to the verified UE4SS zip, downloading it once into the editor's folder."""
    folder = os.path.join(core.APP_DIR, "ue4ss")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, UE4SS_FILE)
    if os.path.isfile(path) and os.path.getsize(path) == UE4SS_SIZE and _sha256(path) == UE4SS_SHA256:
        return path
    bundled = os.path.join(core.VENDOR_DIR, UE4SS_FILE)
    if os.path.isfile(bundled) and os.path.getsize(bundled) == UE4SS_SIZE:
        return bundled
    log("Downloading UE4SS from GitHub…")
    tmp = path + ".part"
    req = urllib.request.Request(UE4SS_URL, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
    except OSError as e:
        raise CheatError("Couldn't download UE4SS (the cheat loader). Check your internet connection and try again. "
                         f"Details: {e}")
    if os.path.getsize(tmp) != UE4SS_SIZE or _sha256(tmp) != UE4SS_SHA256:
        os.remove(tmp)
        raise CheatError("The UE4SS download didn't match the expected file, so it was not used.")
    os.replace(tmp, path)
    return path


def bundled_mod_version():
    """MOD_VERSION of the cheat script this toolkit ships."""
    import re
    try:
        with open(os.path.join(MOD_SOURCE, "Scripts", "main.lua"), encoding="utf-8") as f:
            m = re.search(r"^local MOD_VERSION = (\d+)", f.read(), re.M)
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


def _load_manifest():
    try:
        with open(MANIFEST, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def status(game_dir):
    w = win64_dir(game_dir) if game_dir else None
    out = {"gameFound": bool(w), "installed": False, "ours": False, "modAlive": False,
           "player": False, "cheatMenu": False, "functions": 0, "win64": w}
    if not w:
        return out
    m = _load_manifest()
    out["installed"] = os.path.isfile(os.path.join(w, "dwmapi.dll")) and os.path.isdir(os.path.join(w, "ue4ss"))
    out["ours"] = bool(m and os.path.normcase(m.get("win64", "")) == os.path.normcase(w))
    out["modPresent"] = os.path.isfile(os.path.join(mod_dir(w), "Scripts", "main.lua"))
    st = os.path.join(mod_dir(w), "status.txt")
    if os.path.isfile(st):
        try:
            parts = open(st, encoding="utf-8").read().strip().split("\t")
            age = time.time() - float(parts[0])
            out["modAlive"] = age < 8
            out["player"] = out["modAlive"] and parts[1] == "true"
            out["cheatMenu"] = out["modAlive"] and parts[2] == "true"
            # scripts before version 3 didn't report a version
            running = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
            out["modVersion"] = running
            out["modOutdated"] = out["modAlive"] and running < bundled_mod_version()
        except (OSError, ValueError, IndexError):
            pass
    out["functions"] = len(functions(game_dir))
    return out


def install(game_dir, log=print):
    if core.game_running():
        raise CheatError("Quit the game first; its files are in use while it runs.")
    w = win64_dir(game_dir)
    if not w:
        raise CheatError("Couldn't find the game's Binaries\\Win64 folder. Check the game folder in Game library.")
    m = _load_manifest()
    ours = m and os.path.normcase(m.get("win64", "")) == os.path.normcase(w)
    if not ours and (os.path.exists(os.path.join(w, "dwmapi.dll")) or os.path.exists(os.path.join(w, "ue4ss"))):
        raise CheatError("Another dwmapi.dll or UE4SS is already in the game folder. Remove it first, "
                         "so the editor doesn't overwrite someone else's mod setup.")
    src = ue4ss_zip(log)
    log("Copying UE4SS into the game folder…")
    with zipfile.ZipFile(src) as z:
        for name in z.namelist():
            if name.endswith("/"):
                continue
            target = os.path.normpath(os.path.join(w, name))
            if not target.startswith(os.path.normpath(w) + os.sep):
                continue  # never write outside the game folder
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(name) as fsrc, open(target, "wb") as fdst:
                shutil.copyfileobj(fsrc, fdst)
    # our mod
    dest = mod_dir(w)
    os.makedirs(os.path.join(dest, "Scripts"), exist_ok=True)
    shutil.copy2(os.path.join(MOD_SOURCE, "Scripts", "main.lua"), os.path.join(dest, "Scripts", "main.lua"))
    for f in ("commands.txt", "results.txt"):
        open(os.path.join(dest, f), "w").close()
    # only our mod, the console and keybinds run
    mods = {name: ENABLED_MODS.get(name, 0) for name in STOCK_MODS + [MOD_NAME]}
    with open(os.path.join(w, "ue4ss", "Mods", "mods.txt"), "w", encoding="utf-8") as f:
        for name, on in mods.items():
            if name != "Keybinds":
                f.write(f"{name} : {on}\n")
        f.write("\n; Built-in keybinds, do not move up!\nKeybinds : 1\n")
    with open(os.path.join(w, "ue4ss", "Mods", "mods.json"), "w", encoding="utf-8") as f:
        json.dump([{"mod_name": n, "mod_enabled": bool(on)} for n, on in mods.items()], f, indent=4)
    # Hot reload stays off: reloading Lua mods with running loops can crash the game.
    os.makedirs(core.APP_DIR, exist_ok=True)
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump({"win64": w, "dll_sha256": _sha256(os.path.join(w, "dwmapi.dll")), "installed": time.time()}, f)
    log("Installed. Start the game and load a world; the Cheats tab connects by itself.")
    return status(game_dir)


def update_mod(game_dir):
    """Copy this toolkit's main.lua over an older installed one. The game reads it when it
    starts, so a copy made while it runs takes effect next time. Returns True when copied."""
    w = win64_dir(game_dir)
    if not w or not os.path.isdir(mod_dir(w)):
        return False
    src = os.path.join(MOD_SOURCE, "Scripts", "main.lua")
    dst = os.path.join(mod_dir(w), "Scripts", "main.lua")
    try:
        with open(src, "rb") as a, open(dst, "rb") as b:
            if a.read() == b.read():
                return False
    except OSError:
        pass
    try:
        shutil.copy2(src, dst)
        return True
    except OSError:
        return False


def uninstall(game_dir):
    if core.game_running():
        raise CheatError("Quit the game first; its files are in use while it runs.")
    w = win64_dir(game_dir)
    m = _load_manifest()
    if not w or not m or os.path.normcase(m.get("win64", "")) != os.path.normcase(w):
        raise CheatError("The cheats weren't installed by this editor in this game folder, so nothing was removed.")
    dll = os.path.join(w, "dwmapi.dll")
    if os.path.isfile(dll) and _sha256(dll) == m.get("dll_sha256"):
        os.remove(dll)
    shutil.rmtree(os.path.join(w, "ue4ss"), ignore_errors=True)
    os.remove(MANIFEST)
    return status(game_dir)


# Switch-type cheats the mod keeps applied (key -> number). See main.lua ApplyWanted.
WANTED_KEYS = {"god", "stamina", "durability", "nohunger", "carry", "xp", "damage", "falldamage", "walk", "jump", "fly", "hideoverlay"}


def read_wanted(game_dir):
    w = win64_dir(game_dir)
    path = os.path.join(mod_dir(w), "wanted.txt") if w else None
    out = {}
    if path and os.path.isfile(path):
        for line in open(path, encoding="utf-8").read().splitlines():
            k, _, v = line.partition("=")
            if k.strip() in WANTED_KEYS and v.strip():
                try:
                    out[k.strip()] = float(v)
                except ValueError:
                    pass
    return out


def set_wanted(game_dir, key, value):
    """Turn a switch cheat on (value) or off (None)."""
    if key not in WANTED_KEYS:
        raise CheatError("Unknown cheat.")
    w = win64_dir(game_dir)
    if not w or not os.path.isdir(mod_dir(w)):
        raise CheatError("Cheats aren't installed.")
    cur = read_wanted(game_dir)
    if value is None:
        cur.pop(key, None)
    else:
        cur[key] = float(value)
    with open(os.path.join(mod_dir(w), "wanted.txt"), "w", encoding="utf-8") as f:
        for k, v in sorted(cur.items()):
            f.write(f"{k}={v:g}\n")
    return cur


def set_me(game_dir, char_guid):
    """Tell the mod which character is ours (for "my latest gravestone")."""
    w = win64_dir(game_dir) if game_dir else None
    if w and os.path.isdir(mod_dir(w)) and char_guid:
        with open(os.path.join(mod_dir(w), "me.txt"), "w", encoding="utf-8") as f:
            f.write(str(char_guid).upper() + "\n")


def get_home(game_dir):
    """Home position (x, y, z) the mod teleports to, or None."""
    w = win64_dir(game_dir)
    path = os.path.join(mod_dir(w), "home.txt") if w else None
    if path and os.path.isfile(path):
        try:
            x, y, z = (float(v) for v in open(path, encoding="utf-8").read().strip().split(","))
            return [x, y, z]
        except ValueError:
            return None
    return None


def set_home(game_dir, x, y, z):
    w = win64_dir(game_dir)
    if not w or not os.path.isdir(mod_dir(w)):
        raise CheatError("Cheats aren't installed.")
    with open(os.path.join(mod_dir(w), "home.txt"), "w", encoding="utf-8") as f:
        f.write(f"{float(x):.1f},{float(y):.1f},{float(z):.1f}\n")
    return [float(x), float(y), float(z)]


# ------------------------------------------------------------- talking to the mod
def send(game_dir, fn, args=()):
    w = win64_dir(game_dir)
    if not w or not os.path.isdir(mod_dir(w)):
        raise CheatError("Cheats aren't installed.")
    clean = [str(a).replace("\t", " ").replace("\n", " ") for a in args]
    if not fn.replace("_", "").replace(".", "").isalnum():
        raise CheatError("Bad command name.")
    seq = int(time.time() * 1000)
    with open(os.path.join(mod_dir(w), "commands.txt"), "a", encoding="utf-8") as f:
        f.write("\t".join([str(seq), fn] + clean) + "\n")
    return seq


def results(game_dir, since=0):
    w = win64_dir(game_dir)
    path = os.path.join(mod_dir(w), "results.txt") if w else None
    out = []
    if path and os.path.isfile(path):
        for line in open(path, encoding="utf-8", errors="replace").read().splitlines()[-200:]:
            parts = line.split("\t", 2)
            if len(parts) == 3 and parts[0].isdigit() and int(parts[0]) > since:
                out.append({"seq": int(parts[0]), "ok": parts[1] == "ok", "message": parts[2]})
    return out


def functions(game_dir):
    w = win64_dir(game_dir) if game_dir else None
    path = os.path.join(mod_dir(w), "functions.txt") if w else None
    out = []
    if path and os.path.isfile(path):
        for line in open(path, encoding="utf-8", errors="replace").read().splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[0]:
                params = []
                if len(parts) > 2 and parts[2]:
                    for p in parts[2].split(","):
                        n, _, t = p.partition(":")
                        params.append({"name": n, "type": t})
                out.append({"name": parts[0], "cls": parts[1], "params": params})
    return out


def reset_values(game_dir):
    """Forget every value the mod saved (normal values and switches) so it learns them again.
    Fixes a wrong "normal" speed stuck from an older version. Game must be closed."""
    if core.game_running():
        raise CheatError("Quit the game first; the mod would save the current values again.")
    w = win64_dir(game_dir)
    if not w or not os.path.isdir(mod_dir(w)):
        raise CheatError("Cheats aren't installed.")
    keep_hidden = read_wanted(game_dir).get("hideoverlay")
    removed = []
    for name in ("defaults.txt", "pdefaults.txt", "wanted.txt", "pwanted.txt"):
        path = os.path.join(mod_dir(w), name)
        if os.path.isfile(path):
            os.remove(path)
            removed.append(name)
    if keep_hidden:
        with open(os.path.join(mod_dir(w), "wanted.txt"), "w", encoding="utf-8") as f:
            f.write("hideoverlay=1\n")
    update_mod(game_dir)
    return removed


# ------------------------------------------------------------------ other players
# As host, the mod applies these to other players' characters on the server.
PLAYER_KEYS = {"god", "durability", "nohunger", "carry", "xp", "damage", "falldamage", "freeze", "walk", "fly"}
PLAYER_ACTIONS = {"dw.p.heal", "dw.p.refill", "dw.p.give", "dw.p.take", "dw.p.bring", "dw.p.goto",
                  "dw.p.home", "dw.p.revealmap", "dw.p.normal", "dw.p.grave", "dw.p.gotograve",
                  "dw.p.back", "dw.p.tp", "dw.p.inspect"}
# Actions that can be done to every other player at once (main.lua "dw.all")
ALL_ACTIONS = {"heal", "refill", "bring", "home", "grave", "back", "revealmap"}


def players(game_dir):
    """Everyone in the world right now, from the mod's players.txt (empty when it's stale)."""
    w = win64_dir(game_dir)
    path = os.path.join(mod_dir(w), "players.txt") if w else None
    if not path or not os.path.isfile(path) or time.time() - os.path.getmtime(path) > 8:
        return []
    out = []
    for line in open(path, encoding="utf-8", errors="replace").read().splitlines():
        parts = line.split("\t")
        if len(parts) < 8:
            continue
        try:
            out.append({"name": parts[0], "me": parts[1] == "1", "spawned": parts[2] == "1",
                        "x": float(parts[3]), "y": float(parts[4]), "z": float(parts[5]),
                        "health": float(parts[6]), "maxHealth": float(parts[7])})
        except ValueError:
            continue
    return out


def read_pwanted(game_dir):
    w = win64_dir(game_dir)
    path = os.path.join(mod_dir(w), "pwanted.txt") if w else None
    out = {}
    if path and os.path.isfile(path):
        for line in open(path, encoding="utf-8", errors="replace").read().splitlines():
            name, _, kv = line.rpartition("\t")
            k, _, v = kv.partition("=")
            if name and k in PLAYER_KEYS:
                try:
                    out.setdefault(name, {})[k] = float(v)
                except ValueError:
                    pass
    return out


def set_pwanted(game_dir, name, key, value):
    """Turn a switch on (value) or off (None) for one other player; key None clears them all."""
    if key is not None and key not in PLAYER_KEYS:
        raise CheatError("Unknown cheat.")
    if not name or any(c in name for c in "\t\r\n"):
        raise CheatError("Bad player name.")
    w = win64_dir(game_dir)
    if not w or not os.path.isdir(mod_dir(w)):
        raise CheatError("Cheats aren't installed.")
    cur = read_pwanted(game_dir)
    mine = cur.setdefault(name, {})
    if key is None:
        mine.clear()
    elif value is None:
        mine.pop(key, None)
    else:
        mine[key] = float(value)
    with open(os.path.join(mod_dir(w), "pwanted.txt"), "w", encoding="utf-8") as f:
        for n in sorted(cur):
            for k, v in sorted(cur[n].items()):
                f.write(f"{n}\t{k}={v:g}\n")
    return {n: v for n, v in cur.items() if v}
