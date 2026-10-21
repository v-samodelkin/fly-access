#!/usr/bin/env python3
"""Check GitHub for a newer Fly Access release, or update the local checkout to one.

`check` runs from the menu and only asks the GitHub API for the latest release.
`apply` runs in Terminal: it fast-forwards this checkout to the release tag,
rebuilds the menu, and reinstalls the tunnel only when its files changed.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

REPOSITORY = "v-samodelkin/fly-access"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
SOURCE = Path(__file__).resolve().parent
TAG = re.compile(r"v(\d+)\.(\d+)(?:\.(\d+))?")
# A change in these files also needs `sudo macos.py install`; everything else ships with the menu.
TUNNEL_FILES = ("macos.py", "common.py", "layout.py")


def version(text):
    match = TAG.fullmatch(text if text.startswith("v") else "v" + text)
    if not match:
        raise ValueError("Unexpected version")
    return tuple(int(part or 0) for part in match.groups())


def latest_release():
    try:
        result = subprocess.run(["/usr/bin/curl", "-fsSL", "--max-time", "15", "-H", "Accept: application/vnd.github+json",
                                 LATEST], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Unable to reach GitHub") from None
    if result.returncode:
        raise RuntimeError("Unable to reach GitHub")
    try:
        data = json.loads(result.stdout)
        tag, url = data["tag_name"], data["html_url"]
        version(tag)
        if not url.startswith(f"https://github.com/{REPOSITORY}/releases/"):
            raise ValueError()
    except (TypeError, KeyError, ValueError, AttributeError):
        raise RuntimeError("Unexpected response from GitHub") from None
    return tag, url


def check(current):
    tag, url = latest_release()
    return {"ok": True, "current": current, "tag": tag, "url": url, "available": version(tag) > version(current)}


def git(*args):
    return subprocess.run(["git", "-C", str(SOURCE), *args], check=True, capture_output=True, text=True).stdout.strip()


def elevate(argv):
    subprocess.run(["sudo", *argv], check=True)


def apply(tag, directory):
    version(tag)
    previous = git("rev-parse", "HEAD")
    print(f"Updating {SOURCE} to {tag}...")
    git("fetch", "--quiet", "--tags", "origin")
    git("merge", "--ff-only", "--quiet", f"refs/tags/{tag}")
    subprocess.run([sys.executable, str(SOURCE / "install-menu.py")], check=True)
    if subprocess.run(["git", "-C", str(SOURCE), "diff", "--quiet", previous, "HEAD", "--", *TUNNEL_FILES]).returncode:
        macos = [sys.executable, "-B", str(SOURCE / "macos.py")]
        status = subprocess.run(macos + ["status", "--directory", str(directory)], capture_output=True, text=True)
        try:
            connected = json.loads(status.stdout).get("state") == "on"
        except ValueError:
            connected = False
        if connected:
            print("This release also updates the tunnel. macOS asks for your password; the VPN reconnects.")
            elevate(macos + ["install", "--directory", str(directory)])
        else:
            print("This release also updates the tunnel. Choose Connect in the menu to apply it.")
    print(f"Fly Access is now {tag[1:]}.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("check").add_argument("--current", required=True)
    update = commands.add_parser("apply")
    update.add_argument("tag")
    update.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "check":
        try:
            result = check(args.current)
        except (RuntimeError, ValueError) as error:
            result = {"ok": False, "error": str(error)}
        print(json.dumps(result))
        return 0 if result["ok"] else 1
    try:
        apply(args.tag, args.directory)
    except subprocess.CalledProcessError as error:
        details = (error.stderr or "").strip()
        print("Update failed" + (": " + details if details else "."), file=sys.stderr)
        return 1
    except ValueError as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
