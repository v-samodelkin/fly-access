"""Unprivileged live status. DNS probes do not start any Fly Machines."""
from pathlib import Path
import re
import subprocess

from common import parse_profile
from layout import LABEL, MANAGED, device_profile

MARKER = MANAGED + "\n"


def autostart_enabled(output):
    return not re.search(r'"' + re.escape(LABEL) + r'"\s*=>\s*(?:true|disabled)\b', output)


def command(argv):
    try:
        result = subprocess.run(argv, capture_output=True, text=True, timeout=4)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def classify(running, configured, dns_scoped, routed, reachable, enabled=True):
    if not running and not configured:
        return {"state": "off", "title": "Disconnected", "detail": "Private network disconnected",
                "action": "on", "autostart": enabled}
    if running and configured and dns_scoped and routed and reachable:
        return {"state": "on", "title": "Connected", "detail": "Private network is ready",
                "action": "off", "autostart": enabled}
    if not running:
        detail = "VPN is stopped; DNS settings remain"
    elif not configured or not dns_scoped:
        detail = "DNS settings need repair"
    elif not routed:
        detail = "Private route is unavailable"
    else:
        detail = "Fly DNS is not reachable"
    return {"state": "error", "title": "Connection issue", "detail": detail,
            "action": "off", "autostart": enabled}


def get_status(directory):
    code, job = command(["/bin/launchctl", "print", f"system/{LABEL}"])
    running = code == 0 and "state = running" in job
    files = [Path("/etc/resolver") / domain for domain in ("flycast", "internal")]
    contents = [path.read_text() if path.is_file() else "" for path in files]
    configured = all(text.startswith(MARKER) for text in contents)
    _, disabled = command(["/bin/launchctl", "print-disabled", "system"])
    enabled = autostart_enabled(disabled)
    if not running:
        return classify(False, any(contents), False, False, False, bool(enabled))
    config = parse_profile(device_profile(directory, "macos").read_text())
    dns = config["Interface"]["dns"]
    _, resolvers = command(["/usr/sbin/scutil", "--dns"])
    blocks = resolvers.split("resolver #")[1:]
    scoped = all(any(dns in block and f"domain   : {domain}\n" in block for block in blocks)
                 for domain in ("flycast", "internal"))
    scoped = scoped and not any(dns in block and not any(f"domain   : {domain}\n" in block
                          for domain in ("flycast", "internal")) for block in blocks)
    _, route = command(["/sbin/route", "-n", "get", "-inet6", dns])
    routed = "interface: utun" in route
    reachable = False
    if routed and scoped:
        code, response = command(["/usr/bin/dig", f"@{dns}", "_apps.internal", "TXT",
                                  "+time=1", "+tries=1", "+noall", "+comments"])
        reachable = code == 0 and "status: NOERROR" in response
    return classify(running, configured, scoped, routed, reachable, bool(enabled))
