"""In-game overlay: the toolkit's own window shown over the game when you press F7 there.

The cheat mod and the toolkit talk through two small files in the mod folder:
  toolkit.txt   toolkit -> mod   a timestamp every 2 s, so the mod knows the toolkit is running
  overlay.txt   both ways        "open" or "closed"; the mod writes "open" on F7 (and pauses the game),
                                 the toolkit writes "closed" when the window is closed (the mod resumes)
"""
import ctypes
import ctypes.wintypes as wt
import os
import threading
import time

GAME_EXE = "rsdragonwilds-win64-shipping.exe"
user32 = ctypes.windll.user32 if os.name == "nt" else None
kernel32 = ctypes.windll.kernel32 if os.name == "nt" else None


def _process_name(pid):
    h = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(512)
        if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        kernel32.CloseHandle(h)


def game_window():
    """The game's main window handle, or None."""
    if not user32:
        return None
    found = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            pid = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if _process_name(pid.value) == GAME_EXE:
                found.append(hwnd)
                return False
        return True

    user32.EnumWindows(cb, 0)
    return found[0] if found else None


def window_rect(hwnd):
    r = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right - r.left, r.bottom - r.top


def focus(hwnd):
    if user32 and hwnd:
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
        user32.SetForegroundWindow(hwnd)


def find_window(title):
    return user32.FindWindowW(None, title) if user32 else None


class Overlay:
    """Keeps toolkit.txt fresh and shows/hides the overlay window when overlay.txt changes."""

    TITLE = "Dragonwilds Toolkit - in game"

    def __init__(self, mod_dir_fn):
        self.mod_dir_fn = mod_dir_fn   # returns the mod folder, or None when cheats aren't installed
        self.window = None
        self.shown = False
        self._stop = False

    def _path(self, name):
        d = self.mod_dir_fn()
        return os.path.join(d, name) if d and os.path.isdir(d) else None

    def state(self):
        p = self._path("overlay.txt")
        try:
            with open(p, encoding="utf-8") as f:
                return f.readline().strip()
        except (OSError, TypeError):
            return None

    def close(self):
        p = self._path("overlay.txt")
        if p:
            with open(p, "w", encoding="utf-8") as f:
                f.write("closed\n")

    def start(self, window):
        self.window = window
        threading.Thread(target=self._run, daemon=True).start()

    def _show(self):
        game = game_window()
        if game:
            x, y, w, h = window_rect(game)
            ow, oh = min(1180, max(900, int(w * 0.8))), min(780, max(620, int(h * 0.82)))
            try:
                self.window.resize(ow, oh)
                self.window.move(x + (w - ow) // 2, y + (h - oh) // 2)
            except Exception:
                pass
        self.window.show()
        try:
            self.window.evaluate_js("window.overlayShown && overlayShown()")
        except Exception:
            pass
        focus(find_window(self.TITLE))
        self.shown = True

    def _hide(self):
        self.window.hide()
        self.shown = False
        focus(game_window())

    def _run(self):
        last_beat = 0
        while not self._stop:
            now = time.time()
            if now - last_beat > 2:
                p = self._path("toolkit.txt")
                if p:
                    try:
                        with open(p, "w", encoding="utf-8") as f:
                            f.write(f"{int(now)}\n")
                    except OSError:
                        pass
                last_beat = now
            st = self.state()
            try:
                if st == "open" and not self.shown:
                    self._show()
                elif st != "open" and self.shown:
                    self._hide()
            except Exception:
                pass
            time.sleep(0.15)
