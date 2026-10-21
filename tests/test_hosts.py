"""The managed hosts block: Fly DNS only, strict validation, other lines untouched."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import hosts

NETWORK = "fdaa:0:1234::/48"
ORIGINAL = "127.0.0.1\tlocalhost\n::1 localhost\n# my own entry\n192.0.2.10 printer.lan"
ENTRIES = [("fdaa:0:1234:0:1::2", "my-api.flycast"), ("fdaa:0:1234:0:1::3", "my-web.flycast")]


def header(status="NOERROR"):
    return f";; Got answer:\n;; ->>HEADER<<- opcode: QUERY, status: {status}, id: 1\n;; ANSWER SECTION:\n"


TXT = header() + '_apps.internal.\t10\tIN\tTXT\t"my-api,my-db,my-w" "eb,Bad_Name,my-worker"\n'
AAAA = (header() + "my-api.flycast.\t10\tIN\tAAAA\tfdaa:0:1234:0:1::2\n"
        + header()
        + header() + "my-web.flycast.\t10\tIN\tAAAA\tfdaa:0:1234:0:1::3\n"
        + "my-web.flycast.\t10\tIN\tAAAA\t2001:db8::1\n"
        + header() + "my-worker.flycast.\t10\tIN\tAAAA\tfdaa:0:9999:0:1::4\n")


def dig(*outputs):
    replies = iter(outputs)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, next(replies), "")
    return run, calls


class HostsBlock(unittest.TestCase):
    def test_render_adds_replaces_and_removes_only_its_block(self):
        added = hosts.render(ORIGINAL, ENTRIES)
        self.assertTrue(added.startswith(ORIGINAL + "\n" + hosts.BEGIN + "\n"))
        self.assertIn("fdaa:0:1234:0:1::2 my-api.flycast\n", added)
        self.assertEqual(hosts.render(added, ENTRIES), added)
        replaced = hosts.render(added + "203.0.113.5 later.lan\n", ENTRIES[1:])
        self.assertNotIn("my-api.flycast", replaced)
        self.assertTrue(replaced.endswith(hosts.END + "\n203.0.113.5 later.lan\n"))
        self.assertEqual(hosts.render(added, []), ORIGINAL + "\n")
        self.assertEqual(hosts.render(ORIGINAL, []), ORIGINAL)

    def test_damaged_block_is_refused(self):
        for text in (ORIGINAL + "\n" + hosts.BEGIN + "\n",
                     ORIGINAL + "\n" + hosts.END + "\n" + hosts.BEGIN + "\n",
                     hosts.render(ORIGINAL, ENTRIES) * 2):
            with self.subTest(text=text), self.assertRaises(ValueError):
                hosts.render(text, ENTRIES)

    def test_discovery_reads_fly_dns_and_keeps_only_flycast_addresses_in_network(self):
        run, calls = dig(TXT, AAAA)
        with patch.object(hosts.subprocess, "run", run):
            entries = hosts.discover("fdaa:0:1234::3", NETWORK)
        # Split TXT strings join into one list; invalid names and foreign addresses are dropped.
        self.assertEqual(entries, ENTRIES)
        self.assertEqual(calls[0][:2], ["/usr/bin/dig", "@fdaa:0:1234::3"])
        self.assertEqual(calls[0][-2:], ["_apps.internal", "TXT"])
        self.assertEqual(calls[1][-8:], ["my-api.flycast", "AAAA", "my-db.flycast", "AAAA",
                                         "my-web.flycast", "AAAA", "my-worker.flycast", "AAAA"])

    def test_incomplete_lookup_raises_instead_of_dropping_entries(self):
        timed_out = header() + "my-api.flycast.\t10\tIN\tAAAA\tfdaa:0:1234:0:1::2\n;; connection timed out\n"
        for outputs in ((";; connection timed out; no servers could be reached\n",),
                        (header("SERVFAIL"),), (TXT, timed_out)):
            run, _ = dig(*outputs)
            with self.subTest(outputs=outputs), patch.object(hosts.subprocess, "run", run):
                with self.assertRaises(RuntimeError):
                    hosts.discover("fdaa:0:1234::3", NETWORK)

    def test_sync_writes_atomically_and_failure_leaves_file_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hosts"
            path.write_text(ORIGINAL)
            path.chmod(0o644)
            with patch.object(hosts, "HOSTS", path), patch.object(hosts, "flush") as flush:
                with patch.object(hosts, "discover", side_effect=RuntimeError("Fly DNS lookup failed")):
                    with self.assertRaises(RuntimeError):
                        hosts.sync("fdaa:0:1234::3", NETWORK)
                self.assertEqual(path.read_text(), ORIGINAL)
                with patch.object(hosts, "discover", return_value=ENTRIES):
                    self.assertTrue(hosts.sync("fdaa:0:1234::3", NETWORK))
                    inode = path.stat().st_ino
                    self.assertFalse(hosts.sync("fdaa:0:1234::3", NETWORK))
                self.assertEqual(path.stat().st_ino, inode)
                self.assertEqual(path.stat().st_mode & 0o777, 0o644)
                self.assertEqual(flush.call_count, 1)
                self.assertTrue(hosts.remove())
                self.assertEqual(path.read_text(), ORIGINAL + "\n")
            self.assertEqual(sorted(os.listdir(tmp)), ["hosts"])

    def test_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            target, alias = Path(tmp) / "target", Path(tmp) / "hosts"
            target.write_text(ORIGINAL)
            alias.symlink_to(target)
            with self.assertRaises(ValueError):
                hosts.apply(ENTRIES, alias)
            self.assertEqual(target.read_text(), ORIGINAL)

    def test_runs_isolated_as_launchd_does(self):
        # launchd runs a lone copy with -I: no sibling modules, no user site-packages.
        with tempfile.TemporaryDirectory() as tmp:
            copy = Path(tmp) / "hosts.py"
            copy.write_text(Path(hosts.__file__).read_text())
            result = subprocess.run([sys.executable, "-I", "-B", str(copy), "--help"],
                                    capture_output=True, text=True, cwd=tmp)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
