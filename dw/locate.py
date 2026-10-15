"""Find RuneScape: Dragonwilds installs and save folders on this PC.

Read-only: this module only lists folders and reads small text files
(Steam .vdf/.acf, Epic .item manifests, registry values). It never writes,
moves or deletes anything.

    find_game_installs(deep=True, progress=None) -> [install dict]
    find_save_roots(installs=None)               -> [save root dict]

Install dict:
    {"path": install root, "paks": <root>\\RSDragonwilds\\Content\\Paks,
     "kind": "Steam" | "Epic" | "Jagex" | "Other copy",
     "version": exe ProductVersion or None, "notes": []}
Save root dict:
    {"path": folder holding SaveCharacters and/or SaveGames,
     "kind": "Default" | "Install folder" | "Other",
     "characters": [names], "worlds": [names], "modified": ISO time or None}
"""
import ctypes
import datetime
import glob
import json
import os
import re
import struct
import sys
import threading
import time

try:
    import winreg
except ImportError:          # not Windows: registry sources are skipped
    winreg = None

GAME_DIR_NAME = "RSDragonwilds"
UTOC_NAME = "RSDragonwilds-Windows.utoc"
EXE_NAMES = ("RSDragonwilds.exe", "RSDragonwilds-Win64-Shipping.exe")

# Other install locations: folders checked on every fixed drive.
COMMON_ROOTS = [
    "Juegos", "Program Files", "Program Files (x86)",
    "Downloads",
    r"SteamLibrary\steamapps\common", r"Program Files (x86)\Steam\steamapps\common",
    r"Program Files\Steam\steamapps\common", r"Epic Games", r"Program Files\Epic Games",
]
# Deep scan: directory names never entered.
SKIP_DIRS = {
    "windows", "$recycle.bin", "system volume information", "node_modules", ".git",
    "appdata", "$windows.~bt", "$windows.~ws", "$winreagent", "recovery", "perflogs",
    "windowsapps", "msocache", "config.msi", "winsxs", "__pycache__", ".venv", "venv",
}
SKIP_PATHS = {os.path.normcase(r"ProgramData\Microsoft")}
DEEP_TIME_LIMIT = 8.0      # seconds per drive
DEEP_DIR_LIMIT = 200_000   # directories per drive
DEEP_MAX_DEPTH = 4

FILE_ATTRIBUTE_HIDDEN = 0x2
FILE_ATTRIBUTE_SYSTEM = 0x4
FILE_ATTRIBUTE_REPARSE_POINT = 0x400
DRIVE_FIXED = 3


def _norm(path):
    """Key used to de-duplicate paths (case-insensitive, absolute)."""
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _looks_like_game(name):
    """Folder names used for the game: 'RuneScape Dragonwilds', 'RSDragonwilds', ..."""
    n = name.lower()
    return "dragonwilds" in n or "dragon wilds" in n


def _say(progress, msg):
    if progress:
        try:
            progress(msg)
        except Exception:
            pass


def _listdir(path):
    try:
        return os.listdir(path)
    except OSError:
        return []


# ---------------------------------------------------------------- installs

def paks_dir(folder):
    """Content\\Paks under folder (or folder\\RSDragonwilds) holding the .utoc, else None."""
    for root in (os.path.join(folder, GAME_DIR_NAME), folder):
        p = os.path.join(root, "Content", "Paks")
        if os.path.isfile(os.path.join(p, UTOC_NAME)):
            return p
    return None


def resolve_install(folder):
    """Install root for a candidate folder, or None if it is not a game install.

    Accepts the install root itself, its RSDragonwilds subfolder, or a folder
    that has Content\\Paks directly in it.
    """
    if not folder or not os.path.isdir(folder):
        return None
    if os.path.basename(folder.rstrip("\\/")).lower() == GAME_DIR_NAME.lower():
        parent = os.path.dirname(folder.rstrip("\\/"))
        if paks_dir(parent):
            return parent
    return folder if paks_dir(folder) else None




