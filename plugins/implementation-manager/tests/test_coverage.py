"""Tests for coverage.py: the coverage verdict under a wait --full event."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import unittest

import helpers  # noqa: F401  (puts the scripts dir on sys.path)

# isort: split
import coverage

SHA1 = "1" * 40
SHA2 = "2" * 40
SHA3 = "3" * 40


def completed(stdout="", stderr="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class EventTargetTests(unittest.TestCase):
    def test_event_target(self):
        for line, want in (
            (f"REQUEST a {SHA1} feat/a", ("a", SHA1)),
            (f"PENDING a {SHA1} feat/a", ("a", SHA1)),
            (f"NEXT a {SHA1}", ("a", SHA1)),
            (f"READY a 7 {SHA1}", ("a", SHA1)),
            (f"REMIND a 7 {SHA1}", ("a", SHA1)),
            (f"FINAL-READY a 7 {SHA1}", None),
            ("MERGED a 7", None),
            ("ERROR x", None),
        ):
            with self.subTest(line=line):
                self.assertEqual(coverage.event_target(line), want)


class VerdictTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.rs = self.tmp / "rs"
        self.rs.write_text("", encoding="utf-8")
        self.state = {
            "check": {"rs": str(self.rs), "state_dir": str(self.tmp / "sd")},
            "units": {"a": {"worktree": str(self.tmp / "wt")}, "b": {"worktree": None}},
        }
        self.argv = None

    def fake(self, result=None, exc=None):
        def run(argv, **kwargs):
            self.argv = argv
            if exc:
                raise exc
            return result

        return run

    def test_no_check_recorded(self):
        self.state["check"] = None
        self.assertEqual(
            coverage.verdict_line(self.state, "a", SHA1), f"verdict {SHA1} unavailable: no check command recorded"
        )

    def test_rs_missing(self):
        self.rs.unlink()
        self.assertEqual(
            coverage.verdict_line(self.state, "a", SHA1), f"verdict {SHA1} unavailable: rs not found: {self.rs}"
        )

    def test_unit_without_worktree(self):
        self.assertEqual(
            coverage.verdict_line(self.state, "b", SHA1), f"verdict {SHA1} unavailable: unit b has no worktree"
        )

    def test_covered_json_is_compacted(self):
        run = self.fake(completed('{\n "covered": true,\n "reasons": []\n}\n'))
        self.assertEqual(
            coverage.verdict_line(self.state, "a", SHA1, run=run), f'verdict {SHA1} {{"covered":true,"reasons":[]}}'
        )

    def test_exit_1_reports_stderr(self):
        run = self.fake(completed(stderr="commit not found: abc\nmore", returncode=1))
        self.assertEqual(
            coverage.verdict_line(self.state, "a", SHA1, run=run),
            f"verdict {SHA1} unavailable: check failed: commit not found: abc",
        )

    def test_timeout(self):
        run = self.fake(exc=subprocess.TimeoutExpired("rs", 120))
        self.assertEqual(
            coverage.verdict_line(self.state, "a", SHA1, run=run), f"verdict {SHA1} unavailable: check timed out"
        )

    def test_no_json(self):
        run = self.fake(completed("not json"))
        self.assertEqual(
            coverage.verdict_line(self.state, "a", SHA1, run=run), f"verdict {SHA1} unavailable: check printed no JSON"
        )

    def test_command_shape(self):
        run = self.fake(completed("{}"))
        coverage.verdict_line(self.state, "a", SHA1, run=run)
        self.assertEqual(
            self.argv,
            [
                "sh",
                str(self.rs),
                "covered",
                "--repo",
                str(self.tmp / "wt"),
                "--commit",
                SHA1,
                "--state-dir",
                str(self.tmp / "sd"),
            ],
        )

    def test_ready_verdict_uses_head_sha(self):
        line = f"READY a 7 {SHA2}"
        unit, sha = coverage.event_target(line)
        run = self.fake(completed("{}"))
        self.assertTrue(coverage.verdict_line(self.state, unit, sha, run=run).startswith(f"verdict {SHA2} "))
        self.assertEqual(self.argv[self.argv.index("--commit") + 1], SHA2)


class LogLinesTests(unittest.TestCase):
    def test_log_lines_for_stuck(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "pushes.log").write_text(f"a go {SHA1}\nb go {SHA2}\n", encoding="utf-8")
            (run / "requests.log").write_text(f"a syncing {SHA3} integration/x\n", encoding="utf-8")
            self.assertEqual(
                coverage.log_lines(run, "STUCK a - syncing"),
                [f"pushes: a go {SHA1}", f"requests: a syncing {SHA3} integration/x"],
            )
            self.assertEqual(coverage.log_lines(run, f"READY a 7 {SHA1}"), [])
            self.assertEqual(coverage.log_lines(run, "STUCK c - x"), [])


if __name__ == "__main__":
    unittest.main()
