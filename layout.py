"""Portable defaults, with detection of the original local installation."""
from pathlib import Path

LEGACY_LABEL = 'dev.samoverse.fly-access'
LEGACY_MENU_LABEL = 'dev.samoverse.fly-menu'
LEGACY_STATE = Path('/Library/Application Support/Samoverse/FlyAccess')
LEGACY_MARKER = '# Managed by Samoverse scripts/fly-access'


def paths(legacy=False):
    if legacy:
        return LEGACY_LABEL, LEGACY_STATE, 'samofly', LEGACY_MARKER
    return 'io.flyaccess.tunnel', Path('/Library/Application Support/FlyAccess'), 'flyaccess', '# Managed by Fly Access'


def existing_legacy():
    return Path('/Library/LaunchDaemons', LEGACY_LABEL + '.plist').exists()


LABEL, STATE, TUNNEL_NAME, MANAGED = paths(existing_legacy())
PROFILE = STATE / (TUNNEL_NAME + '.conf')
PLIST = Path('/Library/LaunchDaemons') / (LABEL + '.plist')


def profile_directory(home):
    modern = home / '.config/fly-access'
    legacy = home / '.config/samoverse/fly-access'
    return legacy if not modern.exists() and legacy.exists() else modern


def device_profile(directory, platform):
    modern = directory / platform / 'flyaccess.conf'
    legacy = directory / platform / 'samofly.conf'
    return legacy if not modern.exists() and legacy.exists() else modern
