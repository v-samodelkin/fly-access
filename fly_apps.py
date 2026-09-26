"""Discover Fly apps using management commands only; never contact app endpoints."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import ipaddress
import json
from pathlib import Path
import re
import shutil
import subprocess

ALLOWED_COMMANDS = {('apps', 'list'), ('machine', 'list'), ('ips', 'list')}


class DiscoveryError(Exception):
    pass


def fly_json(executable, *args):
    if tuple(args[:2]) not in ALLOWED_COMMANDS:
        raise DiscoveryError('Command is not allowed for background discovery')
    try:
        result = subprocess.run([executable, *args, '--json'], capture_output=True,
                                text=True, stdin=subprocess.DEVNULL, timeout=12, cwd=Path.home())
    except (OSError, subprocess.TimeoutExpired):
        raise DiscoveryError('Fly CLI is unavailable or timed out') from None
    if result.returncode:
        # Fly output can contain credentials or environment values; never relay it to the menu.
        raise DiscoveryError('Unable to read Fly. Check your connection and run fly auth login')
    try:
        data = json.loads(result.stdout)
        if not isinstance(data, list):
            raise ValueError()
        return data
    except (TypeError, ValueError):
        raise DiscoveryError('Invalid response from Fly CLI') from None


def machine_status(machines):
    states = {m.get('state') for m in machines}
    if not states:
        return 'empty', 'No machines'
    if 'started' in states:
        return 'started', 'Running'
    if states <= {'stopped', 'suspended'}:
        return 'sleeping', 'Sleeping'
    if states & {'starting', 'stopping', 'suspending', 'replacing', 'created'}:
        return 'transitioning', 'Updating'
    return 'unknown', 'Unknown'


def public_ip(record):
    if record.get('Type') == 'private_v6':
        return False
    try:
        return ipaddress.ip_address(record.get('Address', '')).is_global
    except ValueError:
        return False


def web_destination(name, machines, addresses):
    """Infer routes from configured HTTP handlers, never from a probe or a guessed DB port."""
    private = any(ip.get('Type') == 'private_v6' for ip in addresses)
    public = any(public_ip(ip) for ip in addresses)
    routes = []
    for machine in machines:
        for service in machine.get('config', {}).get('services', []):
            if service.get('protocol') != 'tcp':
                continue
            for port in service.get('ports', []):
                handlers = port.get('handlers') or []
                number = port.get('port')
                if 'http' not in handlers or not isinstance(number, int) or not 1 <= number <= 65535:
                    continue
                tls = 'tls' in handlers
                force_https = port.get('force_https', service.get('force_https', False))
                # Flycast is HTTP-only; never turn its HTTP link into HTTPS.
                if private and not tls and not force_https:
                    suffix = '' if number == 80 else ':' + str(number)
                    routes.append((0 if number == 80 else 1, f'http://{name}.flycast{suffix}', True))
                if public and (tls or not force_https):
                    scheme = 'https' if tls else 'http'
                    suffix = '' if number == (443 if tls else 80) else ':' + str(number)
                    routes.append((2 if tls else 3, f'{scheme}://{name}.fly.dev{suffix}', False))
    if routes:
        _, url, private = min(routes)
        return url, 'app', private
    return f'https://fly.io/apps/{name}', 'dashboard', False


def discover(executable, query=fly_json):
    apps = query(executable, 'apps', 'list')  # All organizations accessible to the existing login.
    unique = {app['Name']: app for app in apps if isinstance(app.get('Name'), str)
              and re.fullmatch(r'[a-z0-9][a-z0-9-]*', app['Name'])}

    def detail(app):
        name = app['Name']
        entry = {'name': name, 'organization': (app.get('Organization') or {}).get('Slug', ''),
                 'url': f'https://fly.io/apps/{name}', 'destination': 'dashboard', 'isPrivate': False,
                 'status': 'unknown', 'statusLabel': 'Unavailable', 'detail': ''}
        try:
            machines = [m for m in query(executable, 'machine', 'list', '--app', name)
                        if m.get('state') != 'destroyed']
            entry['status'], entry['statusLabel'] = machine_status(machines)
            addresses = query(executable, 'ips', 'list', '--app', name)
            entry['url'], entry['destination'], entry['isPrivate'] = web_destination(name, machines, addresses)
            entry['detail'] = ('Open app; Fly VPN required' if entry['isPrivate'] else
                               'Open app' if entry['destination'] == 'app' else
                               'Open Fly dashboard; no HTTP route configured')
        except DiscoveryError as error:
            entry['detail'] = str(error) + '. Open Fly dashboard'
            entry['metadataError'] = True
        return entry

    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(detail, sorted(unique.values(), key=lambda app: app['Name'])))
    return {'ok': True, 'apps': entries, 'partial': sum(bool(a.get('metadataError')) for a in entries)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fly', default=shutil.which('fly') or shutil.which('flyctl'))
    args = parser.parse_args()
    try:
        if not args.fly:
            raise DiscoveryError('Install Fly CLI and run fly auth login')
        result = discover(args.fly)
    except DiscoveryError as error:
        result = {'ok': False, 'apps': [], 'partial': 0, 'error': str(error)}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
