#!/usr/bin/env python3
"""Keep a managed /etc/hosts block with Flycast addresses read from Fly private DNS.

Without a global IPv6 address, macOS sends only A queries to scoped resolvers, and
Flycast names have only AAAA records. Hosts entries are answered regardless. Lookups go
to Fly's DNS server only, never to an app. launchd runs a root-owned copy of this file with
/usr/bin/python3 -I, so it stays self-contained and Python 3.9 compatible.
"""
import argparse
import ipaddress
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

HOSTS = Path("/etc/hosts")
BEGIN = "# BEGIN Fly Access: .flycast names, updated automatically"
END = "# END Fly Access"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")


def query(dns, questions):
    """Ask Fly DNS; return answers per question, or raise if any lookup did not complete."""
    argv = ["/usr/bin/dig", "@" + dns, "+time=3", "+tries=2", "+noall", "+comments", "+answer"]
    for name, rtype in questions:
        argv += [name, rtype]
    try:
        result = subprocess.run(argv, capture_output=True, text=True, stdin=subprocess.DEVNULL,
                                timeout=30 + 7 * len(questions))
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("Fly DNS lookup failed") from None
    sections = parse(result.stdout)
    if result.returncode or len(sections) != len(questions) or None in sections:
        raise RuntimeError("Fly DNS lookup failed")
    return [[data for owner, rtype, data in answers if owner == name.lower() and rtype == expected]
            for (name, expected), answers in zip(questions, sections)]


def parse(output):
    """Split dig output into answer lists per response; None marks a failed response."""
    sections = []
    for line in output.splitlines():
        header = re.search(r"->>HEADER<<-.*\bstatus: ([A-Z]+)", line)
        if header:
            sections.append([] if header.group(1) in ("NOERROR", "NXDOMAIN") else None)
        elif line.strip() and not line.startswith(";") and sections and sections[-1] is not None:
            fields = line.split(None, 4)
            if len(fields) == 5 and fields[2] == "IN":
                sections[-1].append((fields[0].rstrip(".").lower(), fields[3], fields[4].strip()))
    return sections


def discover(dns, network):
    """Return sorted (address, hostname) pairs for apps with a Flycast address in this network."""
    network = ipaddress.IPv6Network(network)
    (records,) = query(dns, [("_apps.internal", "TXT")])
    # One TXT record may be split into several quoted strings; they form one value.
    text = ",".join("".join(re.findall(r'"([^"\\]*)"', record)) for record in records)
    names = sorted({name for name in text.split(",") if NAME.fullmatch(name)})
    if not names:
        return []
    entries = set()
    for name, addresses in zip(names, query(dns, [(name + ".flycast", "AAAA") for name in names])):
        for address in addresses:
            try:
                ip = ipaddress.IPv6Address(address)
            except ValueError:
                continue
            if ip in network:
                entries.add((str(ip), name + ".flycast"))
    return sorted(entries, key=lambda entry: (entry[1], entry[0]))


def render(text, entries):
    """Replace only our block; everything else in the hosts file stays byte for byte."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == BEGIN]
    ends = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == END]
    if len(starts) > 1 or len(ends) != len(starts) or (starts and ends[0] < starts[0]):
        raise ValueError("Unexpected Fly Access block in /etc/hosts; fix it manually")
    block = [BEGIN + "\n"] + [f"{address} {name}\n" for address, name in entries] + [END + "\n"]
    if not entries:
        block = []
    if starts:
        lines[starts[0]:ends[0] + 1] = block
    elif block:
        if lines and not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        lines += block
    return "".join(lines)


def write(path, text):
    """Atomically replace the file, keeping its mode and owner."""
    if path.is_symlink():
        raise ValueError("Refusing to write the hosts file through a symlink")
    current = path.stat()
    fd, temporary = tempfile.mkstemp(dir=str(path.parent), prefix=".flyaccess-hosts.")
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, stat.S_IMODE(current.st_mode))
        if os.geteuid() == 0:
            os.chown(temporary, current.st_uid, current.st_gid)
        os.replace(temporary, str(path))
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def flush():
    for argv in (["/usr/bin/dscacheutil", "-flushcache"], ["/usr/bin/killall", "-HUP", "mDNSResponder"]):
        try:
            subprocess.run(argv, capture_output=True)
        except OSError:
            pass  # mDNSResponder also notices the changed file by itself.


def apply(entries, path=None):
    path = path or HOSTS
    current = path.read_text()
    updated = render(current, entries)
    if updated == current:
        return False
    write(path, updated)
    if path == HOSTS:
        flush()
    return True


def sync(dns, network, path=None):
    """Update the block from Fly DNS. A failed lookup leaves the file unchanged."""
    return apply(discover(dns, network), path)


def remove(path=None):
    return apply([], path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("sync", "remove"))
    parser.add_argument("--dns", help="Fly DNS server from the WireGuard profile")
    parser.add_argument("--network", help="Fly private /48 from the WireGuard profile")
    parser.add_argument("--hosts-file", type=Path, default=HOSTS)
    parser.add_argument("--dry-run", action="store_true", help="Print the entries without writing")
    args = parser.parse_args()
    try:
        if args.action == "remove":
            remove(args.hosts_file)
            return 0
        if not args.dns or not args.network:
            parser.error("sync requires --dns and --network")
        if args.dry_run:
            for address, name in discover(args.dns, args.network):
                print(address, name)
        elif sync(args.dns, args.network, args.hosts_file):
            print("Updated .flycast entries in " + str(args.hosts_file))
    except (ValueError, RuntimeError, OSError) as error:
        print(error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
