#!/usr/bin/env python3
"""Check GitHub for a newer Fly Access release, or update the local checkout to one.

`check` runs from the menu and only asks the GitHub API for the latest release.
`apply` runs as a one-off launchd job started by the menu: it fast-forwards this
checkout to the release tag, rebuilds the menu, and reports whether the tunnel changed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

REPOSITORY = "v-samodelkin/fly-access"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
UPSTREAM = f"https://github.com/{REPOSITORY}.git"
SOURCE = Path(__file__).resolve().parent
TAG = re.compile(r"v(\d+)\.(\d+)(?:\.(\d+))?")
# A change in these files also needs a reconnect (macos.py install); everything else ships with the menu.
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


def git(message, *args):
    result = subprocess.run(["git", "-C", str(SOURCE), *args], capture_output=True, text=True)
    if result.returncode:
        print(result.stderr.strip(), file=sys.stderr)  # Details go to the update log.
        raise RuntimeError(message)
    return result.stdout.strip()


def apply(tag):
    """Fast-forward the checkout to the tag and rebuild the menu; return whether the tunnel changed."""
    version(tag)
    previous = git("Unable to read the checkout", "rev-parse", "HEAD")
    print(f"Updating {SOURCE} to {tag}", flush=True)
    # Fetch from the public repository, so the checkout's remotes and SSH keys do not matter.
    git("Unable to download the release from GitHub", "fetch", "--quiet", "--no-tags", UPSTREAM,
        f"refs/tags/{tag}:refs/tags/{tag}")
    git("The checkout has local commits or edits; update it with git", "merge", "--ff-only", "--quiet", f"refs/tags/{tag}")
    if subprocess.run([sys.executable, str(SOURCE / "install-menu.py")]).returncode:
        raise RuntimeError("Unable to rebuild the menu")
    changed = subprocess.run(["git", "-C", str(SOURCE), "diff", "--quiet", previous, "HEAD", "--", *TUNNEL_FILES])
    return changed.returncode != 0


def save(path, outcome):
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(outcome))
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("check").add_argument("--current", required=True)
    update = commands.add_parser("apply")
    update.add_argument("tag")
    update.add_argument("--result", type=Path, required=True, help="JSON outcome for the menu")
    args = parser.parse_args()
    if args.action == "check":
        try:
            result = check(args.current)
        except (RuntimeError, ValueError) as error:
            result = {"ok": False, "error": str(error)}
        print(json.dumps(result))
        return 0 if result["ok"] else 1
    try:
        outcome = {"ok": True, "tunnel": apply(args.tag)}
    except (RuntimeError, ValueError) as error:
        outcome = {"ok": False, "error": str(error)}
    print(json.dumps(outcome))
    save(args.result, outcome)
    return 0 if outcome["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
