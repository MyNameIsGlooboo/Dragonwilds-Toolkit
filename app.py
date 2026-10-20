"""Dragonwilds Toolkit - desktop app entry point."""
import http.server
import json
import os
import socketserver
import subprocess
import sys
import threading
import time
import zipfile
import shutil
import traceback

import webview

from dw import core, locate
from dw.gamedata import build_library, find_game_paks, load_library, library_is_stale
from dw.savefile import CharacterSave, SaveError
from dw.world import WorldEditor, list_worlds
from dw.worldsave import WorldSave, WorldSaveError, get_container, list_containers
from dw import features, cheats
from dw.watcher import SaveWatcher
from dw.version import TITLE, VERSION
from dw import updates
from dw import overlay as overlay_mod

WATCHER = SaveWatcher()
BACKPACK_SLOTS = 32  # backpack page; slots 32+ are the rune, ammo and quest pouches (dw.capacity)


def capacity_for(cls):
    """Slot count of a chest class, from dw.capacity when available."""
    try:
        from dw.capacity import slots_for_class
        return slots_for_class(cls)[0]
    except Exception:
        return None

BASE = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.path.join(BASE, "ui")


class _Handler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def translate_path(self, path):
        path = path.split("?", 1)[0].split("#", 1)[0]
        for prefix, root in (("/lib/", core.CACHE_DIR), ("/", UI_DIR)):
            if path.startswith(prefix):
                rel = os.path.normpath(path[len(prefix):].replace("/", os.sep)).lstrip(os.sep)
                full = os.path.abspath(os.path.join(root, rel))
                if full.startswith(os.path.abspath(root)):
                    return full
        return os.path.join(UI_DIR, "__missing__")

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_POST(self):
        # Developer mode only (--serve): lets a normal browser call the Api.
        api = getattr(self.server, "dev_api", None)
        name = self.path[len("/api/"):] if self.path.startswith("/api/") else ""
        if not api or name.startswith("_") or not callable(getattr(api, name, None)):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length") or 0)
        args = json.loads(self.rfile.read(length) or b"[]")
        body = json.dumps(getattr(api, name)(*args)).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_server(port=0, dev_api=None):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", port), _Handler)
    srv.daemon_threads = True
    srv.dev_api = dev_api
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv.server_address[1]


def friendly_error(e):
    """Plain-language message for common Windows file problems; technical text otherwise."""
    name = os.path.basename(getattr(e, "filename", "") or "") or "a file"
    if isinstance(e, PermissionError):
        return (f"Windows blocked access to {name}. Close the game and any program that has it open "
                "(OneDrive syncing or antivirus can also lock it), then try again.")
    if isinstance(e, FileNotFoundError):
        return f"{name} wasn't found. It may have been moved or deleted. Click Reload at the top right."
    if isinstance(e, OSError) and getattr(e, "errno", None) == 28:
        return "Your disk is full. Free some space, then try again."
    if isinstance(e, MemoryError):
        return "Your PC ran out of memory. Close some programs, then try again."
    return f"Something went wrong: {type(e).__name__}: {e}. Details are saved in errors.log (Backups > Open folder)."


def _safe(fn):
    def wrap(*a, **kw):
        if os.environ.get("DW_TRACE"):
            print("api:", fn.__name__, flush=True)
        try:
            return fn(*a, **kw)
        except (SaveError, WorldSaveError) as e:
            return {"error": str(e)}
        except Exception as e:  # report to the UI instead of failing silently
            traceback.print_exc()
            try:
                os.makedirs(core.APP_DIR, exist_ok=True)
                with open(os.path.join(core.APP_DIR, "errors.log"), "a", encoding="utf-8") as f:
                    f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {fn.__name__}\n{traceback.format_exc()}\n")
            except OSError:
                pass
            if ST.lib is None and isinstance(e, TypeError):
                return {"error": "The game library isn't ready. Open Game library (bottom left) and click Build library."}
            return {"error": friendly_error(e)}
    wrap.__name__ = fn.__name__
    return wrap


