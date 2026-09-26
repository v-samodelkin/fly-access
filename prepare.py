#!/usr/bin/env python3
"""Create device-specific Fly peers; keep profiles and encrypted backups outside Git."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from common import DEFAULT_DIR, external_directory, parse_profile, private_write, run_private
from layout import device_profile

PLATFORMS = ('macos', 'iphone', 'windows')


def seal_profile(profile, recipient):
    # Use an explicit public recipient instead of a project-specific .sops.yaml.
    with tempfile.TemporaryDirectory(prefix='fly-access-sops-') as tmp:
        config = Path(tmp) / 'config.json'
        config.write_text(json.dumps({'creation_rules': [{'age': recipient}]}))
        return run_private(['sops', '--config', str(config), 'encrypt', '--input-type', 'binary',
                            '--output-type', 'json', '--age', recipient, str(profile)])


def prepare(args):
    os.umask(0o077)
    tools = ['fly', 'sops'] + (['qrencode'] if 'iphone' in args.devices else [])
    for tool in tools:
        if not shutil.which(tool):
            raise RuntimeError(f'Install {tool} first')
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,30}', args.prefix):
        raise ValueError('Invalid peer prefix')
    if not re.fullmatch(r'age1[0-9a-z]{50,}', args.age_recipient or ''):
        raise ValueError('Provide your public age recipient with --age-recipient')
    root = external_directory(args.directory)
    os.environ.setdefault('SOPS_AGE_KEY_FILE', str(Path.home() / '.config/sops/age/keys.txt'))
    metadata = {'org': args.org, 'region': args.region, 'prefix': args.prefix}
    if (root / 'peers.json').exists() and json.loads((root / 'peers.json').read_text()) != metadata:
        raise ValueError('Directory belongs to another org/region/prefix; use a separate --directory')
    private_write(root / 'peers.json', json.dumps(metadata, indent=2) + '\n')
    for platform in args.devices:
        folder = external_directory(root / platform)
        profile = device_profile(root, platform)
        sealed = profile.with_suffix('.sops.json')
        # Recover older profiles when only their encrypted backup remains.
        if not profile.exists() and not sealed.exists() and (folder / 'samofly.sops.json').exists():
            profile, sealed = folder / 'samofly.conf', folder / 'samofly.sops.json'
        peer_name = f'{args.prefix}-{platform}'
        if not profile.exists():
            if sealed.exists():
                private_write(profile, run_private(['sops', 'decrypt', '--output-type', 'binary', str(sealed)]))
            else:
                print(f'Creating {platform} peer...', flush=True)
                run_private(['fly', 'wireguard', 'create', args.org, args.region, peer_name, str(profile)])
                profile.chmod(0o600)
        parse_profile(profile.read_text())
        if not sealed.exists():
            private_write(sealed, seal_profile(profile, args.age_recipient))
        restored = run_private(['sops', 'decrypt', '--output-type', 'binary', str(sealed)])
        if restored != profile.read_bytes():
            raise ValueError('Encrypted backup differs from profile; neither file was overwritten')
        if platform == 'iphone':
            png = run_private(['qrencode', '-t', 'PNG', '-o', '-'], input=profile.read_bytes())
            private_write(folder / 'import.png', png)
        print(f'Ready: {platform}; private route and encrypted backup verified')
    print(f'Private profiles: {root}')
    print('Rerunning reuses profiles. Existing peers are never rotated or deleted automatically.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--org', required=True)
    parser.add_argument('--region', default='ams')
    parser.add_argument('--prefix', default='fly-access')
    parser.add_argument('--directory', type=Path, default=DEFAULT_DIR)
    parser.add_argument('--devices', nargs='+', choices=PLATFORMS, default=['macos'])
    parser.add_argument('--age-recipient', default=os.environ.get('SOPS_AGE_RECIPIENTS'))
    try:
        prepare(parser.parse_args())
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, f'{error}\n')
