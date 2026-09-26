"""Validate network boundaries and secret handling without touching system networking."""
import base64
import configparser
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from common import REPO, external_directory, parse_profile, private_write, render_profile, run_private
import macos
from macos_status import autostart_enabled, classify

# Synthetic, non-operational keys and network: no credentials from a live peer.
PROFILE = f"""[Interface]
PrivateKey = {base64.b64encode(bytes(range(32))).decode()}
Address = fdaa:0:1234:a7b:1:2:3:4/120
DNS = fdaa:0:1234::3
[Peer]
PublicKey = {base64.b64encode(bytes(range(32, 64))).decode()}
AllowedIPs = fdaa:0:1234::/48
Endpoint = ams.gateway.6pn.dev:51820
PersistentKeepalive = 15
"""


class Profiles(unittest.TestCase):
    def test_split_preserves_private_route_without_global_dns(self):
        config = parse_profile(PROFILE)
        split = configparser.ConfigParser()
        split.read_string(render_profile(config, split=True))
        self.assertNotIn("dns", split["Interface"])
        self.assertEqual(split["Peer"]["allowedips"], "fdaa:0:1234::/48")
        normal = parse_profile(render_profile(config))
        self.assertEqual(normal["Interface"]["dns"], "fdaa:0:1234::3")
        self.assertEqual(normal["Interface"]["privatekey"], config["Interface"]["privatekey"])

    def test_rejects_wrong_network_default_routes_and_command_hooks(self):
        for text in (
            PROFILE.replace("fdaa:0:1234::/48", "::/0"),
            PROFILE.replace("fdaa:0:1234::/48", "fdaa:0:1234::/48, 0.0.0.0/0"),
            PROFILE.replace("DNS = fdaa:0:1234::3", "DNS = fdaa:0:9999::3"),
            PROFILE.replace("[Peer]", "PostUp = touch /tmp/should-never-run\n[Peer]"),
            PROFILE + "\n[Peer]\nPublicKey = invalid\n",
        ):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_profile(text)

    def test_parser_errors_do_not_echo_key(self):
        key = base64.b64encode(bytes(range(32))).decode()
        with self.assertRaises(ValueError) as error:
            parse_profile(PROFILE + f"\n{key}\n")
        self.assertNotIn(key, str(error.exception))

    def test_subprocess_failure_does_not_echo_key(self):
        with self.assertRaises(RuntimeError) as error:
            run_private([sys.executable, "-c", "import sys; print('secret-value'); sys.exit(1)"])
        self.assertNotIn("secret-value", str(error.exception))

    def test_private_files_and_repo_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = external_directory(tmp)
            target = root / "samofly.conf"
            private_write(target, PROFILE)
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            alias = root / "alias"
            alias.symlink_to(target)
            with self.assertRaises(ValueError):
                private_write(alias, "overwrite")
            self.assertEqual(target.read_text(), PROFILE)
        with self.assertRaises(ValueError):
            external_directory(REPO / "profiles")

    def test_launchd_uses_argument_array_and_keeps_process_alive(self):
        job = plistlib.loads(plistlib.dumps(macos.launch_plist(Path("/opt/homebrew/bin"))))
        self.assertEqual(job["ProgramArguments"], ["/opt/homebrew/bin/wg-quick", "up", str(macos.PROFILE)])
        self.assertTrue(job["KeepAlive"])
        self.assertTrue(job["RunAtLoad"])

    def test_existing_resolver_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resolvers = root / "resolver"
            resolvers.mkdir()
            target = resolvers / "internal"
            target.write_text("nameserver 192.0.2.1\n")
            with patch.object(macos, "RESOLVERS", resolvers), patch.object(macos, "STATE", root / "state"), patch.object(macos, "PLIST", root / "job.plist"):
                with self.assertRaises(RuntimeError):
                    macos.preflight()
            self.assertEqual(target.read_text(), "nameserver 192.0.2.1\n")

    def test_menu_requires_real_dns_reachability_for_connected(self):
        self.assertEqual(classify(True, True, True, True, True)["state"], "on")
        for checks in ((True, True, True, True, False), (True, True, False, True, True),
                       (True, False, True, True, True), (True, True, True, False, True)):
            self.assertEqual(classify(*checks)["state"], "error")
        self.assertEqual(classify(False, False, False, False, False, False)["state"], "off")
        self.assertEqual(classify(False, True, False, False, False)["state"], "error")

    def test_autostart_parses_current_and_older_launchctl_output(self):
        for value in ("disabled", "true"):
            self.assertFalse(autostart_enabled(f'"{macos.LABEL}" => {value}'))
        for value in ("enabled", "false"):
            self.assertTrue(autostart_enabled(f'"{macos.LABEL}" => {value}'))

    def test_off_removes_only_our_resolvers_and_disables_autostart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state, resolvers = root / "state", root / "resolver"
            state.mkdir(); resolvers.mkdir()
            (state / ".managed").write_text(macos.MANAGED + "\n")
            for domain in macos.DOMAINS:
                (resolvers / domain).write_text(macos.MANAGED + "\nnameserver fdaa:0:1234::3\n")
            unrelated = resolvers / "corp.example"
            unrelated.write_text("nameserver 192.0.2.1\n")
            with patch.object(macos, "STATE", state), patch.object(macos, "RESOLVERS", resolvers), \
                 patch.object(macos, "PLIST", root / "job.plist"), patch.object(macos, "checked") as run, \
                 patch.object(macos, "stop"), patch.object(macos, "brew_bin", return_value=Path("/fake")), \
                 patch.object(macos.subprocess, "run"):
                macos.turn_off()
            run.assert_called_once_with(["launchctl", "disable", f"system/{macos.LABEL}"])
            self.assertEqual(unrelated.read_text(), "nameserver 192.0.2.1\n")
            for domain in macos.DOMAINS:
                self.assertFalse((resolvers / domain).exists())
                self.assertTrue((state / f"resolver-{domain}.disabled").exists())


if __name__ == "__main__":
    unittest.main()
