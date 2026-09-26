import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from layout import device_profile, paths, profile_directory
from prepare import seal_profile


class PortableInstall(unittest.TestCase):
    def test_new_defaults_and_legacy_profiles_do_not_mix(self):
        self.assertEqual(paths(False)[0], 'io.flyaccess.tunnel')
        self.assertEqual(paths(True)[2], 'samofly')
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            modern, legacy = home / '.config/fly-access', home / '.config/samoverse/fly-access'
            self.assertEqual(profile_directory(home), modern)
            (legacy / 'macos').mkdir(parents=True)
            old = legacy / 'macos/samofly.conf'
            old.write_text('synthetic placeholder')
            self.assertEqual(profile_directory(home), legacy)
            self.assertEqual(device_profile(legacy, 'macos'), old)
            (modern / 'macos').mkdir(parents=True)
            self.assertEqual(profile_directory(home), modern)
            self.assertEqual(device_profile(modern, 'macos'), modern / 'macos/flyaccess.conf')

    @unittest.skipUnless(shutil.which('sops') and shutil.which('age-keygen'), 'SOPS/age optional integration test')
    def test_backup_uses_explicit_recipient_outside_parent_sops_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            key = root / 'identity.txt'
            subprocess.run(['age-keygen', '-o', str(key)], capture_output=True, check=True)
            recipient = subprocess.run(['age-keygen', '-y', str(key)], capture_output=True,
                                       text=True, check=True).stdout.strip()
            (root / '.sops.yaml').write_text(json.dumps({'creation_rules': [{'path_regex': '^other-project-only$'}]}))
            profile = root / 'profile.conf'
            synthetic = '[Interface]\nPrivateKey = ' + base64.b64encode(bytes(range(32))).decode() + '\n'
            profile.write_text(synthetic)
            previous = Path.cwd()
            try:
                os.chdir(root)
                encrypted = seal_profile(profile, recipient)
            finally:
                os.chdir(previous)
            sealed = root / 'profile.sops.json'
            sealed.write_bytes(encrypted)
            env = dict(os.environ, SOPS_AGE_KEY_FILE=str(key))
            restored = subprocess.run(['sops', 'decrypt', '--output-type', 'binary', str(sealed)],
                                      env=env, capture_output=True, check=True).stdout
            self.assertEqual(restored, synthetic.encode())
            self.assertNotIn(synthetic.encode(), encrypted)


if __name__ == '__main__':
    unittest.main()
