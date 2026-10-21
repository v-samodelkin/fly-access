"""Update checks read only the GitHub release API; updates fast-forward to a release tag."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import update

RELEASE = {"tag_name": "v1.4", "html_url": "https://github.com/v-samodelkin/fly-access/releases/tag/v1.4"}
# Isolated git: no user config, hooks or signing from the machine running the tests.
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "Test",
           "GIT_AUTHOR_EMAIL": "test@example.com", "GIT_COMMITTER_NAME": "Test", "GIT_COMMITTER_EMAIL": "test@example.com"}
FAKE_INSTALL = "from pathlib import Path\nPath(__file__).with_name('installed').write_text('yes')\n"


def curl(stdout, code=0):
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, code, stdout, "")
    return run, calls


class Check(unittest.TestCase):
    def test_versions_compare_numerically(self):
        self.assertGreater(update.version("v1.10"), update.version("1.9"))
        self.assertGreater(update.version("v1.2.1"), update.version("1.2"))
        self.assertEqual(update.version("1.2"), update.version("v1.2.0"))
        for text in ("1", "v1.2-beta", "latest", "1.2; rm -rf"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                update.version(text)

    def test_check_reads_only_the_latest_release(self):
        run, calls = curl(json.dumps(RELEASE))
        with patch.object(update.subprocess, "run", run):
            self.assertTrue(update.check("1.3")["available"])
            self.assertFalse(update.check("1.4")["available"])
            self.assertFalse(update.check("1.10")["available"])
        self.assertEqual(calls[0][0], "/usr/bin/curl")
        self.assertEqual(calls[0][-1], "https://api.github.com/repos/v-samodelkin/fly-access/releases/latest")

    def test_unexpected_or_failed_responses_are_errors(self):
        for stdout, code in ((json.dumps(RELEASE), 22), ("not json", 0), ("[]", 0),
                             (json.dumps(dict(RELEASE, tag_name="nightly")), 0),
                             (json.dumps(dict(RELEASE, html_url="https://example.com/v1.4")), 0)):
            run, _ = curl(stdout, code)
            with self.subTest(stdout=stdout, code=code), patch.object(update.subprocess, "run", run):
                with self.assertRaises(RuntimeError):
                    update.check("1.3")


class Apply(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, GIT_ENV)
        self.env.start()
        self.origin, self.work, self.checkout = root / "origin.git", root / "work", root / "checkout"
        self.git(root, "init", "--quiet", "--bare", "-b", "main", str(self.origin))
        self.git(root, "clone", "--quiet", str(self.origin), str(self.work))
        self.git(self.work, "symbolic-ref", "HEAD", "refs/heads/main")
        for name, text in (("install-menu.py", FAKE_INSTALL), ("common.py", "# v1.0\n"), ("README.md", "v1.0\n")):
            (self.work / name).write_text(text)
        self.release("v1.0")
        self.git(root, "clone", "--quiet", str(self.origin), str(self.checkout))

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def git(self, where, *args):
        subprocess.run(["git", "-C", str(where), *args], check=True, capture_output=True)

    def release(self, tag, **changes):
        for name, text in changes.items():
            (self.work / name).write_text(text)
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "--quiet", "-m", tag)
        self.git(self.work, "tag", tag)
        self.git(self.work, "push", "--quiet", "origin", "main", "--tags")

    def apply(self, tag):
        # The checkout's own remote points elsewhere: releases come only from the upstream repository.
        self.git(self.checkout, "remote", "set-url", "origin", "https://invalid.example/fork.git")
        with patch.object(update, "SOURCE", self.checkout), patch.object(update, "UPSTREAM", str(self.origin)), \
             contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return update.apply(tag)

    def test_menu_only_release_fast_forwards_to_the_tag_and_rebuilds(self):
        self.release("v1.1", **{"README.md": "v1.1\n"})
        self.release("v1.2", **{"README.md": "unreleased\n"})
        self.assertFalse(self.apply("v1.1"))
        self.assertEqual((self.checkout / "README.md").read_text(), "v1.1\n")
        self.assertTrue((self.checkout / "installed").exists())

    def test_reports_tunnel_changes(self):
        self.release("v1.1", **{"common.py": "# v1.1\n"})
        self.assertTrue(self.apply("v1.1"))
        self.release("v1.2", **{"README.md": "v1.2\n"})
        self.assertFalse(self.apply("v1.2"))

    def test_local_commits_stop_the_update(self):
        self.release("v1.1", **{"README.md": "v1.1\n"})
        (self.checkout / "README.md").write_text("local change\n")
        self.git(self.checkout, "commit", "--quiet", "-am", "local")
        with self.assertRaisesRegex(RuntimeError, "local commits"):
            self.apply("v1.1")
        self.assertEqual((self.checkout / "README.md").read_text(), "local change\n")
        self.assertFalse((self.checkout / "installed").exists())



class Outcome(unittest.TestCase):
    def test_apply_always_leaves_an_outcome_for_the_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = Path(tmp) / "update.json"
            argv = ["update.py", "apply", "v1.4", "--result", str(result)]
            for effect, expected in ((lambda tag: True, {"ok": True, "tunnel": True}),
                                     (RuntimeError("Unable to rebuild the menu"),
                                      {"ok": False, "error": "Unable to rebuild the menu"})):
                with self.subTest(expected=expected), patch.object(sys, "argv", argv), \
                     patch.object(update, "apply", side_effect=effect), contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(update.main(), 0 if expected["ok"] else 1)
                self.assertEqual(json.loads(result.read_text()), expected)
            self.assertEqual(sorted(os.listdir(tmp)), ["update.json"])


if __name__ == "__main__":
    unittest.main()
