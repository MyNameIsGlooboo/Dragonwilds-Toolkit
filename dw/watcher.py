"""Snapshot every save the game writes, while the editor is open.

Polls the save folders every few seconds. When a .json or .sav file changes and
then stays unchanged for a few seconds (the game finished writing), it is copied
into the backups folder with reason "game". The newest MAX_GAME_SNAPSHOTS are kept
per save; manual and pre-edit backups are never touched.
"""
import os
import shutil
import threading
import time

from . import core

MAX_GAME_SNAPSHOTS = 50
POLL_SECONDS = 4
SETTLE_SECONDS = 6


class SaveWatcher:
    def __init__(self):
        self.enabled = True
        self.last_snapshot = {}   # path -> (mtime, size) already backed up
        self.pending = {}         # path -> (mtime, size, first seen)
        self.count = 0
        self.ignore_until = 0     # skip our own writes
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        # Don't snapshot what is already there; only new game saves.
        for p in self._files():
            st = os.stat(p)
            self.last_snapshot[p] = (st.st_mtime, st.st_size)
        self._thread.start()

    def pause_for(self, seconds=10):
        """Called after the editor itself writes a save."""
        self.ignore_until = time.time() + seconds

    def _files(self):
        out = []
        for d, ext in ((core.SAVE_DIR, ".json"), (core.WORLD_DIR, ".sav")):
            if os.path.isdir(d):
                out += [os.path.join(d, f) for f in os.listdir(d) if f.lower().endswith(ext)]
        return out

    def _run(self):
        while True:
            time.sleep(POLL_SECONDS)
            if not self.enabled:
                continue
            try:
                self._tick()
            except Exception:
                pass  # never let the watcher take the app down

    def _tick(self):
        now = time.time()
        for p in self._files():
            try:
                st = os.stat(p)
            except OSError:
                continue
            sig = (st.st_mtime, st.st_size)
            if self.last_snapshot.get(p) == sig:
                continue
            if now < self.ignore_until:
                self.last_snapshot[p] = sig
                continue
            prev = self.pending.get(p)
            if not prev or prev[:2] != sig:
                self.pending[p] = (sig[0], sig[1], now)
            elif now - prev[2] >= SETTLE_SECONDS:
                self._snapshot(p)
                self.last_snapshot[p] = sig
                self.pending.pop(p, None)

    def _snapshot(self, path):
        key = core.backup_key(path)
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        dest = os.path.join(core.BACKUP_DIR, key, f"{stamp}_game")
        os.makedirs(dest, exist_ok=True)
        for p in (path, path + ".backup"):
            if os.path.isfile(p):
                shutil.copy2(p, dest)
        self.count += 1
        folder = os.path.join(core.BACKUP_DIR, key)
        games = sorted(d for d in os.listdir(folder) if d.endswith("_game"))
        for d in games[:-MAX_GAME_SNAPSHOTS]:
            shutil.rmtree(os.path.join(folder, d), ignore_errors=True)