def _exe_version(path):
    """ProductVersion string from an exe's version resource, or None."""
    try:
        ver = ctypes.windll.version
    except (AttributeError, OSError):
        return None
    try:
        ver.GetFileVersionInfoSizeW.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p]
        ver.GetFileVersionInfoSizeW.restype = ctypes.c_uint
        ver.GetFileVersionInfoW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p]
        ver.VerQueryValueW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_uint)]
        size = ver.GetFileVersionInfoSizeW(path, None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ver.GetFileVersionInfoW(path, 0, size, buf):
            return None
        ptr, ln = ctypes.c_void_p(), ctypes.c_uint()
        # String table first: ProductVersion as the developer wrote it.
        if ver.VerQueryValueW(buf, "\\VarFileInfo\\Translation", ctypes.byref(ptr), ctypes.byref(ln)) \
                and ln.value >= 4:
            lang, cp = struct.unpack("<HH", ctypes.string_at(ptr.value, 4))
            key = "\\StringFileInfo\\%04x%04x\\ProductVersion" % (lang, cp)
            if ver.VerQueryValueW(buf, key, ctypes.byref(ptr), ctypes.byref(ln)) and ln.value:
                text = ctypes.wstring_at(ptr.value, ln.value).split("\x00")[0].strip()
                if text:
                    return text
        # Fallback: the numeric VS_FIXEDFILEINFO product version.
        if ver.VerQueryValueW(buf, "\\", ctypes.byref(ptr), ctypes.byref(ln)) and ln.value >= 52:
            f = struct.unpack("<13I", ctypes.string_at(ptr.value, 52))
            if f[0] == 0xFEEF04BD:
                ms, ls = f[4], f[5]
                return "%d.%d.%d.%d" % (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)
    except Exception:
        return None
    return None


def install_version(root):
    """Game version from the shipping exe (or the launcher exe)."""
    for exe in (os.path.join(root, GAME_DIR_NAME, "Binaries", "Win64", EXE_NAMES[1]),
                os.path.join(root, EXE_NAMES[0])):
        if os.path.isfile(exe):
            v = _exe_version(exe)
            if v:
                return v
    return None


class _Found:
    """Collects installs in discovery order, de-duplicated by path."""

    def __init__(self):
        self.items, self.seen = [], set()
        self.lock = threading.Lock()

    def add(self, folder, kind):
        root = resolve_install(folder)
        if not root:
            return False
        key = _norm(root)
        with self.lock:
            if key in self.seen:
                return False
            self.seen.add(key)
        item = {"path": os.path.abspath(root), "paks": paks_dir(root), "kind": kind,
                "version": install_version(root), "notes": []}
        with self.lock:
            self.items.append(item)
        return True


# --- registry helpers

def _reg_value(hive, key, name):
    if not winreg:
        return None
    for view in (0, winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
        try:
            with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as k:
                return winreg.QueryValueEx(k, name)[0]
        except OSError:
            continue
    return None


def _uninstall_entries():
    """(key name, values dict) for every Add/Remove Programs entry."""
    if not winreg:
        return
    bases = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, base in bases:
        try:
            top = winreg.OpenKey(hive, base)
        except OSError:
            continue
        with top:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(top, i)
                except OSError:
                    break
                i += 1
                vals = {}
                try:
                    with winreg.OpenKey(top, sub) as k:
                        for name in ("DisplayName", "InstallLocation", "Publisher"):
                            try:
                                vals[name] = str(winreg.QueryValueEx(k, name)[0] or "")
                            except OSError:
                                vals[name] = ""
                except OSError:
                    continue
                yield sub, vals


# --- Steam

def parse_libraryfolders_vdf(path):
    """Library folder paths listed in Steam's libraryfolders.vdf (new and old format)."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return []
    out = []
    # New format: "path"  "D:\\SteamLibrary". Old format: "1"  "D:\\SteamLibrary".
    for m in re.finditer(r'"(path|\d+)"\s+"([^"]+)"', text):
        value = m.group(2).replace("\\\\", "\\")
        if re.match(r"^[A-Za-z]:[\\/]", value) or value.startswith("\\\\"):
            if value not in out:
                out.append(value)
    return out


def parse_acf_installdir(path):
    """The "installdir" value of a Steam appmanifest_*.acf, or None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            m = re.search(r'"installdir"\s+"([^"]+)"', f.read())
    except OSError:
        return None
    return m.group(1).replace("\\\\", "\\") if m else None


def steam_libraries():
    """Steam install folder plus every library folder it knows about."""
    roots = []
    if winreg:
        for hive, key, name in [(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
                                (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam", "InstallPath")]:
            v = _reg_value(hive, key, name)
            if v:
                roots.append(os.path.normpath(v))
    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(env):
            roots.append(os.path.join(os.environ[env], "Steam"))
    libs, seen = [], set()
    for root in roots:
        for lib in [root] + parse_libraryfolders_vdf(os.path.join(root, "steamapps", "libraryfolders.vdf")) \
                + parse_libraryfolders_vdf(os.path.join(root, "config", "libraryfolders.vdf")):
            key = _norm(lib)
            if key not in seen and os.path.isdir(os.path.join(lib, "steamapps")):
                seen.add(key)
                libs.append(lib)
    return libs


def _scan_steam(found, progress):
    for lib in steam_libraries():
        _say(progress, "Steam library " + lib)
        common = os.path.join(lib, "steamapps", "common")
        names = [n for n in _listdir(common) if _looks_like_game(n)]
        for acf in glob.glob(os.path.join(lib, "steamapps", "appmanifest_*.acf")):
            d = parse_acf_installdir(acf)
            if d and _looks_like_game(d) and d not in names:
                names.append(d)
        for n in names:
            found.add(os.path.join(common, n), "Steam")


# --- Epic

def parse_epic_manifest(path):
    """InstallLocation from an Epic .item manifest if it is Dragonwilds, else None."""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    names = " ".join(str(data.get(k, "")) for k in ("DisplayName", "AppName", "InstallLocation"))
    if "dragonwilds" in names.lower() and data.get("InstallLocation"):
        return data["InstallLocation"]
    return None


def _scan_epic(found, progress):
    base = os.path.join(os.environ.get("ProgramData", r"C:\ProgramData"),
                        "Epic", "EpicGamesLauncher", "Data", "Manifests")
    _say(progress, "Epic manifests")
    for item in glob.glob(os.path.join(base, "*.item")):
        loc = parse_epic_manifest(item)
        if loc:
            found.add(loc, "Epic")


# --- Jagex Launcher and Add/Remove Programs

def _jagex_dirs():
    dirs = []
    for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA", "ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if not base:
            continue
        for name in _listdir(base):
            if "jagex" in name.lower():
                dirs.append(os.path.join(base, name))
    return dirs


def _scan_jagex(found, progress):
    # Folders under any Jagex directory (launcher games go in a subfolder), 3 levels.
    for jdir in _jagex_dirs():
        _say(progress, "Jagex folder " + jdir)
        level = [jdir]
        for _ in range(3):
            nxt = []
            for d in level:
                for n in _listdir(d):
                    p = os.path.join(d, n)
                    if _looks_like_game(n):
                        found.add(p, "Jagex")
                    elif os.path.isdir(p):
                        nxt.append(p)
            level = nxt[:200]
    # Add/Remove Programs entries named like the game.
    _say(progress, "Installed programs list")
    for key, vals in _uninstall_entries():
        if not _looks_like_game(vals["DisplayName"]) or not vals["InstallLocation"]:
            continue
        loc = vals["InstallLocation"].strip('"')
        if key.lower().startswith("steam app"):
            kind = "Steam"
        elif "jagex" in (vals["Publisher"] + loc).lower():
            kind = "Jagex"
        elif "epic" in loc.lower():
            kind = "Epic"
        else:
            kind = "Other copy"
        found.add(loc, kind)


# --- other copies in common folders

def fixed_drives():
    """Root paths ("C:\\") of every fixed (local hard disk) drive."""
    try:
        k32 = ctypes.windll.kernel32
    except (AttributeError, OSError):
        return []
    mask = k32.GetLogicalDrives()
    out = []
    for i in range(26):
        if mask & (1 << i):
            root = chr(65 + i) + ":\\"
            if k32.GetDriveTypeW(ctypes.c_wchar_p(root)) == DRIVE_FIXED:
                out.append(root)
    return out


def _user_folders():
    home = os.path.expanduser("~")
    out = [os.path.join(home, n) for n in ("Downloads", "Desktop", "Documents", "Games")]
    one = os.environ.get("OneDrive")
    if one:
        out += [os.path.join(one, n) for n in ("Desktop", "Documents")]
    return out


def _scan_common(found, progress):
    roots = []
    for drive in fixed_drives():
        roots.append(drive)
        roots += [os.path.join(drive, sub) for sub in COMMON_ROOTS]
        # Game, Games, Games2, Games3 ... at the drive root
        roots += [os.path.join(drive, n) for n in _listdir(drive) if re.match(r"(?i)^games?\d*$", n)]
    roots += _user_folders()
    for root in roots:
        if not os.path.isdir(root):
            continue
        _say(progress, "Checking " + root)
        for n in _listdir(root):
            p = os.path.join(root, n)
            if _looks_like_game(n):
                # The game folder itself, or a folder with the game one level down.
                if not found.add(p, "Other copy"):
                    for m in _listdir(p):
                        if _looks_like_game(m) or m.lower() == GAME_DIR_NAME.lower():
                            found.add(os.path.join(p, m), "Other copy")
            elif root != os.path.splitdrive(root)[0] + "\\" and os.path.isdir(p):
                # One level deeper (e.g. Games\Jagex\RuneScape Dragonwilds).
                for m in _listdir(p):
                    if _looks_like_game(m):
                        found.add(os.path.join(p, m), "Other copy")


# --- deep scan

def deep_scan(root, max_depth=DEEP_MAX_DEPTH, time_limit=DEEP_TIME_LIMIT,
              max_dirs=DEEP_DIR_LIMIT, progress=None):
    """Breadth-first walk of root looking for game folders. Returns candidate folders.

    A candidate is a folder named RSDragonwilds, or a folder holding one of the
    game exes. Stops after time_limit seconds or max_dirs directories.
    """
    start = time.monotonic()
    hits, level, count = [], [(root, 0)], 0
    while level:
        nxt = []
        for path, depth in level:
            if time.monotonic() - start > time_limit or count >= max_dirs:
                _say(progress, "Stopped early in " + root)
                return hits
            count += 1
            try:
                with os.scandir(path) as it:
                    entries = list(it)
            except OSError:          # PermissionError, too-long path, vanished
                continue
            for e in entries:
                name = e.name
                try:
                    if e.is_file(follow_symlinks=False):
                        if name in EXE_NAMES:
                            hits.append(path if name == EXE_NAMES[0]
                                        else os.path.abspath(os.path.join(path, "..", "..", "..")))
                        continue
                    if not e.is_dir(follow_symlinks=False):
                        continue
                    attrs = getattr(e.stat(follow_symlinks=False), "st_file_attributes", 0)
                except OSError:
                    continue
                if name.lower() == GAME_DIR_NAME.lower():
                    hits.append(e.path)
                    continue
                if attrs & (FILE_ATTRIBUTE_HIDDEN | FILE_ATTRIBUTE_SYSTEM | FILE_ATTRIBUTE_REPARSE_POINT):
                    continue
                if name.lower() in SKIP_DIRS or name.startswith("."):
                    continue
                rel = os.path.normcase(os.path.relpath(e.path, root))
                if rel in SKIP_PATHS:
                    continue
                if depth + 1 < max_depth:
                    nxt.append((e.path, depth + 1))
        level = nxt
    return hits


def _scan_deep(found, progress):
    def one(drive):
        _say(progress, "Deep scan of " + drive)
        for hit in deep_scan(drive, progress=progress):
            found.add(hit, "Other copy")
    threads = [threading.Thread(target=one, args=(d,), daemon=True) for d in fixed_drives()]
    for t in threads:
        t.start()
    for t in threads:
        t.join(DEEP_TIME_LIMIT + 5)


def find_game_installs(deep=True, progress=None):
    """Every Dragonwilds install found on this PC, in source order.

    Sources: Steam, Epic, Jagex/Add-Remove Programs, common folders on every
    fixed drive, then (deep=True) a bounded scan of each drive. progress(msg)
    is called with short status text (from worker threads during the deep scan).
    """
    found = _Found()
    for scan in (_scan_steam, _scan_epic, _scan_jagex, _scan_common):
        try:
            scan(found, progress)
        except Exception as e:       # one broken source must not stop the others
            _say(progress, "%s failed: %s" % (scan.__name__, e))
    if deep:
        _scan_deep(found, progress)
    _say(progress, "Found %d install(s)" % len(found.items))
    return found.items


# ---------------------------------------------------------------- saves

def save_root_info(path, kind):
    """Save root dict for path, or None if it has no character or world saves."""
    chars = sorted(glob.glob(os.path.join(glob.escape(path), "SaveCharacters", "*.json")))
    worlds = sorted(glob.glob(os.path.join(glob.escape(path), "SaveGames", "*.sav")))
    if not chars and not worlds:
        return None
    mtimes = []
    for f in chars + worlds:
        try:
            mtimes.append(os.path.getmtime(f))
        except OSError:
            pass
    modified = datetime.datetime.fromtimestamp(max(mtimes)).isoformat(timespec="seconds") if mtimes else None
    return {"path": os.path.abspath(path), "kind": kind,
            "characters": [os.path.splitext(os.path.basename(f))[0] for f in chars],
            "worlds": [os.path.splitext(os.path.basename(f))[0] for f in worlds],
            "modified": modified}


def _find_save_dirs(base, max_depth=5, max_dirs=5000, need_game_name=False):
    """Folders under base that hold SaveCharacters or SaveGames (bounded walk).

    SaveGames alone is a generic Unreal name, so it only counts when the path
    mentions Dragonwilds (or need_game_name is False and SaveCharacters is there).
    """
    out, level, count = [], [(base, 0)], 0
    while level and count < max_dirs:
        nxt = []
        for path, depth in level:
            count += 1
            names = _listdir(path)
            lower = {n.lower() for n in names}
            game_path = _looks_like_game(path)
            if "savecharacters" in lower and (game_path or not need_game_name):
                out.append(path)
            elif "savegames" in lower and game_path:
                out.append(path)
            if depth < max_depth:
                for n in names:
                    if n.lower() in ("savecharacters", "savegames"):
                        continue
                    p = os.path.join(path, n)
                    if os.path.isdir(p):
                        nxt.append((p, depth + 1))
        level = nxt
    return out


def find_save_roots(installs=None):
    """Every folder holding Dragonwilds character (.json) or world (.sav) saves."""
    if installs is None:
        installs = find_game_installs(deep=False)
    cands = []                                   # (path, kind) in priority order
    local = os.environ.get("LOCALAPPDATA", "")
    default = os.path.join(local, "RSDragonwilds", "Saved") if local else ""
    if default:
        cands.append((default, "Default"))
    # Other Windows user profiles we can read.
    home = os.path.expanduser("~")
    users_dir = os.path.dirname(home)
    for user in _listdir(users_dir):
        cands.append((os.path.join(users_dir, user, "AppData", "Local", "RSDragonwilds", "Saved"), "Other"))
    # Portable setups keep saves next to the game.
    for inst in installs or []:
        root = inst["path"] if isinstance(inst, dict) else inst
        cands.append((os.path.join(root, GAME_DIR_NAME, "Saved"), "Install folder"))
        cands.append((os.path.join(root, "Saved"), "Install folder"))
    if os.environ.get("APPDATA"):
        cands.append((os.path.join(os.environ["APPDATA"], "RSDragonwilds", "Saved"), "Other"))
    # Shared documents: some setups keep saves there.
    search = []
    public_docs = os.path.join(os.environ.get("PUBLIC", os.path.join(users_dir, "Public")), "Documents")
    search.append((public_docs, True))
    for base, need_name in search:
        if os.path.isdir(base):
            for d in _find_save_dirs(base, need_game_name=need_name):
                cands.append((d, "Other"))

    out, seen = [], set()
    for path, kind in cands:
        key = _norm(path)
        if key in seen or not os.path.isdir(path):
            continue
        seen.add(key)
        info = save_root_info(path, kind)
        if info:
            out.append(info)
    return out


if __name__ == "__main__":
    t = time.monotonic()
    for i in find_game_installs(deep="--deep" in sys.argv, progress=print):
        print("INSTALL", i)
    for s in find_save_roots():
        print("SAVES", s)
    print("%.2fs" % (time.monotonic() - t))