class _State:
    """App state. Kept off the Api object: pywebview walks every attribute of the
    js_api object, and walking the native window or the item library freezes it."""


ST = _State()


class Api:
    def __init__(self):
        ST.window = None
        ST.settings = core.load_settings()
        ST.lib = load_library(core.CACHE_DIR)
        ST.editor = core.Editor(ST.lib) if ST.lib else None
        ST.char_file = None
        ST.world_file = None
        ST.world = None
        ST.build_log = []
        ST.building = False
        ST.mod_checked = False
        WATCHER.enabled = ST.settings.get("watcher", True)
        root = ST.settings.get("saveRoot")
        if root and not os.environ.get("DW_SAVE_DIR") and os.path.isdir(root):
            core.set_save_root(root)

    # ---------------------------------------------------------------- setup
    @_safe
    def status(self):
        return {
            "gameRunning": core.game_running(),
            "gameProcesses": core.game_processes(),
            "libraryReady": ST.lib is not None,
            "libraryStale": bool(ST.lib and library_is_stale(ST.lib)),
            "building": ST.building,
            "characters": core.list_characters(),
            "current": ST.char_file,
            "lastCharacter": ST.settings.get("lastCharacter"),
            "saveDir": core.SAVE_DIR,
            "version": VERSION,
        }

    @_safe
    def game_dirs(self, deep=False):
        """Game installs: official (Steam/Epic/Jagex) and other copies on all drives."""
        found = locate.find_game_installs(deep=bool(deep))
        last = ST.settings.get("gameDir")
        paths = [f["path"].lower() for f in found]
        if last and find_game_paks(last) and last.lower() not in paths:
            found.insert(0, {"path": last, "kind": "Last used", "notes": [], "version": None})
        return {"dirs": [{"path": f["path"], "kind": f["kind"], "notes": f.get("notes") or []} for f in found]}

    @_safe
    def save_roots(self):
        roots = locate.find_save_roots()
        current = os.path.normpath(core.save_root())
        if not any(os.path.normcase(r["path"]) == os.path.normcase(current) for r in roots) and os.path.isdir(current):
            roots.insert(0, locate.save_root_info(current, "Default"))
        return {"roots": roots, "current": current}

    @_safe
    def set_save_root(self, path):
        known = [os.path.normcase(r["path"]) for r in locate.find_save_roots()]
        if os.path.normcase(path) not in known:
            return {"error": "That folder is not a Dragonwilds save folder."}
        core.set_save_root(path)
        ST.char_file = None
        ST.world_file = None
        ST.settings["saveRoot"] = path
        core.save_settings(ST.settings)
        return {"ok": True}

    @_safe
    def browse_game(self):
        res = ST.window.create_file_dialog(webview.FOLDER_DIALOG)
        if not res:
            return {"dir": None}
        d = res[0]
        if not find_game_paks(d):
            return {"error": "That folder does not look like the RuneScape Dragonwilds install folder."}
        return {"dir": d}

    @_safe
    def build(self, game_dir):
        if ST.building:
            return {"ok": True}
        if not game_dir or not os.path.isdir(game_dir):
            return {"error": "That folder doesn't exist any more. Click Scan all drives or Browse to pick the game folder."}
        if not find_game_paks(game_dir):
            return {"error": "That folder isn't a RuneScape: Dragonwilds install. Pick the game's main folder "
                             "(the one that has the RSDragonwilds folder inside it)."}
        ST.building = True
        ST.build_log = []

        def run():
            try:
                lib = build_library(game_dir, core.CACHE_DIR, log=ST.build_log.append)
                ST.lib = lib
                ST.editor = core.Editor(lib)
                ST.settings["gameDir"] = game_dir
                core.save_settings(ST.settings)
                ST.build_log.append("DONE")
            except Exception as e:
                traceback.print_exc()
                log_crash("build_library")
                if isinstance(e, OSError):
                    msg = friendly_error(e)
                else:
                    msg = ("Couldn't read the game files. The game may have had an update this toolkit doesn't "
                           "understand yet, or some files are damaged (in Steam: right-click the game > "
                           "Properties > Installed Files > Verify). Details: " + str(e))
                ST.build_log.append("ERROR: " + msg)
            finally:
                ST.building = False

        threading.Thread(target=run, daemon=True).start()
        return {"ok": True}

    @_safe
    def build_progress(self):
        return {"log": ST.build_log[-12:], "building": ST.building}

    # ---------------------------------------------------------- characters
    def _path(self):
        return os.path.join(core.SAVE_DIR, ST.char_file)

    @_safe
    def open_character(self, file):
        if os.path.basename(file) != file or not file.endswith(".json"):
            return {"error": "Bad file name."}
        ST.char_file = file
        ST.settings["lastCharacter"] = file
        core.save_settings(ST.settings)
        save = CharacterSave(self._path())
        try:
            cheats.set_me(self._game_dir(), save.data.get("meta_data", {}).get("char_guid"))
        except OSError:
            pass
        return {"view": ST.editor.view(save)}

    @_safe
    def item_list(self):
        keep = ("id", "name", "icon", "category", "slot", "maxStack", "maxDurability", "powerLevel", "skill",
                "description", "path")
        return {"items": [{k: v.get(k) for k in keep} for v in ST.lib["items"].values()
                          if not v["asset"].endswith(("_Deprecated", "_Deleted"))],
                "xpCurve": ST.lib.get("xpCurve") or []}

    @_safe
    def bundles(self):
        return {"bundles": core.Editor.BUNDLES}

    @_safe
    def bundle_edits(self, bundle_id, amount=None):
        save = CharacterSave(self._path())
        return {"edits": ST.editor.bundle_edits(save, bundle_id, amount)}

    @_safe
    def gear_check(self, item_id, container=None, slot=None):
        save = CharacterSave(self._path())
        w, e = ST.editor.gear_check(save, item_id, container, slot)
        return {"warnings": w, "errors": e}

    @_safe
    def preview(self, edits):
        save = CharacterSave(self._path())
        w, e = ST.editor.apply(save, edits)
        return {"warnings": w, "errors": e, "diff": save.diff_lines()[:200],
                "editable": save.roundtrip_ok, "view": ST.editor.view(save)}

    @_safe
    def commit(self, edits):
        if core.game_running():
            return {"error": "The game is running. Quit the game completely, then save again."}
        save = CharacterSave(self._path())
        w, e = ST.editor.apply(save, edits)
        if e:
            return {"error": "Fix these first: " + " ".join(e)}
        backup = core.make_backup(save.path, "auto")
        save.write()
        WATCHER.pause_for()
        return {"ok": True, "backup": backup, "view": ST.editor.view(CharacterSave(self._path()))}

    # ---------------------------------------------------------------- world
    def _world_path(self):
        return os.path.join(core.WORLD_DIR, ST.world_file)

    def _world_view(self, save, with_layers=True):
        char = None
        if ST.char_file and os.path.isfile(self._path()):
            char = CharacterSave(self._path())
        return WorldEditor(ST.lib).view(save, char, with_layers)

    @_safe
    def world_list(self):
        return {"worlds": list_worlds(core.WORLD_DIR)}

    @_safe
    def open_world(self, file):
        if os.path.basename(file) != file or not file.lower().endswith(".sav"):
            return {"error": "Bad file name."}
        ST.world_file = file
        return {"view": self._world_view(WorldSave(self._world_path()))}

    @_safe
    def world_preview(self, edits):
        save = WorldSave(self._world_path())
        ed = WorldEditor(ST.lib)
        w, e = ed.apply(save, edits)
        return {"warnings": w, "errors": e, "diff": ed.diff_lines(save), "view": self._world_view(save, False)}

    @_safe
    def world_commit(self, edits):
        if core.game_running():
            return {"error": "The game is running. Quit the game completely, then save again."}
        save = WorldSave(self._world_path())
        w, e = WorldEditor(ST.lib).apply(save, edits)
        if e:
            return {"error": "Fix these first: " + " ".join(e)}
        backup = core.make_backup(save.path, "auto")
        save.write()
        WATCHER.pause_for()
        return {"ok": True, "backup": backup, "view": self._world_view(WorldSave(self._world_path()))}

    # ------------------------------------------------------------- features
    @_safe
    def teleport_target(self, x, y):
        """Height for a teleport to (x, y), read from objects around it in the open world."""
        if not ST.world_file:
            return {"error": "Open a world first, so the editor can find the ground height."}
        g = features.ground_height(WorldSave(self._world_path()), float(x), float(y))
        if not g:
            return {"error": "Nothing is saved near that spot, so the ground height is unknown. Pick a spot nearer to buildings, chests or resources."}
        return g

    @_safe
    def chest_entry(self, cid, slot):
        """The full item entry in a chest slot (kept whole when moving it to the backpack)."""
        c = get_container(WorldSave(self._world_path()), cid)
        e = c.slots().get(int(slot))
        return {"entry": e} if e else {"error": "That slot is empty."}

    @_safe
    def backpack_entry(self, slot):
        e = CharacterSave(self._path()).container("Inventory").get(int(slot))
        return {"entry": e} if e else {"error": "That slot is empty."}

    def _capacity(self, cid, view_container=None):
        cls = (view_container or {}).get("class")
        slots = capacity_for(cls) if cls else None
        if slots:
            return slots
        used = (view_container or {}).get("maxSlotIndex")
        return max((used if used is not None else -1) + 1, 16)

    @_safe
    def presets(self):
        return {"builtin": features.BUILTIN_PRESETS, "user": features.load_user_presets()}

    @_safe
    def preset_edits(self, preset_id, target, container=None):
        """Edits that fill the backpack (target 'char') or a chest (target 'world') with a preset."""
        allp = features.BUILTIN_PRESETS + features.load_user_presets()
        preset = next((p for p in allp if p["id"] == preset_id), None)
        if not preset:
            return {"error": "Preset not found."}
        wanted = features.preset_items(preset, ST.lib["items"])
        if target == "char":
            existing = CharacterSave(self._path()).container("Inventory")
            edits, skipped = features.plan_fill(existing, wanted, ST.lib["items"], BACKPACK_SLOTS)
            for e in edits:
                e["container"] = "Inventory"
        else:
            save = WorldSave(self._world_path())
            c = get_container(save, container)
            info = next((x for x in list_containers(save) if x["id"] == container), {})
            edits, skipped = features.plan_fill(c.slots(), wanted, ST.lib["items"], self._capacity(container, info))
            for e in edits:
                e["container"] = container
        return {"edits": edits, "skipped": skipped, "name": preset["name"]}

    @_safe
    def save_preset(self, name, source, container=None):
        if source == "char":
            entries = CharacterSave(self._path()).container("Inventory").values()
        else:
            entries = get_container(WorldSave(self._world_path()), container).slots().values()
        rows = []
        for e in entries:
            if e.get("ItemData"):
                row = {"itemId": e["ItemData"]}
                if "Durability" in e:
                    row["durability"] = int(float(e["Durability"]))
                else:
                    row["count"] = int(float(e.get("Count", 1)))
                rows.append(row)
        if not rows:
            return {"error": "Nothing to save: it's empty."}
        user = features.load_user_presets()
        pid = "user-" + str(int(time.time() * 1000))
        user.append({"id": pid, "name": name.strip()[:40] or "My preset", "desc": f"{len(rows)} stack(s)", "items": rows})
        features.save_user_presets(user)
        return {"ok": True, "id": pid}

    @_safe
    def delete_preset(self, preset_id):
        features.save_user_presets([p for p in features.load_user_presets() if p["id"] != preset_id])
        return {"ok": True}

    @_safe
    def organize(self, container_ids):
        save = WorldSave(self._world_path())
        infos = {x["id"]: x for x in list_containers(save)}
        caps = {cid: self._capacity(cid, infos.get(cid)) for cid in container_ids}
        try:
            plan, summary = features.plan_organize(save, container_ids, ST.lib["items"], caps)
        except ValueError as e:
            return {"error": str(e)}
        edits = [{"op": "setslots", "container": cid, "slots": {str(k): v for k, v in slots.items()}}
                 for cid, slots in plan.items()]
        return {"edits": edits, "summary": summary}

    @_safe
    def compare(self, key, old_id, new_id="current"):
        """What changed between two backups (or a backup and the current save)."""
        def path_of(bid):
            if bid == "current":
                if key.startswith("World - "):
                    return os.path.join(core.WORLD_DIR, key[len("World - "):] + ".sav")
                return os.path.join(core.SAVE_DIR, key + ".json")
            folder = os.path.join(core.BACKUP_DIR, key, bid)
            name = key[len("World - "):] + ".sav" if key.startswith("World - ") else key + ".json"
            return os.path.join(folder, name)
        a, b = path_of(old_id), path_of(new_id)
        if not (os.path.isfile(a) and os.path.isfile(b)):
            return {"error": "One of those saves is missing."}
        return {"lines": features.compare_files(a, b, ST.lib["items"], ST.lib.get("skills", {}))}

    @_safe
    def watcher_status(self):
        return {"enabled": WATCHER.enabled, "snapshots": WATCHER.count}

    @_safe
    def set_watcher(self, on):
        WATCHER.enabled = bool(on)
        ST.settings["watcher"] = WATCHER.enabled
        core.save_settings(ST.settings)
        return {"enabled": WATCHER.enabled}

    # ---------------------------------------------------------------- cheats
    def _game_dir(self):
        return ST.settings.get("gameDir") or (ST.lib or {}).get("gameDir")

    @_safe
    def cheat_status(self):
        st = cheats.status(self._game_dir())
        if st.get("installed") and not ST.mod_checked:
            # an updated toolkit brings a newer cheat script; the game picks it up on its next start
            ST.mod_checked = True
            st["modUpdated"] = cheats.update_mod(self._game_dir())
        return st

    @_safe
    def cheat_players(self):
        gd = self._game_dir()
        return {"players": cheats.players(gd), "wanted": cheats.read_pwanted(gd)}

    @_safe
    def cheat_pset(self, name, key, value=None):
        try:
            return {"wanted": cheats.set_pwanted(self._game_dir(), name, key, value)}
        except cheats.CheatError as e:
            return {"error": str(e)}

    @_safe
    def cheat_pdo(self, fn, name, args=None):
        if fn not in cheats.PLAYER_ACTIONS:
            return {"error": "Unknown player action."}
        try:
            if fn == "dw.p.normal":
                cheats.set_pwanted(self._game_dir(), name, None, None)
            return {"seq": cheats.send(self._game_dir(), fn, [name] + list(args or []))}
        except cheats.CheatError as e:
            return {"error": str(e)}

    @_safe
    def cheat_install(self):
        try:
            return {"status": cheats.install(self._game_dir())}
        except cheats.CheatError as e:
            return {"error": str(e)}
        except PermissionError:
            return {"error": "Windows didn't allow copying files into the game folder. Close the game, then "
                             "right-click Dragonwilds Toolkit and choose \"Run as administrator\". If your antivirus "
                             "shows a warning about dwmapi.dll (the cheat loader), allow it."}

    @_safe
    def cheat_uninstall(self):
        try:
            return {"status": cheats.uninstall(self._game_dir())}
        except cheats.CheatError as e:
            return {"error": str(e)}

    @_safe
    def cheat_functions(self):
        return {"functions": cheats.functions(self._game_dir())}

    @_safe
    def cheat_send(self, fn, args=None):
        try:
            return {"seq": cheats.send(self._game_dir(), fn, args or [])}
        except cheats.CheatError as e:
            return {"error": str(e)}

    @_safe
    def cheat_wanted(self):
        return {"wanted": cheats.read_wanted(self._game_dir())}

    @_safe
    def cheat_set(self, key, value=None):
        try:
            return {"wanted": cheats.set_wanted(self._game_dir(), key, value)}
        except cheats.CheatError as e:
            return {"error": str(e)}

    @_safe
    def cheat_home(self):
        return {"home": cheats.get_home(self._game_dir())}

    @_safe
    def cheat_set_home(self, x, y, z=None):
        """Home from a map spot; height from objects saved nearby when not given."""
        if z is None:
            if not ST.world_file:
                return {"error": "Open a world first so the ground height can be found."}
            g = features.ground_height(WorldSave(self._world_path()), float(x), float(y))
            if not g:
                return {"error": "Nothing is saved near that spot, so the ground height is unknown. Pick a spot nearer to your base."}
            z = g["z"]
        try:
            return {"home": cheats.set_home(self._game_dir(), x, y, z)}
        except cheats.CheatError as e:
            return {"error": str(e)}

    @_safe
    def cheat_reset_all(self):
        """Every cheat off; the mod puts the game's own values back."""
        out = {}
        for k in list(cheats.read_wanted(self._game_dir())):
            if k != "hideoverlay":  # the overlay choice isn't a cheat
                out = cheats.set_wanted(self._game_dir(), k, None)
        return {"wanted": out}

    @_safe
    def cheat_all(self, action):
        if action not in cheats.ALL_ACTIONS:
            return {"error": "Unknown action."}
        try:
            return {"seq": cheats.send(self._game_dir(), "dw.all", [action])}
        except cheats.CheatError as e:
            return {"error": str(e)}

    @_safe
    def cheat_reset_values(self):
        try:
            return {"removed": cheats.reset_values(self._game_dir())}
        except cheats.CheatError as e:
            return {"error": str(e)}

    @_safe
    def cheat_results(self, since=0):
        return {"results": cheats.results(self._game_dir(), int(since or 0))}

    # --------------------------------------------------------------- unlocks
    @_safe
    def unlocks_view(self, family):
        """Every entry of a family with unlocked flags, plus map regions, for the open character."""
        from dw import unlocks
        if not ST.lib.get("unlocks"):
            return {"error": "Rebuild the game library to add unlock names (Game library, left menu)."}
        save = CharacterSave(self._path())
        if family == "map":
            return {"regions": [{"id": r, "name": n, "revealed": v} for r, n, v in unlocks.revealed_regions(save.data)],
                    "all": unlocks.ALL_REGIONS_BITMAP}
        rows = unlocks.status(save.data, ST.lib, family)
        keep = ("id", "name", "icon", "category", "unlocked", "new", "hidden", "paid", "dlc")
        return {"rows": [{k: r.get(k) for k in keep} for r in rows if not r.get("hidden")]}

    # ----------------------------------------------------------- host wizard
    @_safe
    def pack_world(self, file):
        """Zip a world (and its .backup) to the Desktop, ready to send to a friend."""
        src = os.path.join(core.WORLD_DIR, os.path.basename(file))
        if not os.path.isfile(src):
            return {"error": "World not found."}
        name = os.path.basename(file)[:-4]
        desk = os.path.join(os.path.expanduser("~"), "Desktop")
        onedrive = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
        if os.path.isdir(onedrive):
            desk = onedrive
        out = os.path.join(desk, f"{name} - world for a friend.zip")
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for p in (src, src + ".backup"):
                if os.path.isfile(p):
                    z.write(p, os.path.basename(p))
            z.writestr("HOW TO INSTALL.txt",
                       "Open Dragonwilds Toolkit > Share > Install a world from a friend, "
                       "and pick this zip.\r\nOr copy the .sav file into "
                       "%LOCALAPPDATA%\\RSDragonwilds\\Saved\\SaveGames (game closed).\r\n"
                       "Your own character is not inside; only the world.\r\n")
        return {"path": out}

    @_safe
    def install_world(self):
        if core.game_running():
            return {"error": "The game is running. Quit the game completely first."}
        res = ST.window.create_file_dialog(webview.OPEN_DIALOG, file_types=("World (*.zip;*.sav)",))
        if not res:
            return {"cancelled": True}
        src = res[0]
        os.makedirs(core.WORLD_DIR, exist_ok=True)
        installed = []
        if src.lower().endswith(".zip"):
            with zipfile.ZipFile(src) as z:
                for n in z.namelist():
                    base = os.path.basename(n)
                    if base.lower().endswith((".sav", ".sav.backup")) and base == n:
                        installed.append(base)
                if not any(x.lower().endswith(".sav") for x in installed):
                    return {"error": "No world (.sav) inside that zip."}
                for base in installed:
                    target = os.path.join(core.WORLD_DIR, base)
                    if base.lower().endswith(".sav") and os.path.isfile(target):
                        core.make_backup(target, "before-install")
                    with z.open(base) as fsrc, open(target, "wb") as fdst:
                        shutil.copyfileobj(fsrc, fdst)
        elif src.lower().endswith(".sav"):
            target = os.path.join(core.WORLD_DIR, os.path.basename(src))
            if os.path.isfile(target):
                core.make_backup(target, "before-install")
            shutil.copy2(src, target)
            installed.append(os.path.basename(src))
        else:
            return {"error": "Pick a .zip or .sav file."}
        WATCHER.pause_for()
        return {"installed": installed}

    # -------------------------------------------------------------- backups
    @_safe
    def backups(self):
        """Backup lists for the open character and the open world."""
        groups = []
        if ST.char_file:
            key = core.backup_key(self._path())
            groups.append({"key": key, "label": "Character: " + key, "backups": core.list_backups(key)})
        if ST.world_file:
            key = core.backup_key(self._world_path())
            groups.append({"key": key, "label": key, "backups": core.list_backups(key)})
        return {"groups": groups}

    @_safe
    def backup_now(self):
        out = [core.make_backup(self._path(), "manual")] if ST.char_file else []
        if ST.world_file:
            out.append(core.make_backup(self._world_path(), "manual"))
        return {"backup": out}

    @_safe
    def restore(self, key, backup_id):
        if core.game_running():
            return {"error": "The game is running. Quit the game completely, then restore."}
        allowed = [core.backup_key(self._path())] if ST.char_file else []
        if ST.world_file:
            allowed.append(core.backup_key(self._world_path()))
        if key not in allowed:
            return {"error": "Backup not found."}
        core.restore_backup(key, backup_id)
        WATCHER.pause_for()
        out = {"ok": True}
        if ST.char_file:
            out["view"] = ST.editor.view(CharacterSave(self._path()))
        if ST.world_file:
            out["worldView"] = self._world_view(WorldSave(self._world_path()))
        return out
    # ------------------------------------------------------------- in-game overlay
    @_safe
    def overlay_close(self):
        """Close the in-game window (the game resumes)."""
        if OVERLAY:
            OVERLAY.close()
        return {"ok": True}

    # ------------------------------------------------------------- updates
    @_safe
    def update_check(self):
        """Newer release on GitHub? Notice only; the user downloads it themselves."""
        if not ST.settings.get("updateCheck", True):
            return {"available": False, "enabled": False, "current": VERSION}
        out = updates.check(ST.settings.get("skipVersion"))
        out["enabled"] = True
        return out

    @_safe
    def update_prefs(self, enabled=None, skip=None):
        if enabled is not None:
            ST.settings["updateCheck"] = bool(enabled)
        if skip is not None:
            ST.settings["skipVersion"] = str(skip)
        core.save_settings(ST.settings)
        return {"enabled": ST.settings.get("updateCheck", True), "skip": ST.settings.get("skipVersion")}

    @_safe
    def open_release_page(self, url=None):
        """Open the release page in the normal web browser (only this project's GitHub pages)."""
        import webbrowser
        if not url or not url.startswith(f"https://github.com/{updates.REPO}/"):
            url = updates.RELEASES_PAGE + "/latest"
        webbrowser.open(url)
        return {"ok": True}

    @_safe
    def open_folder(self, which):
        folder = {"backups": core.BACKUP_DIR,
                  "saves": core.SAVE_DIR}.get(which)
        if folder and os.path.isdir(folder):
            subprocess.Popen(["explorer", folder])
        return {"ok": True}


