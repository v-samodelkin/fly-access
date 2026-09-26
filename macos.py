#!/usr/bin/env python3
"""Install a launchd WireGuard tunnel with domain-scoped macOS resolvers."""
import argparse
import os
from pathlib import Path
import plistlib
import pwd
import re
import socket
import subprocess
import sys
import time

from common import parse_profile, private_write, render_profile, trash
from layout import LABEL, MANAGED, STATE, PROFILE, PLIST, TUNNEL_NAME, device_profile, profile_directory

DOMAINS = ("flycast", "internal")
RESOLVERS = Path("/etc/resolver")


def default_directory():
    home = Path(pwd.getpwnam(os.environ["SUDO_USER"]).pw_dir) if os.environ.get("SUDO_USER") else Path.home()
    return profile_directory(home)


def brew_bin():
    for folder in (Path("/opt/homebrew/bin"), Path("/usr/local/bin")):
        if all((folder / name).exists() for name in ("wg-quick", "wg", "wireguard-go", "bash")):
            return folder
    raise RuntimeError("Run: brew install wireguard-tools")


def launch_plist(folder):
    return {"Label": LABEL, "ProgramArguments": [str(folder / "wg-quick"), "up", str(PROFILE)],
            "EnvironmentVariables": {"PATH": f"{folder}:/usr/bin:/bin:/usr/sbin:/sbin"},
            "RunAtLoad": True, "KeepAlive": True, "ThrottleInterval": 30,
            "StandardOutPath": str(STATE / "tunnel.log"), "StandardErrorPath": str(STATE / "tunnel.log")}


def checked(argv):
    result = subprocess.run(argv, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"{Path(argv[0]).name} failed; see {STATE / 'tunnel.log'}")
    return result.stdout


def preflight():
    if STATE.is_symlink() or PLIST.is_symlink() or RESOLVERS.is_symlink():
        raise RuntimeError("Refusing existing symlinks in system configuration")
    if STATE.exists() and not (STATE / ".managed").exists():
        raise RuntimeError("Existing state directory is not owned by this installer")
    if (Path("/var/run/wireguard") / (TUNNEL_NAME + ".name")).exists() and not (STATE / ".managed").exists():
        raise RuntimeError("An unmanaged tunnel with this name already exists")
    if PLIST.exists() and (not (STATE / ".managed").exists() or plistlib.loads(PLIST.read_bytes()).get("Label") != LABEL):
        raise RuntimeError("Existing launchd job is not owned by this installer")
    for domain in DOMAINS:
        path = RESOLVERS / domain
        if path.is_symlink() or (path.exists() and not path.read_text().startswith(MANAGED + "\n")):
            raise RuntimeError(f"Existing resolver for {domain}; refusing to overwrite it")


def stop(folder):
    active = subprocess.run(["launchctl", "print", f"system/{LABEL}"], capture_output=True).returncode == 0
    if active:
        checked(["launchctl", "bootout", f"system/{LABEL}"])
        time.sleep(1)
    # Clean up a leftover interface if launchd already stopped the job's processes.
    namefile = Path("/var/run/wireguard") / (TUNNEL_NAME + ".name")
    if namefile.exists() and PROFILE.exists():
        subprocess.run([str(folder / "wg-quick"), "down", str(PROFILE)], capture_output=True)


def install(args):
    folder = brew_bin()
    config = parse_profile(device_profile(args.directory, "macos").read_text())
    preflight()  # Validate everything before modifying system files.
    dns = config["Interface"]["dns"]
    expected = render_profile(config, split=True)
    if args.dry_run:
        print("Valid private /48 profile; no global DNS, hooks or default route.")
        print("Would install launchd job and resolvers for .flycast and .internal only.")
        return
    stop(folder)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.chmod(0o700)
    private_write(STATE / ".managed", MANAGED + "\n")
    private_write(PROFILE, expected)
    RESOLVERS.mkdir(mode=0o755, exist_ok=True)
    for domain in DOMAINS:
        path = RESOLVERS / domain
        private_write(path, f"{MANAGED}\nnameserver {dns}\nport 53\ntimeout 2\n")
        path.chmod(0o644)
    private_write(PLIST, plistlib.dumps(launch_plist(folder)))
    PLIST.chmod(0o644)
    if not (STATE / "tunnel.log").exists():
        private_write(STATE / "tunnel.log", "")
    subprocess.run(["dscacheutil", "-flushcache"], check=True)
    checked(["launchctl", "enable", f"system/{LABEL}"])
    checked(["launchctl", "bootstrap", "system", str(PLIST)])
    for _ in range(20):
        namefile = Path("/var/run/wireguard") / (TUNNEL_NAME + ".name")
        if namefile.exists():
            interface = namefile.read_text().strip()
            result = subprocess.run([str(folder / "wg"), "show", interface, "latest-handshakes"], capture_output=True, text=True)
            if result.returncode == 0 and any(int(line.split()[-1]) > 0 for line in result.stdout.splitlines()):
                print("Installed: launchd autostart, private route, split DNS; handshake confirmed.")
                return
        time.sleep(1)
    raise RuntimeError("Installed, but handshake not confirmed. Check tunnel.log; no global DNS was changed.")


