"""Tests for pruning old run folders: prune.py directly, and `run.py prune` as a subprocess."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

import helpers
import prune

DAY = 86400
RUN_PY = helpers.SCRIPTS_DIR / "run.py"


def age(path: Path, days: float, now: float) -> None:
    """Set the mtime of `path` (not a symlink's target) to `days` before `now`."""
    when = now - days * DAY
    os.utime(path, (when, when), follow_symlinks=False)


class PruneRunsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.repo_dir = self.tmp / "owner-repo"
        self.repo_dir.mkdir()
        self.keep = self.repo_dir / "new-run"
        self.now = time.time()

    def make_run(self, name: str, days: float) -> Path:
        run = self.repo_dir / name
        run.mkdir()
        (run / "run.json").write_text("{}", encoding="utf-8")
        age(run / "run.json", days, self.now)
        age(run, days, self.now)
        return run

    def prune(self, days: int = 30) -> dict:
        return prune.prune_runs(self.keep, days, self.now)

    def test_prune_removes_old_run(self):
        old = self.make_run("old", 31)
        recent = self.make_run("recent", 29)
        result = self.prune()
        self.assertEqual(result, {"removed": [str(old)], "skipped": [], "errors": []})
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_prune_days_option(self):
        run = self.make_run("run", 5)
        self.assertEqual(self.prune(30)["removed"], [])
        self.assertEqual(self.prune(3)["removed"], [str(run)])

    def test_prune_age_is_newest_file(self):
        run = self.make_run("live", 40)
        (run / "requests.log").write_text("u push abc b\n", encoding="utf-8")
        age(run / "requests.log", 1, self.now)
        age(run, 40, self.now)
        self.assertEqual(self.prune()["removed"], [])
        self.assertTrue(run.exists())

    def test_prune_keeps_the_given_run(self):
        self.keep = self.make_run("new-run", 90)
        result = self.prune()
        self.assertEqual(result, {"removed": [], "skipped": [], "errors": []})
        self.assertTrue(self.keep.exists())

    def test_prune_keeps_the_given_run_in_another_case(self):
        run = self.make_run("my-slug", 90)
        if not (self.repo_dir / "MY-SLUG").exists():
            self.skipTest("case-sensitive filesystem")
        self.keep = self.repo_dir / "My-Slug"
        self.assertEqual(self.prune()["removed"], [])
        self.assertTrue(run.exists())

    def test_prune_refuses_unsafe_dir(self):
        old = self.make_run("old", 90)
        home = Path.home()
        for keep in (Path("old-sibling"), Path("/new-run"), home / "new-run", home / "projects" / "new-run"):
            with self.subTest(keep=str(keep)), self.assertRaises(prune.PruneError):
                prune.prune_runs(keep, 30, self.now)
        self.assertTrue(old.exists())

    def test_prune_skips_symlinked_run(self):
        elsewhere = self.tmp / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "run.json").write_text("{}", encoding="utf-8")
        age(elsewhere / "run.json", 90, self.now)
        link = self.repo_dir / "linked"
        link.symlink_to(elsewhere)
        age(link, 90, self.now)
        result = self.prune()
        self.assertEqual(result, {"removed": [], "skipped": [str(link)], "errors": []})
        self.assertTrue((elsewhere / "run.json").exists())

    def test_prune_age_ignores_symlink_targets(self):
        fresh = self.tmp / "fresh.txt"
        fresh.write_text("new", encoding="utf-8")
        run = self.make_run("old", 40)
        link = run / "pointer"
        link.symlink_to(fresh)
        age(link, 40, self.now)
        age(run, 40, self.now)
        self.assertEqual(self.prune()["removed"], [str(run)])
        self.assertTrue(fresh.exists())

    def test_prune_skips_non_run_folder(self):
        other = self.repo_dir / "notes"
        other.mkdir()
        age(other, 90, self.now)
        result = self.prune()
        self.assertEqual(result, {"removed": [], "skipped": [str(other)], "errors": []})
        self.assertTrue(other.exists())

    def test_prune_without_repo_folder(self):
        self.keep = self.tmp / "other-repo" / "first-run"
        self.assertEqual(self.prune(), {"removed": [], "skipped": [], "errors": []})


class RetentionDaysTest(unittest.TestCase):
    def test_retention_days(self):
        self.assertEqual(prune.retention_days(""), 30)
        self.assertEqual(prune.retention_days("${user_config.retention_days}"), 30)
        self.assertEqual(prune.retention_days("7"), 7)
        for bad in ("0", "-1", "x"):
            with self.subTest(value=bad), self.assertRaises(argparse.ArgumentTypeError):
                prune.retention_days(bad)


class PruneCliTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo_dir = Path(tmp.name) / "owner-repo"
        self.repo_dir.mkdir()

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(RUN_PY), *args], capture_output=True, text=True, timeout=60)

    def test_cli_prune(self):
        old = self.repo_dir / "old"
        old.mkdir()
        (old / "run.json").write_text("{}", encoding="utf-8")
        now = time.time()
        age(old / "run.json", 31, now)
        age(old, 31, now)
        result = self.run_cli("prune", str(self.repo_dir / "new-run"), "--days", "${user_config.retention_days}")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"removed": [str(old)], "skipped": [], "errors": []})
        self.assertFalse(old.exists())

    def test_cli_prune_refuses_relative_dir(self):
        old = self.repo_dir / "old"
        old.mkdir()
        (old / "run.json").write_text("{}", encoding="utf-8")
        age(old, 90, time.time())
        result = subprocess.run(
            [sys.executable, str(RUN_PY), "prune", "new-run", "--days", "30"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=self.repo_dir,
        )
        self.assertEqual((result.returncode, result.stdout), (1, ""))
        self.assertTrue(result.stderr.startswith("run.py prune: "), result.stderr)
        self.assertTrue(old.exists())

    def test_cli_prune_rejects_zero_days(self):
        result = self.run_cli("prune", str(self.repo_dir / "new-run"), "--days", "0")
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
