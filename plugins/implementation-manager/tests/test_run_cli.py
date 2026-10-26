"""Tests for the run.py command line, run as a subprocess against temporary run folders."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from fakes import HEAD_A, make_pr
import helpers

RUN_PY = helpers.SCRIPTS_DIR / "run.py"
SHA1 = "1" * 40
SHA2 = "2" * 40
SHA3 = "3" * 40

FAKE_GH = """#!{python}
import json, os, sys
with open(os.environ["FAKE_GH_DATA"]) as f:
    data = json.load(f)
if sys.argv[1:3] == ["pr", "list"]:
    print(json.dumps(data["pr_list"]))
elif sys.argv[1:3] == ["api", "graphql"]:
    print(json.dumps(data["graphql"]))
else:
    print("unexpected: " + " ".join(sys.argv[1:]), file=sys.stderr)
    sys.exit(1)
"""


class CliTestCase(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.dir = self.tmp / "my-run"
        self.env = dict(os.environ)

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(RUN_PY), *args], capture_output=True, text=True, env=self.env, timeout=60
        )

    def ok(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        return result.stdout

    def init(self, *extra):
        self.ok("init", str(self.dir), "--repo", "owner/repo", "--base", "main", "--method", "squash", *extra)

    def read(self, name):
        return (self.dir / name).read_text(encoding="utf-8")

    def state(self):
        return json.loads(self.read("run.json"))

    def append_request(self, text):
        with open(self.dir / "requests.log", "a", encoding="utf-8") as f:
            f.write(text)

    def fake_gh(self, data):
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        gh = bin_dir / "gh"
        gh.write_text(FAKE_GH.format(python=sys.executable))
        gh.chmod(0o755)
        data_file = self.tmp / "gh.json"
        data_file.write_text(json.dumps(data))
        self.env["PATH"] = f"{bin_dir}{os.pathsep}{self.env['PATH']}"
        self.env["FAKE_GH_DATA"] = str(data_file)


class SetupTests(CliTestCase):
    def test_init_unit_open_ready_flow(self):
        self.init("--wip", "1")
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b", "--prereq", "a")
        self.assertEqual(self.ok("open-ready", str(self.dir)), "a\n")
        self.assertEqual(self.read("gates.log"), "a open\n")
        state = self.state()
        self.assertEqual(state["wip"], 1)
        self.assertEqual(state["units"]["b"]["prereqs"], ["a"])

    def test_unit_after_and_none(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b", "--after", "a")
        self.assertEqual(self.state()["units"]["b"]["after"], ["a"])
        self.ok("unit", str(self.dir), "b", "--after", "none")
        self.assertEqual(self.state()["units"]["b"]["after"], [])

    def test_init_creates_the_files(self):
        self.init()
        for name in ("run.json", "requests.log", "pushes.log", "gates.log"):
            self.assertTrue((self.dir / name).exists(), name)
        state = self.state()
        self.assertEqual((state["repo"], state["base"], state["method"]), ("owner/repo", "main", "squash"))
        self.assertEqual((state["queue_mode"], state["wip"]), ("serial", None))

    def test_init_twice_exit_1(self):
        self.init()
        result = self.run_cli("init", str(self.dir), "--repo", "o/r", "--base", "main", "--method", "merge")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertIn("already exists", result.stderr)

    def test_unit_records_branch_worktree_and_specs(self):
        self.init()
        self.ok("unit", str(self.dir), "specs", "--specs")
        self.ok("unit", str(self.dir), "a", "--prereq", "specs", "--branch", "feat/a", "--worktree", "/w/a")
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a2")
        unit = self.state()["units"]["a"]
        self.assertEqual((unit["branch"], unit["worktree"], unit["prereqs"]), ("feat/a2", "/w/a", ["specs"]))
        self.assertTrue(self.state()["units"]["specs"]["specs"])
        self.assertFalse(unit["specs"])

    def test_set_wip_and_queue(self):
        self.init("--wip", "2")
        self.ok("set", str(self.dir), "wip", "none")
        self.ok("set", str(self.dir), "queue", "parallel")
        state = self.state()
        self.assertEqual((state["wip"], state["queue_mode"]), (None, "parallel"))
        result = self.run_cli("set", str(self.dir), "wip", "0")
        self.assertEqual(result.returncode, 1)

    def test_open_and_stop_write_gates_log(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("open", str(self.dir), "a")
        self.ok("stop", str(self.dir), "a", "dropped", "by", "the", "owner")
        self.assertEqual(self.read("gates.log"), "a open\na stop dropped by the owner\n")

    def test_stopped_unit_stays_closed(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("stop", str(self.dir), "a", "dropped")
        self.assertEqual(self.ok("open-ready", str(self.dir)), "")
        self.assertEqual(self.read("gates.log"), "a stop dropped\n")
        self.assertIn("- gated: a stopped", self.ok("status", str(self.dir)).splitlines())


class PushTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b")

    def test_go_then_queue_then_yield_then_wait_next(self):
        self.ok("go", str(self.dir), "a", SHA1)
        self.ok("queue", str(self.dir), "b", SHA2)
        self.ok("yield", str(self.dir), "a", "owner", "question")
        self.assertEqual(self.ok("wait", str(self.dir)), f"NEXT b {SHA2}\n")
        self.assertEqual(self.read("pushes.log"), f"a go {SHA1}\n")

    def test_go_refused_exit_1(self):
        self.ok("go", str(self.dir), "a", SHA1)
        result = self.run_cli("go", str(self.dir), "b", SHA2)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout, "")
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertIn("held by 'a'", result.stderr)
        self.assertEqual(self.read("pushes.log"), f"a go {SHA1}\n")

    def test_after_on_the_slot_holder_frees_the_slot(self):
        self.ok("go", str(self.dir), "b", SHA1)
        self.ok("unit", str(self.dir), "b", "--after", "a")
        self.assertIn("- slot: free", self.ok("status", str(self.dir)).splitlines())
        self.ok("go", str(self.dir), "a", SHA2)
        self.ok("queue", str(self.dir), "b", SHA3)
        self.assertEqual(self.read("pushes.log"), f"b go {SHA1}\na go {SHA2}\n")

    def test_go_refused_while_another_unit_is_first_in_the_queue_exit_1(self):
        self.ok("unit", str(self.dir), "c")
        self.ok("go", str(self.dir), "a", SHA1)
        self.ok("queue", str(self.dir), "b", SHA2)
        self.ok("yield", str(self.dir), "a", "owner", "question")
        result = self.run_cli("go", str(self.dir), "c", SHA1)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertIn("'b' is first in the queue; use queue", result.stderr)
        self.ok("queue", str(self.dir), "c", SHA1)
        self.assertEqual(self.ok("wait", str(self.dir)), f"NEXT b {SHA2}\n")
        self.ok("go", str(self.dir), "b", SHA2)
        self.assertEqual(self.read("pushes.log"), f"a go {SHA1}\nb go {SHA2}\n")

    def test_redo_reasons_joined(self):
        self.ok("redo", str(self.dir), "b", SHA1, "changes-since-review, escalated")
        self.ok("redo", str(self.dir), "a", SHA2, "wrong-branch", "feat/x")
        self.assertEqual(
            self.read("pushes.log"),
            f"b redo {SHA1} changes-since-review, escalated\na redo {SHA2} wrong-branch feat/x\n",
        )

    def test_bad_sha_exit_1(self):
        result = self.run_cli("go", str(self.dir), "a", "abc1234")
        self.assertEqual(result.returncode, 1)
        self.assertIn("40-character", result.stderr)

    def test_wait_prints_request(self):
        self.append_request(f"a push {SHA1} feat/a\n")
        result = self.run_cli("wait", str(self.dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, f"REQUEST a {SHA1} feat/a\n")

    def test_merged_more_keeps_unit(self):
        self.ok("unit", str(self.dir), "c", "--prereq", "a")
        self.ok("open-ready", str(self.dir))
        self.ok("go", str(self.dir), "a", SHA1)
        self.assertEqual(self.ok("merged", str(self.dir), "a", "7", "--more"), "")
        state = self.state()
        self.assertFalse(state["units"]["a"]["done"])
        self.assertEqual(state["units"]["a"]["prs"], {"7": "MERGED"})
        self.assertIsNone(state["slot"])
        self.assertEqual(self.ok("merged", str(self.dir), "a", "9"), "c\n")
        self.assertTrue(self.state()["units"]["a"]["done"])
        self.assertEqual(self.read("gates.log"), "a open\nb open\n")  # merged only prints; open-ready opens

    def test_set_queue_parallel_lists_the_queued_requests(self):
        self.ok("unit", str(self.dir), "c")
        self.ok("go", str(self.dir), "a", SHA1)
        self.ok("queue", str(self.dir), "b", SHA2)
        self.ok("queue", str(self.dir), "c", SHA1)
        self.assertEqual(self.ok("set", str(self.dir), "queue", "parallel"), f"queued b {SHA2}\nqueued c {SHA1}\n")
        self.ok("go", str(self.dir), "b", SHA2)
        self.assertEqual([entry["unit"] for entry in self.state()["queue"]], ["c"])
        self.assertEqual(self.ok("set", str(self.dir), "queue", "serial"), "")

    def test_stop_a_slot_holder(self):
        self.ok("go", str(self.dir), "a", SHA1)
        self.ok("queue", str(self.dir), "b", SHA2)
        self.ok("stop", str(self.dir), "a", "cancelled")
        self.assertIsNone(self.state()["slot"])
        self.assertEqual(self.ok("wait", str(self.dir)), f"NEXT b {SHA2}\n")
        for args in (["go", "a", SHA1], ["queue", "a", SHA1], ["open", "a"]):
            with self.subTest(command=args[0]):
                result = self.run_cli(args[0], str(self.dir), *args[1:])
                self.assertEqual(result.returncode, 1)
                self.assertIn("unit-stopped", result.stderr)
        self.ok("redo", str(self.dir), "a", SHA2, "unit-stopped")
        self.assertEqual(self.read("pushes.log"), f"a go {SHA1}\na redo {SHA2} unit-stopped\n")

    def test_clear_bad(self):
        self.append_request(f"junk one\njunk two\na push {SHA1} feat/a\n")
        self.ok("wait", str(self.dir))
        self.assertEqual(self.ok("clear-bad", str(self.dir)), "cleared 2 bad request lines\n")
        self.assertEqual(self.state()["bad_requests"], [])
        self.assertNotIn("bad request line", self.ok("status", str(self.dir)))
        self.assertEqual(self.ok("clear-bad", str(self.dir)), "cleared 0 bad request lines\n")

    def test_closed_frees_the_slot(self):
        self.ok("go", str(self.dir), "a", SHA1)
        self.ok("closed", str(self.dir), "a", "7")
        state = self.state()
        self.assertIsNone(state["slot"])
        self.assertEqual(state["units"]["a"]["prs"], {"7": "CLOSED"})

    def test_go_for_a_done_unit_exit_1(self):
        self.ok("merged", str(self.dir), "a", "7")
        result = self.run_cli("go", str(self.dir), "a", SHA1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("pr-merged", result.stderr)


class StatusTests(CliTestCase):
    def test_status_lists_pending_and_bad(self):
        self.init("--wip", "1")
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b")
        self.append_request(f"not a request\na push {SHA1} feat/a\n")
        self.assertEqual(self.ok("wait", str(self.dir)), f"REQUEST a {SHA1} feat/a\n")
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertEqual(lines[0], "Run my-run (owner/repo), queue serial, WIP 1")
        self.assertIn("- slot: free", lines)
        self.assertIn("- queue: empty", lines)
        self.assertIn("- needs the manager:", lines)
        needs = lines[lines.index("- needs the manager:") + 1 :]
        self.assertRegex(needs[0], rf"^  - request a {SHA1} feat/a unanswered since \d\d:\d\d$")
        self.assertIn("  - bad request line: not a request", needs)
        self.assertIn("  - gate a may open", needs)
        self.assertNotIn("  - gate b may open", needs)  # WIP 1 leaves room for one
        self.assertIn("- gated: a may open, b on WIP", lines)

    def test_status_lists_unread_requests(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("open", str(self.dir), "a")
        self.append_request(f"a push {SHA1} feat/a\n")
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertEqual(lines[-1], "  - requests.log has lines no wait has read yet: start a wait")
        self.ok("wait", str(self.dir))
        self.assertNotIn("requests.log", self.ok("status", str(self.dir)))

    def test_a_request_answered_before_wait_reads_it_is_not_unanswered(self):
        # The first wait reports a only; the manager answers a and b, and the next wait reads b's line.
        self.init()
        for name in ("a", "b"):
            self.ok("unit", str(self.dir), name)
            self.ok("open", str(self.dir), name)
        self.append_request(f"a push {SHA1} feat/a\nb push {SHA2} feat/b\n")
        self.assertEqual(self.ok("wait", str(self.dir)), f"REQUEST a {SHA1} feat/a\n")
        self.ok("redo", str(self.dir), "a", SHA1, "not", "covered")
        self.ok("redo", str(self.dir), "b", SHA2, "not", "covered")
        self.append_request(f"a push {SHA3} feat/a\n")
        self.assertEqual(self.ok("wait", str(self.dir)), f"REQUEST a {SHA3} feat/a\n")
        needs = self.ok("status", str(self.dir))
        self.assertNotIn("request b", needs)
        self.assertNotIn("requests.log", needs)

    def test_status_tracker_lines(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b", "--prereq", "a")
        self.ok("unit", str(self.dir), "c")
        self.ok("unit", str(self.dir), "d")
        self.ok("open", str(self.dir), "a")
        self.ok("open", str(self.dir), "c")
        self.ok("open", str(self.dir), "d")
        self.ok("go", str(self.dir), "c", SHA1)
        self.ok("merged", str(self.dir), "c", "4")
        self.ok("go", str(self.dir), "a", SHA1)
        self.ok("queue", str(self.dir), "d", SHA2)
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertEqual(lines[0], "Run my-run (owner/repo), queue serial, WIP none")
        self.assertRegex(lines[1], r"^- slot: a 1111111 since \d\d:\d\d$")
        self.assertEqual(lines[2], "- queue: d 2222222")
        self.assertEqual(lines[3], "- merged: c #4")
        self.assertEqual(lines[4], "- open: a working, d working")
        self.assertEqual(lines[5], "- gated: b on a")
        self.assertEqual(lines[6], "- needs the manager:")
        self.assertEqual(lines[7:], ["  - push approved for a 1111111, not seen yet"])

    def test_status_shows_merges_after(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b", "--after", "a")
        self.ok("open", str(self.dir), "b")
        self.assertIn("- open: b working, merges after a", self.ok("status", str(self.dir)).splitlines())

    def test_status_shows_titles(self):
        self.init()
        self.ok("unit", str(self.dir), "column-overrides", "--title", "8.7")
        self.ok("unit", str(self.dir), "plain")
        self.ok("unit", str(self.dir), "late", "--title", "8.9", "--prereq", "column-overrides")
        self.ok("unit", str(self.dir), "tail", "--title", "9.0", "--after", "plain", "column-overrides")
        self.ok("unit", str(self.dir), "held", "--title", "9.1", "--branch", "feat/held")
        self.ok("unit", str(self.dir), "ready", "--title", "9.2")
        self.ok("open", str(self.dir), "column-overrides")
        self.ok("open", str(self.dir), "tail")
        self.ok("open", str(self.dir), "held")
        self.ok("open", str(self.dir), "ready")
        self.ok("merged", str(self.dir), "column-overrides", "138")
        self.ok("hold", str(self.dir), "held", "later")
        state = self.state()
        state["units"]["tail"]["prs"] = {"7": "OPEN"}
        state["units"]["ready"]["prs"] = {"8": "OPEN"}
        state["marks"]["ready"] = {"pr": 8, "head": HEAD_A, "ready_head": f"8 {HEAD_A}", "ready_at": 1000.0}
        (self.dir / "run.json").write_text(json.dumps(state))
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertIn("- merged: column-overrides (8.7) #138", lines)
        self.assertIn("- open: tail (9.0) PR #7, merges after plain, held (9.1) working, ready (9.2) PR #8", lines)
        self.assertIn("- gated: plain may open, late (8.9) may open", lines)
        self.assertRegex(" ".join(lines), r"hold held \(9\.1\): later since \d\d:\d\d")
        self.assertRegex("\n".join(lines), r"(?m)^  - ready \(9\.2\) PR #8 ready since \d\d:\d\d$")

    def test_status_keeps_bare_names_where_a_sha_follows(self):
        self.init()
        self.ok("unit", str(self.dir), "a", "--title", "8.7")
        self.ok("unit", str(self.dir), "b", "--title", "8.8")
        self.ok("open", str(self.dir), "a")
        self.ok("open", str(self.dir), "b")
        self.ok("go", str(self.dir), "a", SHA1)
        self.ok("queue", str(self.dir), "b", SHA2)
        self.append_request(f"b push {SHA2} feat/b\n")
        self.ok("wait", str(self.dir))
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertRegex(lines[1], r"^- slot: a 1111111 since \d\d:\d\d$")
        self.assertEqual(lines[2], "- queue: b 2222222")
        self.assertIn("  - push approved for a 1111111, not seen yet", lines)
        self.assertRegex("\n".join(lines), rf"(?m)^  - request b {SHA2} feat/b unanswered since")
        self.ok("merged", str(self.dir), "a", "1")
        self.assertIn(f"  - slot free with a queue, next b {SHA2}", self.ok("status", str(self.dir)).splitlines())

    def test_status_keeps_bare_names_in_gate_and_behind_lines(self):
        self.init()
        self.ok("unit", str(self.dir), "a", "--title", "8.7", "--branch", "feat/a")
        self.ok("unit", str(self.dir), "b", "--title", "8.8")
        self.ok("open", str(self.dir), "a")
        self.ok("go", str(self.dir), "a", SHA1)
        state = self.state()
        state["units"]["a"]["prs"] = {"7": "OPEN"}
        state["marks"]["a"] = {"pr": 7, "head": HEAD_A, "behind_head": HEAD_A}
        (self.dir / "run.json").write_text(json.dumps(state))
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertIn("  - slot holder a PR #7 behind base", lines)
        self.assertIn("  - gate b may open", lines)

    def test_unit_title_with_a_newline_is_refused(self):
        self.init()
        result = self.run_cli("unit", str(self.dir), "a", "--title", "one\ntwo")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1)

    def test_status_next_skips_queue_entries_that_are_not_mergeable(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b", "--after", "a")
        self.ok("unit", str(self.dir), "c")
        self.ok("queue", str(self.dir), "b", SHA1)
        self.assertNotIn("slot free with a queue", self.ok("status", str(self.dir)))
        self.ok("queue", str(self.dir), "c", SHA2)
        self.assertIn(f"  - slot free with a queue, next c {SHA2}", self.ok("status", str(self.dir)).splitlines())

    def test_status_nothing_needed(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("open-ready", str(self.dir))
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertEqual(
            lines,
            [
                "Run my-run (owner/repo), queue serial, WIP none",
                "- slot: free",
                "- queue: empty",
                "- merged: none",
                "- open: a working",
                "- gated: none",
                "- needs the manager: nothing",
            ],
        )

    def test_status_gated_by_wip_and_slot_free_with_queue(self):
        self.init("--wip", "1")
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b")
        self.ok("open-ready", str(self.dir))
        self.ok("queue", str(self.dir), "a", SHA1)
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertIn("- gated: b on WIP", lines)
        self.assertIn(f"  - slot free with a queue, next a {SHA1}", lines)

    def test_hold_and_release_commands(self):
        self.init()
        self.ok("unit", str(self.dir), "u")
        self.ok("hold", str(self.dir), "u", "waiting", "on", "owner")
        status = self.ok("status", str(self.dir))
        self.assertRegex(status, r"(?m)^  - hold u: waiting on owner since \d\d:\d\d$")
        result = self.run_cli("go", str(self.dir), "u", SHA1)
        self.assertEqual(result.returncode, 1)
        self.assertIn("is held: waiting on owner", result.stderr)
        self.ok("release", str(self.dir), "u")
        self.assertNotIn("hold u", self.ok("status", str(self.dir)))
        result = self.run_cli("release", str(self.dir), "u")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertEqual(result.stdout, "")

    def test_status_omits_ready_since_for_a_held_unit(self):
        self.init()
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")
        self.ok("open", str(self.dir), "a")
        state = self.state()
        state["units"]["a"]["prs"] = {"7": "OPEN"}
        state["marks"]["a"] = {"pr": 7, "head": HEAD_A, "ready_head": f"7 {HEAD_A}", "ready_at": 1000.0}
        (self.dir / "run.json").write_text(json.dumps(state))
        self.assertIn("a PR #7 ready since", self.ok("status", str(self.dir)))
        self.ok("hold", str(self.dir), "a", "later")
        status = self.ok("status", str(self.dir))
        self.assertIn("  - hold a: later since", status)
        self.assertNotIn("ready since", status)

    def test_status_omits_ready_since_for_an_after_blocked_unit(self):
        # R1-F5
        self.init()
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")
        self.ok("unit", str(self.dir), "b", "--branch", "feat/b")
        self.ok("open", str(self.dir), "b")
        state = self.state()
        state["units"]["b"]["prs"] = {"7": "OPEN"}
        state["marks"]["b"] = {"pr": 7, "head": HEAD_A, "ready_head": f"7 {HEAD_A}", "ready_at": 1000.0}
        (self.dir / "run.json").write_text(json.dumps(state))
        self.ok("unit", str(self.dir), "b", "--after", "a")
        self.assertNotIn("ready since", self.ok("status", str(self.dir)))

    def test_status_drops_merges_after_for_a_stopped_unit(self):
        # R1-F6
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b", "--after", "a")
        self.ok("open", str(self.dir), "b")
        self.ok("stop", str(self.dir), "a", "dropped")
        self.assertIn("- open: b working", self.ok("status", str(self.dir)).splitlines())

    def test_status_ready_pr(self):
        self.init()
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")
        self.ok("unit", str(self.dir), "b")
        self.ok("open", str(self.dir), "a")
        state = self.state()  # as the watcher leaves it after reporting READY
        state["units"]["a"]["prs"] = {"7": "OPEN"}
        state["marks"]["a"] = {"pr": 7, "head": HEAD_A, "ready_head": f"7 {HEAD_A}", "ready_at": 1000.0}
        (self.dir / "run.json").write_text(json.dumps(state))
        lines = self.ok("status", str(self.dir)).splitlines()
        self.assertIn("- open: a PR #7", lines)
        self.assertIn("- gated: b may open", lines)
        self.assertRegex(lines[-2], r"^  - a PR #7 ready since \d\d:\d\d$")
        self.assertEqual(lines[-1], "  - gate b may open")

    def test_status_lists_a_slot_holder_behind_base(self):
        self.init()
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")
        self.ok("unit", str(self.dir), "b", "--branch", "feat/b")
        self.ok("go", str(self.dir), "a", SHA1)

        def status_with(marks, slot="keep", pending=False):
            state = self.state()
            state["units"]["a"]["prs"] = {"7": "OPEN"}
            state["marks"]["a"] = marks
            if slot is None:
                state["slot"] = None
            state["pending"] = [{"unit": "a", "sha": SHA2, "branch": "feat/a", "at": 1000.0}] if pending else []
            (self.dir / "run.json").write_text(json.dumps(state))
            return self.ok("status", str(self.dir)).splitlines()

        behind = {"pr": 7, "head": HEAD_A, "behind_head": HEAD_A}
        self.assertIn("  - slot holder a PR #7 behind base", status_with(behind))
        for marks, slot in (
            ({**behind, "head": SHA2}, "keep"),  # pushed since
            ({**behind, "go_head": HEAD_A}, "keep"),  # approved to push: it syncs first
            (behind, None),  # not the holder
        ):
            with self.subTest(marks=marks, slot=slot):
                self.assertNotIn("behind base", "\n".join(status_with(marks, slot)))
        # It already asked to push again (after syncing): the request is what needs the manager.
        self.assertNotIn("behind base", "\n".join(status_with(behind, pending=True)))

    def test_status_asks_a_freed_holder_to_push_once_its_after_units_merge(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b", "--branch", "feat/b")
        self.ok("go", str(self.dir), "b", SHA1)
        state = self.state()
        state["units"]["b"]["prs"] = {"7": "OPEN"}
        state["marks"]["b"] = {"pr": 7, "head": SHA1}  # pushed under its go: nothing outstanding
        (self.dir / "run.json").write_text(json.dumps(state))
        self.ok("unit", str(self.dir), "b", "--after", "a")
        line = "  - b can merge now its --after units merged: ask it to sync and push"
        self.assertNotIn(line, self.ok("status", str(self.dir)).splitlines())  # a has not merged
        self.ok("merged", str(self.dir), "a", "1")
        self.assertIn(line, self.ok("status", str(self.dir)).splitlines())
        # R1-F8: while a go is still outstanding, the child is pushing: no prompt.
        state = self.state()
        state["marks"]["b"]["go_head"] = SHA1
        (self.dir / "run.json").write_text(json.dumps(state))
        self.assertNotIn(line, self.ok("status", str(self.dir)).splitlines())
        state["marks"]["b"].pop("go_head")
        (self.dir / "run.json").write_text(json.dumps(state))
        self.assertIn(line, self.ok("status", str(self.dir)).splitlines())
        self.ok("queue", str(self.dir), "b", SHA2)  # it asked again; the request is what the manager answers
        self.assertNotIn(line, self.ok("status", str(self.dir)).splitlines())

    def test_status_ready_pr_with_a_newer_head_is_not_listed(self):
        self.init()
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")
        self.ok("open", str(self.dir), "a")
        state = self.state()
        state["units"]["a"]["prs"] = {"7": "OPEN"}
        state["marks"]["a"] = {"pr": 7, "head": SHA2, "ready_head": f"7 {HEAD_A}", "ready_at": 1000.0}
        (self.dir / "run.json").write_text(json.dumps(state))
        self.assertIn("- needs the manager: nothing", self.ok("status", str(self.dir)).splitlines())


class ErrorTests(CliTestCase):
    def test_usage_exit_2(self):
        for args in ([], ["frob"], ["go", "dir", "a"], ["init", "dir"], ["set", "dir", "colour", "red"]):
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 2, result.stderr)
                self.assertEqual(result.stdout, "")

    def test_missing_run_exit_1(self):
        result = self.run_cli("status", str(self.dir))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertIn("no run in", result.stderr)

    def test_corrupt_run_json_exit_1(self):
        self.init()
        (self.dir / "run.json").write_text("{not json")
        for args in (["status", str(self.dir)], ["go", str(self.dir), "a", SHA1]):
            with self.subTest(args=args[0]):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                self.assertIn("run.json", result.stderr)
                self.assertNotIn("Traceback", result.stderr)

    def test_run_json_of_the_wrong_shape_exit_1(self):
        self.init()
        for text in ("{}", "[]"):
            (self.dir / "run.json").write_text(text)
            for args in (["status", str(self.dir)], ["go", str(self.dir), "a", SHA1]):
                with self.subTest(text=text, command=args[0]):
                    result = self.run_cli(*args)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
                    self.assertIn("not a version 1 run state", result.stderr)
            result = self.run_cli("wait", str(self.dir))
            self.assertEqual(result.returncode, 0)
            self.assertTrue(result.stdout.startswith("ERROR "), result.stdout)

    def test_wait_on_a_broken_run_prints_error_and_exits_0(self):
        result = self.run_cli("wait", str(self.dir))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith("ERROR "), result.stdout)
        self.assertEqual(len(result.stdout.splitlines()), 1)

    def test_check_needs_a_branch(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        result = self.run_cli("check", str(self.dir), "a")
        self.assertEqual(result.returncode, 1)
        self.assertIn("no branch", result.stderr)


class GhTests(CliTestCase):
    def test_check_runs_on_the_units_branch(self):
        self.fake_gh({"pr_list": [], "graphql": None})
        self.init()
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")
        self.assertEqual(self.ok("check", str(self.dir), "a"), "NOT-READY - no open PR from feat/a\n")

    def test_wait_polls_prs_with_gh(self):
        merged = make_pr(state="MERGED")
        self.fake_gh({"pr_list": [{"number": 7}], "graphql": {"data": {"repository": {"pullRequest": merged}}}})
        self.init()
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")
        self.assertEqual(self.ok("wait", str(self.dir), "--stall-hours", "1"), "MERGED a 7\n")


if __name__ == "__main__":
    unittest.main()