def turn_off():
    preflight()
    if not (STATE / ".managed").exists():
        raise RuntimeError("Install the tunnel first")
    # Disable persists across reboot; merely stopping a KeepAlive job would restart it.
    checked(["launchctl", "disable", f"system/{LABEL}"])
    stop(brew_bin())
    for domain in DOMAINS:
        path = RESOLVERS / domain
        if path.exists():
            path.replace(STATE / f"resolver-{domain}.disabled")
    subprocess.run(["dscacheutil", "-flushcache"], check=True)
    print("VPN and split DNS disabled; they remain off after reboot. Profiles retained.")


def uninstall():
    preflight()
    stop(brew_bin())
    targets = [PLIST, STATE] + [RESOLVERS / domain for domain in DOMAINS]
    for path in targets:
        if path.exists():
            trash(path)
    subprocess.run(["dscacheutil", "-flushcache"], check=True)
    print("Removed local tunnel and managed resolvers. Private backups and Fly peers retained.")


def check(args):
    config = parse_profile(device_profile(args.directory, "macos").read_text())
    dns = config["Interface"]["dns"]
    dump = checked(["scutil", "--dns"])
    # A Fly resolver must always be scoped to one of our domains, never default.
    for block in dump.split("resolver #")[1:]:
        if dns in block and not any(f"domain   : {domain}\n" in block for domain in DOMAINS):
            raise RuntimeError("Fly DNS also appears outside the expected scoped resolvers")
    for domain in DOMAINS:
        if not any(dns in block and f"domain   : {domain}\n" in block for block in dump.split("resolver #")):
            raise RuntimeError(f"Missing system resolver for {domain}")
    routes = {}
    for host in ("example.com", args.host):
        records = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
        print(f"System DNS OK: {host}")
        family = socket.AF_INET6 if host == args.host else socket.AF_INET
        address = next(record[4][0] for record in records if record[0] == family)
        route = checked(["route", "-n", "get", "-inet6" if family == socket.AF_INET6 else "-inet", address])
        match = re.search(r"interface:\s*(\S+)", route)
        if not match:
            raise RuntimeError("Cannot determine destination interface")
        routes[host] = match.group(1)
    if not routes[args.host].startswith("utun") or routes[args.host] == routes["example.com"]:
        raise RuntimeError("Expected private traffic in WireGuard and public traffic outside it")
    if args.port:
        with socket.create_connection((args.host, args.port), timeout=60):
            print("Private TCP connection OK (service authentication not tested)")
    print("Split DNS and private route verified; default resolver remains outside Fly.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "on", "off", "status", "check", "uninstall"))
    parser.add_argument("--directory", type=Path, default=default_directory())
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--host", help="Existing private app hostname, e.g. my-app.flycast")
    parser.add_argument("--port", type=int, help="Optional TCP readiness check")
    args = parser.parse_args()
    try:
        if sys.platform != "darwin":
            raise RuntimeError("This installer is for macOS only")
        if args.action in ("install", "on", "off", "uninstall") and not args.dry_run and os.geteuid() != 0:
            raise RuntimeError("Run with sudo; macOS needs administrator access for routes and launchd")
        if args.action == "check" and not args.host:
            parser.error("check requires --host")
        if args.dry_run and args.action not in ("install", "on"):
            parser.error("--dry-run is supported only for install/on")
        if args.action == "status":
            import json
            from macos_status import get_status
            print(json.dumps(get_status(args.directory), ensure_ascii=False))
        else:
            {"install": lambda: install(args), "on": lambda: install(args),
             "off": turn_off, "check": lambda: check(args), "uninstall": uninstall}[args.action]()
    except (ValueError, RuntimeError, OSError, StopIteration) as error:
        parser.exit(1, f"{error}\n")
