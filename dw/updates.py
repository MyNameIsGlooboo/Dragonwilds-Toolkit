"""Tell the user when a newer release is on GitHub. Notice only: nothing is downloaded or installed."""
import json
import os
import re
import urllib.request

from .version import REPO, USER_AGENT, VERSION

LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"


def version_tuple(text):
    """'v1.4.0' -> (1, 4, 0); anything unreadable -> ()"""
    m = re.match(r"^v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def latest_release(timeout=6):
    """The newest published release as {version, url, title, notes}, or None if GitHub can't be reached.
    DW_FAKE_LATEST=1.9.0 pretends a newer release exists (for testing the notice)."""
    fake = os.environ.get("DW_FAKE_LATEST")
    if fake:
        return {"version": fake.lstrip("v"), "url": RELEASES_PAGE + "/latest", "title": f"Dragonwilds Toolkit v{fake}",
                "notes": "Test release notes.\n- First change\n- Second change"}
    req = urllib.request.Request(LATEST_URL, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("draft") or data.get("prerelease"):
        return None
    url = data.get("html_url") or ""
    if not url.startswith(f"https://github.com/{REPO}/"):
        url = RELEASES_PAGE + "/latest"
    return {"version": (data.get("tag_name") or "").lstrip("v"), "url": url,
            "title": data.get("name") or data.get("tag_name") or "", "notes": (data.get("body") or "")[:4000]}


def check(skip_version=None):
    """{available, current, latest, url, title, notes} - available is True only for a newer, not-skipped version."""
    out = {"available": False, "current": VERSION}
    rel = latest_release()
    if not rel:
        return out
    out.update(latest=rel["version"], url=rel["url"], title=rel["title"], notes=rel["notes"])
    newer = version_tuple(rel["version"]) > version_tuple(VERSION)
    out["available"] = bool(newer and rel["version"] != (skip_version or ""))
    return out
