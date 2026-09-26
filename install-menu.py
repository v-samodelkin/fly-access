#!/usr/bin/env python3
"""Build and install the local Fly menu bar app and login LaunchAgent (no sudo)."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile

from common import DEFAULT_DIR, trash
from layout import LEGACY_MENU_LABEL

LABEL = "io.flyaccess.menu"
SOURCE = Path(__file__).resolve().parent


def install(directory=None):
    if sys.platform != "darwin":
        raise SystemExit("macOS only")
    app = Path.home() / "Applications/Fly Access.app"
    agent = Path.home() / "Library/LaunchAgents" / f"{LABEL}.plist"
    target = f"gui/{os.getuid()}/{LABEL}"
    fly = shutil.which("fly") or shutil.which("flyctl")
    if not fly:
        raise SystemExit("Install Fly CLI and run fly auth login before installing the menu")
    previous = {}
    if app.exists():
        info = app / "Contents/Info.plist"
        if not info.exists() or plistlib.loads(info.read_bytes()).get("CFBundleIdentifier") not in (LABEL, LEGACY_MENU_LABEL):
            raise SystemExit("Existing app is not managed by this installer")
        previous = plistlib.loads(info.read_bytes())
    directory = directory or Path(previous.get("ProfileDirectory", DEFAULT_DIR))
    with tempfile.TemporaryDirectory(prefix="fly-menu-") as tmp:
        bundle = Path(tmp) / "Fly Access.app"
        contents = bundle / "Contents"
        (contents / "MacOS").mkdir(parents=True)
        resources = contents / "Resources"
        resources.mkdir()
        subprocess.run(["/usr/bin/swiftc", "-swift-version", "5", "-O", "-framework", "Cocoa",
                        str(SOURCE / "FlyMenu.swift"), "-o", str(contents / "MacOS/FlyMenu")], check=True)
        for filename in ("macos.py", "macos_status.py", "fly_apps.py", "common.py", "layout.py"):
            shutil.copy2(SOURCE / filename, resources / filename)
        info = {"CFBundleIdentifier": LABEL, "CFBundleName": "Fly Access", "CFBundleExecutable": "FlyMenu",
                "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "CFBundleShortVersionString": "1.0",
                "LSUIElement": True, "NSHighResolutionCapable": True,
                "PythonPath": sys.executable, "FlyPath": fly, "ProfileDirectory": str(directory.expanduser().resolve())}
        (contents / "Info.plist").write_bytes(plistlib.dumps(info))
        subprocess.run(["/usr/bin/codesign", "--force", "--sign", "-", str(bundle)], check=True)
        # Build succeeds before replacing or stopping an earlier copy.
        subprocess.run(["/bin/launchctl", "bootout", target], capture_output=True)
        if previous.get("CFBundleIdentifier") == LEGACY_MENU_LABEL:
            subprocess.run(["/bin/launchctl", "bootout", f"gui/{os.getuid()}/{LEGACY_MENU_LABEL}"], capture_output=True)
            old_agent = Path.home() / "Library/LaunchAgents" / (LEGACY_MENU_LABEL + ".plist")
            if old_agent.is_file() and not old_agent.is_symlink():
                if plistlib.loads(old_agent.read_bytes()).get("Label") == LEGACY_MENU_LABEL:
                    trash(old_agent)
        app.parent.mkdir(parents=True, exist_ok=True)
        if app.exists():
            trash(app)
        shutil.copytree(bundle, app)
    agent.parent.mkdir(parents=True, exist_ok=True)
    if agent.is_symlink():
        raise SystemExit("Refusing a symlink LaunchAgent")
    agent.write_bytes(plistlib.dumps({"Label": LABEL,
        "ProgramArguments": [str(app / "Contents/MacOS/FlyMenu")], "RunAtLoad": True,
        "ProcessType": "Interactive"}))
    agent.chmod(0o644)
    subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(agent)], check=True)
    print(f"Installed: {app}; menu starts at login. VPN state was not changed.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, help="Private profiles; reuses the installed app setting when omitted")
    install(parser.parse_args().directory)
