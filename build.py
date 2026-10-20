"""Build the release: syntax-check the UI, build the exe, and pack the zip on the Desktop.

Run with:  .venv\\Scripts\\python.exe build.py
The version comes from dw/version.py. Close the toolkit first (Windows locks a running exe).
"""
import os
import shutil
import subprocess
import sys
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from dw.version import APP_NAME, VERSION  # noqa: E402

EXE_NAME = f"DragonwildsToolkit-v{VERSION}"
ZIP_PATH = os.path.join(os.environ.get("DW_RELEASE_DIR") or os.path.dirname(ROOT), f"Dragonwilds-Toolkit-v{VERSION}.zip")
HIDDEN = ["clr", "dw.mapdata", "dw.unlocks", "dw.capacity", "dw.cheats", "dw.features", "dw.watcher", "dw.version"]


def check_ui():
    """A single bad quote in a script blanks a whole tab, so check every UI script first."""
    for name in sorted(os.listdir(os.path.join(ROOT, "ui"))):
        if name.endswith(".js"):
            path = os.path.join(ROOT, "ui", name)
            code = f"new Function(require('fs').readFileSync({path!r}, 'utf8'))"
            subprocess.run(["node", "-e", code], check=True)
    # All scripts share one page, so two files declaring the same name break the later one
    import re
    html = open(os.path.join(ROOT, "ui", "index.html"), encoding="utf-8").read()
    order = re.findall(r'<script src="([^"]+)"', html)
    joined = "\n;\n".join(open(os.path.join(ROOT, "ui", n), encoding="utf-8").read().replace('"use strict";', "", 1)
                          for n in order)
    tmp = os.path.join(ROOT, "build", "all-scripts.js")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(joined)
    subprocess.run(["node", "-e", f"new Function(require('fs').readFileSync({tmp!r}, 'utf8'))"], check=True)
    print("UI scripts OK")


def version_file():
    """Windows file properties (right-click the exe > Properties > Details)."""
    nums = tuple(int(x) for x in VERSION.split(".")) + (0,)
    text = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('ProductName', '{APP_NAME}'),
    StringStruct('FileDescription', '{APP_NAME}'),
    StringStruct('FileVersion', '{VERSION}'),
    StringStruct('ProductVersion', '{VERSION}'),
    StringStruct('OriginalFilename', '{EXE_NAME}.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
"""
    path = os.path.join(ROOT, "build", "version_info.txt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def fetch_vendor():
    """UE4SS (MIT licence) is downloaded from its GitHub release and checked, not kept in the repo."""
    from dw import cheats, core
    os.makedirs(core.VENDOR_DIR, exist_ok=True)
    target = os.path.join(core.VENDOR_DIR, cheats.UE4SS_FILE)
    if not os.path.isfile(target):
        shutil.copy2(cheats.ue4ss_zip(print), target)
    print("vendor:", ", ".join(sorted(os.listdir(core.VENDOR_DIR))))


def build_exe():
    args = [os.path.join(ROOT, ".venv", "Scripts", "python.exe"), "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
            "--name", EXE_NAME, "--version-file", version_file(),
            "--add-data", "ui;ui", "--add-data", "dw\\cheatmod;dw\\cheatmod", "--add-data", "vendor;vendor",
            "--collect-all", "webview"]
    for h in HIDDEN:
        args += ["--hidden-import", h]
    subprocess.run(args + ["app.py"], cwd=ROOT, check=True)
    exe = os.path.join(ROOT, "dist", EXE_NAME + ".exe")
    # keep only this version's exe in dist
    for name in os.listdir(os.path.join(ROOT, "dist")):
        if name.endswith(".exe") and name != EXE_NAME + ".exe":
            try:
                os.remove(os.path.join(ROOT, "dist", name))
            except OSError:
                print("Kept (still open):", name)
    return exe


def pack(exe):
    with open(os.path.join(ROOT, "release-files", "README.txt"), encoding="utf-8") as f:
        readme = f.read().replace("{VERSION}", VERSION).replace("{EXE}", EXE_NAME + ".exe")
    with zipfile.ZipFile(ZIP_PATH, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(exe, os.path.basename(exe))
        z.writestr("README.txt", readme.replace("\n", "\r\n"))
    print("Zip:", ZIP_PATH)


if __name__ == "__main__":
    check_ui()
    fetch_vendor()
    exe = build_exe()
    print("Exe:", exe)
    pack(exe)