OVERLAY = None


def _mod_folder(api):
    w = cheats.win64_dir(api._game_dir())
    return cheats.mod_dir(w) if w else None


WEBVIEW2_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_PAGE = "https://developer.microsoft.com/microsoft-edge/webview2/#download"


def message_box(text, title=TITLE, flags=0x10):
    """Native Windows message box, for problems that happen before the window can open."""
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        print(text, file=sys.stderr)
        return 0


def webview2_installed():
    """True when the Microsoft Edge WebView2 runtime (which draws the window) is present."""
    try:
        import winreg
    except ImportError:
        return True
    keys = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients" + "\\" + WEBVIEW2_GUID),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\EdgeUpdate\Clients" + "\\" + WEBVIEW2_GUID),
            (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\EdgeUpdate\Clients" + "\\" + WEBVIEW2_GUID)]
    for hive, path in keys:
        try:
            with winreg.OpenKey(hive, path) as k:
                version, _ = winreg.QueryValueEx(k, "pv")
                if version and version != "0.0.0.0":
                    return True
        except OSError:
            continue
    return False


def log_crash(where):
    try:
        os.makedirs(core.APP_DIR, exist_ok=True)
        with open(os.path.join(core.APP_DIR, "errors.log"), "a", encoding="utf-8") as f:
            f.write(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} {where}\n{traceback.format_exc()}\n")
    except OSError:
        pass


def main():
    if "--serve" in sys.argv:
        # Developer mode: serve the UI and Api to a normal browser, no window.
        port = start_server(8765, dev_api=Api())
        WATCHER.start()
        print(f"Serving on http://127.0.0.1:{port}/index.html?dev", flush=True)
        threading.Event().wait()
        return
    if sys.platform == "win32" and not webview2_installed():
        answer = message_box(
            "Dragonwilds Toolkit needs Microsoft Edge WebView2 to draw its window, and it isn't on this PC.\n\n"
            "It's free and comes with most Windows 10 and 11 PCs. Click Yes to open Microsoft's download page "
            "(pick \"Evergreen Bootstrapper\"), install it, then start the toolkit again.\n\n"
            "Click No to try starting anyway.", flags=0x34)  # Yes/No + warning icon
        if answer == 6:  # Yes
            import webbrowser
            webbrowser.open(WEBVIEW2_PAGE)
            return
    try:
        port = start_server()
    except OSError:
        log_crash("start_server")
        message_box("Dragonwilds Toolkit couldn't start its local helper. Another copy may already be open, "
                    "or a firewall or antivirus blocked it.\n\nClose other copies and try again.")
        return
    api = Api()
    WATCHER.start()
    try:
        window = webview.create_window(TITLE, f"http://127.0.0.1:{port}/index.html",
                                       js_api=api, width=1360, height=860, min_size=(1080, 700),
                                       background_color="#14100c")
        ST.window = window
        # in-game window: hidden until F7 is pressed in the game
        global OVERLAY
        OVERLAY = overlay_mod.Overlay(lambda: _mod_folder(api))
        over = webview.create_window(overlay_mod.Overlay.TITLE, f"http://127.0.0.1:{port}/overlay.html",
                                     js_api=api, width=1180, height=760, frameless=True, on_top=True,
                                     hidden=True, background_color="#100e0c")
        webview.start(lambda: OVERLAY.start(over), debug="--debug" in sys.argv)
    except Exception:
        log_crash("window")
        message_box("Dragonwilds Toolkit couldn't open its window.\n\n"
                    "Most often Microsoft Edge WebView2 is missing or broken. Reinstall it from:\n" + WEBVIEW2_PAGE +
                    "\n\nDetails were saved to:\n" + os.path.join(core.APP_DIR, "errors.log"))


if __name__ == "__main__":
    main()
