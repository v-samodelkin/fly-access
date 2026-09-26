"""Strict Fly WireGuard profiles; never print configuration or subprocess errors."""
import base64
import configparser
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import shutil
from layout import profile_directory

REPO = Path(__file__).resolve().parent
DEFAULT_DIR = profile_directory(Path.home())


def run_private(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{Path(str(argv[0])).name} failed ({result.returncode}); output hidden to protect keys")
    return result.stdout


def private_write(path, data):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Refusing to write through a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(data.encode() if isinstance(data, str) else data)


def external_directory(path):
    path = Path(path).expanduser().resolve()
    if path == REPO or REPO in path.parents:
        raise ValueError("Private profiles must be outside the repository")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.stat().st_mode & 0o077:
        raise ValueError("Private directory needs mode 700")
    return path


def parse_profile(text):
    config = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=("#",))
    try:
        config.read_string(text)
    except configparser.Error:
        raise ValueError("Invalid profile syntax") from None
    fields = {
        "Interface": {"privatekey", "address", "dns", "mtu"},
        "Peer": {"publickey", "allowedips", "endpoint", "persistentkeepalive"},
    }
    if config.defaults() or set(config.sections()) != set(fields):
        raise ValueError("Expected exactly one Interface and one Peer")
    for section, allowed in fields.items():
        if set(config[section]) - allowed:
            raise ValueError("Unsupported WireGuard settings (hooks and default routes are forbidden)")
    interface, peer = config["Interface"], config["Peer"]
    try:
        for value in (interface["privatekey"], peer["publickey"]):
            if len(base64.b64decode(value, validate=True)) != 32:
                raise ValueError()
        subnet = ipaddress.IPv6Network(peer["allowedips"])
        address = ipaddress.IPv6Interface(interface["address"])
        dns = ipaddress.IPv6Address(interface["dns"])
        if subnet.prefixlen != 48 or not subnet.subnet_of(ipaddress.IPv6Network("fdaa::/16")):
            raise ValueError()
        if address.ip not in subnet or dns not in subnet:
            raise ValueError()
        if not re.fullmatch(r"[A-Za-z0-9._:\[\]-]+:[0-9]{1,5}", peer["endpoint"]):
            raise ValueError()
        if not 1 <= int(peer["endpoint"].rsplit(":", 1)[1]) <= 65535:
            raise ValueError()
        if not 0 <= int(peer.get("persistentkeepalive", "25")) <= 65535:
            raise ValueError()
        if not 1280 <= int(interface.get("mtu", "1280")) <= 9000:
            raise ValueError()
    except (KeyError, ValueError):
        raise ValueError("Expected a valid Fly /48 profile with matching address and DNS") from None
    return config


def render_profile(config, split=False):
    interface, peer = config["Interface"], config["Peer"]
    lines = ["[Interface]", f"PrivateKey = {interface['privatekey']}", f"Address = {interface['address']}"]
    if not split:
        lines.append(f"DNS = {interface['dns']}")
    lines += ["MTU = 1280", "", "[Peer]", f"PublicKey = {peer['publickey']}",
              f"AllowedIPs = {peer['allowedips']}", f"Endpoint = {peer['endpoint']}",
              "PersistentKeepalive = 25", ""]
    return "\n".join(lines)


def trash(path):
    executable = shutil.which("trash") or next((str(p) for p in (Path("/opt/homebrew/bin/trash"), Path("/usr/local/bin/trash")) if p.is_file()), None)
    if not executable:
        raise RuntimeError("Install trash first: brew install trash")
    subprocess.run([executable, str(path)], check=True)
