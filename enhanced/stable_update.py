"""Stage verified Windows releases for activation when the MCP client reconnects."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sys
import tempfile
import time
import urllib.request
import zipfile

REPO = "kyrosdev/foundry-mcp"
FEED = f"https://api.github.com/repos/{REPO}/releases/latest"
MAX_PACKAGE = 80_000_000
VERSION = re.compile(r"\d{1,6}\.\d{1,6}\.\d{1,6}\Z")
REQUIRED = {
    "enhanced/foundry-bootstrap.exe", "enhanced/server.py",
    "enhanced/bootstrap_catalog.json", "enhanced/workbench_catalog.json",
    "enhanced/blender_bridge.py", "enhanced/blender_addon/__init__.py",
}
ROOT = Path(__file__).resolve().parents[1]


def version(value):
    if not isinstance(value, str) or not VERSION.fullmatch(value):
        raise ValueError("Expected a stable version number")
    return tuple(map(int, value.split(".")))


def atomic_text(path, value):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
        file.write(value)
        temp = Path(file.name)
    try:
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def release_asset(release):
    tag = release["tag_name"]
    target = tag.removeprefix("v")
    version(target)
    if tag != "v" + target or release.get("draft") or release.get("prerelease"):
        raise ValueError("The update feed did not return a stable release")
    name = f"Foundry-MCP-{target}-windows-x64.zip"
    expected_url = f"https://github.com/{REPO}/releases/download/{tag}/{name}"
    for asset in release.get("assets", []):
        digest = asset.get("digest") or ""
        size = asset.get("size")
        if (asset.get("name") == name and asset.get("browser_download_url") == expected_url
                and re.fullmatch(r"sha256:[a-f0-9]{64}", digest) and type(size) is int
                and 0 < size <= MAX_PACKAGE):
            return target, expected_url, digest[7:], size
    raise ValueError("Stable Windows package with GitHub SHA-256 metadata is missing")


def download(url, maximum):
    req = urllib.request.Request(url, headers={"User-Agent": "Foundry-MCP-Stable"})
    with urllib.request.urlopen(req, timeout=20) as stream:
        body = stream.read(maximum + 1)
    if len(body) > maximum:
        raise ValueError("Update response exceeds size limit")
    return body


def stage(root, current, release, fetch=download):
    target, url, digest, size = release_asset(release)
    updates = root / ".foundry-updates"
    pointer = updates / "active"
    try:
        installed = pointer.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        installed = current
    if version(target) <= max(version(current), version(installed)):
        return {"status": "up_to_date", "version": installed}
    payload = fetch(url, size)
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise ValueError("Release package SHA-256 or size does not match GitHub")
    updates.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=updates) as temporary:
        directory = Path(temporary)
        archive = directory / "package.zip"
        archive.write_bytes(payload)
        tree = directory / "tree"
        tree.mkdir()
        with zipfile.ZipFile(archive) as package:
            entries = package.infolist()
            if len(entries) > 400 or sum(e.file_size for e in entries) > 160_000_000:
                raise ValueError("Release archive exceeds extraction limits")
            names = set()
            folded = set()
            for entry in entries:
                raw = entry.filename.removesuffix("/")
                parts = PurePosixPath(raw).parts
                if (not parts or parts[0] != "Foundry-MCP" or "\\" in raw
                        or any(part in ("", ".", "..") or ":" in part or part.endswith((".", " "))
                               or not re.fullmatch(r"[A-Za-z0-9_. -]+", part)
                               or re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])", part.split(".")[0])
                               for part in raw.split("/"))):
                    raise ValueError("Unexpected path in release archive")
                if entry.is_dir():
                    continue
                relative = "/".join(parts[1:])
                if (not relative or relative.casefold() in folded or relative.startswith(".")
                        or (entry.external_attr >> 16) & 0o170000 == 0o120000):
                    raise ValueError("Duplicate or unsafe release entry")
                names.add(relative)
                folded.add(relative.casefold())
                path = tree.joinpath(*parts[1:])
                path.parent.mkdir(parents=True, exist_ok=True)
                with package.open(entry) as source, path.open("xb") as destination:
                    shutil.copyfileobj(source, destination)
            if not REQUIRED <= names:
                raise ValueError("Release package is incomplete")
        final = updates / target
        try:
            tree.rename(final)
        except OSError:
            if not final.is_dir() or not (final / "enhanced/foundry-bootstrap.exe").is_file():
                raise
        atomic_text(pointer, target)
    return {"status": "staged", "version": target, "effective": "next_connection"}


@contextmanager
def update_lock(path):
    # The operating system drops this lock if the process exits mid-update.
    with path.open("a+b") as file:
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
        else:
            import fcntl
            try:
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
        yield True


def check(root, current, *, force=False):
    updates = root / ".foundry-updates"
    updates.mkdir(parents=True, exist_ok=True)
    status = updates / "status.json"
    try:
        if not force and time.time() - status.stat().st_mtime < 86400:
            return json.loads(status.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    with update_lock(updates / "checking.lock") as acquired:
        if not acquired:
            return {"status": "checking"}
        try:
            result = stage(root, current, json.loads(download(FEED, 1_000_000)))
        except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
            result = {"status": "error", "error": str(error)}
        atomic_text(status, json.dumps(result))
        return result


if __name__ == "__main__":
    from version import VERSION as CURRENT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check-now", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ.get("FOUNDRY_INSTALL_ROOT", ROOT))
    print(json.dumps(check(root, CURRENT, force=args.check_now)))
