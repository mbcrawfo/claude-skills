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
        self.assertEqual(lines[6], "- items: none")
        self.assertEqual(lines[7], "- needs the manager:")
        self.assertEqual(lines[8:], ["  - push approved for a 1111111, not seen yet"])

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
                "- items: none",
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

    def test_unit_refuses_none_mixed_with_unit_names_in_after(self):
        # R4-F4: `--after none` clears the list and is given alone.
        self.init()
        self.ok("unit", str(self.dir), "none")
        self.ok("unit", str(self.dir), "a")
        result = self.run_cli("unit", str(self.dir), "b", "--after", "a", "none")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(len(result.stderr.splitlines()), 1)
        self.assertIn("give it alone", result.stderr)
        self.assertNotIn("b", self.state()["units"])

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
        # R4-F2: its re-ask read by a wait is a pending request; the request line is what needs the manager.
        state["pending"] = [{"unit": "b", "sha": SHA2, "branch": "feat/b", "at": 1000.0}]
        (self.dir / "run.json").write_text(json.dumps(state))
        status = self.ok("status", str(self.dir)).splitlines()
        self.assertNotIn(line, status)
        self.assertTrue(any(entry.startswith("  - request b ") for entry in status))
        state["pending"] = []
        (self.dir / "run.json").write_text(json.dumps(state))
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


class IntegrationCliTests(CliTestCase):
    """A run with the integration mode: unit `int` is the integration unit on integration/x, `a` an ordinary unit."""

    def setUp(self):
        super().setUp()
        site = self.tmp / "site"  # a check polls twice, 30 seconds apart: skip the wait in the child process
        site.mkdir()
        (site / "sitecustomize.py").write_text("import time\ntime.sleep = lambda seconds: None\n")
        self.env["PYTHONPATH"] = str(site)

    def init_integration(self, final_merge="owner", feature_review="no"):
        self.init("--integration", "integration/x", "--final-merge", final_merge, "--feature-review", feature_review)
        self.ok("unit", str(self.dir), "int", "--integration")
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")

    def status(self):
        return self.ok("status", str(self.dir)).splitlines()

    def refused(self, *args):
        result = self.run_cli(*args)
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual(len(result.stderr.splitlines()), 1, result.stderr)
        return result.stderr

    def test_init_refusals(self):
        d = str(self.dir)
        base = ("init", d, "--repo", "o/r", "--base", "main", "--method", "squash")
        for extra in (
            ("--final-merge", "owner"),
            ("--feature-review", "yes"),
            ("--integration", "integration/x"),
            ("--integration", "integration/x", "--final-merge", "owner"),
            ("--integration", "integration/x", "--feature-review", "no"),
        ):
            with self.subTest(extra=extra):
                self.refused(*base, *extra)
                self.assertFalse(self.dir.exists())

    def test_init_refuses_the_rebase_method(self):
        d = str(self.dir)
        args = ("init", d, "--repo", "o/r", "--base", "main", "--method", "rebase")
        stderr = self.refused(
            *args, "--integration", "integration/x", "--final-merge", "owner", "--feature-review", "no"
        )
        self.assertIn("use merge or squash", stderr)
        self.assertFalse(self.dir.exists())
        self.ok(*args)

    def test_init_stores_the_choices_as_a_bool(self):
        self.init_integration("manager", "yes")
        block = self.state()["integration"]
        self.assertEqual(
            block, {"branch": "integration/x", "final_merge": "manager", "feature_review": True, "final_gate": False}
        )
        unit = self.state()["units"]["int"]
        self.assertEqual((unit["integration"], unit["branch"]), (True, "integration/x"))

    def test_status_has_the_integration_line(self):
        self.init_integration()
        lines = self.status()
        gated = next(i for i, line in enumerate(lines) if line.startswith("- gated:"))
        self.assertEqual(
            lines[gated + 2], "- integration: integration/x into main · final merge: owner · feature review: no"
        )

    def test_a_default_run_has_no_integration_line(self):
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("open-ready", str(self.dir))
        self.assertFalse([line for line in self.status() if "integration" in line or "final" in line])

    def test_set_final_merge_and_feature_review(self):
        self.init_integration()
        self.ok("set", str(self.dir), "final-merge", "manager")
        self.ok("set", str(self.dir), "feature-review", "yes")
        self.assertIn("final merge: manager · feature review: yes", "\n".join(self.status()))
        self.refused("set", str(self.dir), "final-merge", "nobody")
        self.refused("set", str(self.dir), "feature-review", "maybe")

    def test_set_is_refused_without_the_mode_and_once_the_final_gate_is_open(self):
        self.init()
        self.assertIn("integration", self.refused("set", str(self.dir), "final-merge", "owner"))
        d = self.tmp / "run2"
        self.dir = d
        self.init_integration()
        self.ok("open-ready", str(d))
        self.ok("merged", str(d), "a", "7")
        self.assertEqual(self.ok("open-ready", str(d)), "int final no-review\n")
        self.assertIn("final gate", self.refused("set", str(d), "final-merge", "manager"))
        self.refused("set", str(d), "feature-review", "yes")

    def test_set_final_merge_prints_no_queued_lines_in_parallel_mode(self):
        self.init_integration()
        self.ok("set", str(self.dir), "queue", "parallel")
        state = self.state()
        state["queue"] = [{"unit": "a", "sha": SHA1, "at": 1000.0, "front": False}]
        (self.dir / "run.json").write_text(json.dumps(state))
        self.assertEqual(self.ok("set", str(self.dir), "queue", "parallel"), f"queued a {SHA1}\n")
        self.assertEqual(self.ok("set", str(self.dir), "final-merge", "owner"), "")
        self.assertEqual(self.ok("set", str(self.dir), "feature-review", "yes"), "")

    def test_unit_integration_needs_the_mode(self):
        self.init()
        self.assertIn("integration", self.refused("unit", str(self.dir), "int", "--integration"))

    def test_open_ready_opens_the_final_gate_after_the_last_unit_merges(self):
        self.init_integration()
        self.assertEqual(self.ok("open-ready", str(self.dir)), "int\na\n")
        self.assertEqual(self.ok("open-ready", str(self.dir)), "")
        self.assertEqual(self.ok("merged", str(self.dir), "a", "7"), "")
        self.assertIn("  - final gate for int may open: run open-ready", self.status())
        self.assertEqual(self.ok("open-ready", str(self.dir)), "int final no-review\n")
        self.assertEqual(self.read("gates.log").splitlines()[-1], "int final no-review")
        self.assertNotIn("final gate for int may open", "\n".join(self.status()))
        self.assertEqual(self.ok("open-ready", str(self.dir)), "")

    def test_the_final_line_says_review_when_feature_review_is_yes(self):
        self.init_integration(feature_review="yes")
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.assertEqual(self.ok("open-ready", str(self.dir)), "int final review\n")

    def test_every_unit_stopped_asks_the_owner(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.ok("stop", str(self.dir), "a", "dropped")
        self.assertIn(
            "  - every unit stopped: ask the owner what to do with integration/x",
            self.status(),
        )
        self.assertEqual(self.ok("open-ready", str(self.dir)), "")

    def test_a_stopped_integration_unit_ends_the_every_unit_stopped_ask(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.ok("stop", str(self.dir), "a", "dropped")
        self.ok("stop", str(self.dir), "int", "every unit stopped")
        self.assertNotIn("every unit stopped: ask the owner", "\n".join(self.status()))

    def test_a_landed_integration_push_points_to_synced(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.ok("go", str(self.dir), "int", SHA1)
        self.assertIn("  - push approved for int 1111111, not seen yet", self.status())
        state = self.state()
        state["marks"]["int"]["synced_sha"] = SHA1  # the watcher's mark when it printed SYNCED
        (self.dir / "run.json").write_text(json.dumps(state))
        lines = self.status()
        self.assertIn(f"  - integration push landed: run synced int {SHA1}", lines)
        self.assertFalse([line for line in lines if "not seen yet" in line], lines)
        self.ok("synced", str(self.dir), "int", SHA1)
        self.assertFalse([line for line in self.status() if "int" in line and "push" in line])

    def test_a_landed_integration_push_points_to_synced_with_the_final_pr_open(self):
        # R4-F1: the watcher popped go_head when the open final PR's head moved, so the landed line must not need it.
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        state = self.state()
        state["units"]["int"]["prs"] = {"7": "OPEN"}
        state["marks"]["int"] = {"pr": 7, "head": HEAD_A}
        (self.dir / "run.json").write_text(json.dumps(state))
        self.ok("go", str(self.dir), "int", SHA1)
        state = self.state()
        self.assertEqual(state["marks"]["int"]["go_head"], HEAD_A)
        state["marks"]["int"].pop("go_head")
        state["marks"]["int"].update(head=SHA1, synced_sha=SHA1)  # what the watcher does on the head change
        (self.dir / "run.json").write_text(json.dumps(state))
        lines = self.status()
        self.assertIn(f"  - integration push landed: run synced int {SHA1}", lines)
        self.assertFalse([line for line in lines if "not seen yet" in line], lines)
        self.ok("synced", str(self.dir), "int", SHA1)
        self.assertNotIn("- integration sync in flight", self.status())

    def test_synced_clears_the_sync_in_flight_line(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.ok("go", str(self.dir), "int", SHA1)
        self.assertIn("- integration sync in flight", self.status())
        self.ok("synced", str(self.dir), "int", SHA1)
        self.assertNotIn("- integration sync in flight", self.status())
        self.assertIn("no outstanding go", self.refused("synced", str(self.dir), "int", SHA2))

    def test_synced_is_for_the_integration_unit(self):
        self.init_integration()
        self.refused("synced", str(self.dir), "a", SHA1)
        self.refused("synced", str(self.dir), "nobody", SHA1)

    def test_a_pending_request_is_a_sync_in_flight(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        state = self.state()
        state["pending"] = [{"unit": "int", "sha": SHA1, "branch": "integration/x", "at": 1000.0}]
        (self.dir / "run.json").write_text(json.dumps(state))
        self.assertIn("- integration sync in flight", self.status())

    def open_pr(self, unit, pr, ready_sha=None, head=HEAD_A, base=None):
        state = self.state()
        state["units"][unit]["prs"][str(pr)] = "OPEN"
        marks = {"pr": pr, "head": head}
        if base:
            marks["base"] = base
        if ready_sha:
            marks |= {"ready_head": f"{pr} {ready_sha}", "ready_at": 1000.0}
        state["marks"][unit] = marks
        (self.dir / "run.json").write_text(json.dumps(state))

    def test_status_says_what_the_final_pr_waits_on(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.open_pr("int", 9)
        self.assertIn("- final PR #9 waits on a", self.status())
        self.ok("merged", str(self.dir), "a", "7")
        self.assertFalse([line for line in self.status() if "waits on" in line])

    def test_status_says_the_owner_merges_a_ready_final_pr(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.ok("open-ready", str(self.dir))
        self.open_pr("int", 9, ready_sha=HEAD_A)
        lines = self.status()
        self.assertIn("- final PR #9 ready: the owner merges it", lines)
        self.assertFalse([line for line in lines if "ready since" in line])
        self.assertIn("- needs the manager: nothing", lines)

    def test_a_stale_ready_mark_is_not_ready(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.ok("open-ready", str(self.dir))
        self.open_pr("int", 9, ready_sha=HEAD_A, head=SHA2)
        self.assertFalse([line for line in self.status() if "owner merges" in line])

    def test_a_ready_final_pr_stops_being_ready_when_a_unit_is_added(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.ok("open-ready", str(self.dir))
        self.open_pr("int", 9, ready_sha=HEAD_A)
        self.assertIn("- final PR #9 ready: the owner merges it", self.status())
        self.ok("unit", str(self.dir), "late")
        lines = self.status()
        self.assertIn("- final PR #9 waits on late", lines)
        self.assertFalse([line for line in lines if "owner merges" in line])

    def test_a_manager_merged_final_pr_is_reported_as_ready_since(self):
        self.init_integration("manager")
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.ok("open-ready", str(self.dir))
        self.open_pr("int", 9, ready_sha=HEAD_A)
        lines = self.status()
        self.assertTrue([line for line in lines if "PR #9 ready since" in line], lines)
        self.assertFalse([line for line in lines if "owner merges" in line])

    def test_no_ready_since_for_a_unit_blocked_by_the_integration_branch(self):
        self.init_integration("manager")
        self.ok("open-ready", str(self.dir))
        self.open_pr("a", 8, ready_sha=HEAD_A)
        self.assertTrue([line for line in self.status() if "PR #8 ready since" in line])
        self.ok("go", str(self.dir), "int", SHA1)
        self.assertFalse([line for line in self.status() if "PR #8 ready since" in line])
        self.ok("synced", str(self.dir), "int", SHA1)
        # The sync forgot the ready mark: the watcher reports the PR READY afresh, not REMIND half an hour on.
        self.assertNotIn("ready_head", self.state()["marks"]["a"])
        self.open_pr("a", 8, ready_sha=HEAD_A)
        self.assertTrue([line for line in self.status() if "PR #8 ready since" in line])
        self.open_pr("int", 9, ready_sha=HEAD_A)  # the integration unit waits on `a`
        self.assertFalse([line for line in self.status() if "PR #9 ready since" in line])

    def test_status_lists_a_unit_pr_on_the_wrong_base(self):
        self.init_integration("manager")
        self.ok("open-ready", str(self.dir))
        self.open_pr("a", 8, ready_sha=HEAD_A, base="main")
        lines = self.status()
        self.assertIn("  - a PR #8 targets main, not integration/x: tell its child to retarget it", lines)
        self.assertFalse([line for line in lines if "ready since" in line])
        self.open_pr("a", 8, ready_sha=HEAD_A, base="integration/x")
        lines = self.status()
        self.assertFalse([line for line in lines if "targets" in line])
        self.assertTrue([line for line in lines if "PR #8 ready since" in line])

    def test_status_lists_a_final_pr_on_the_wrong_base(self):
        self.init_integration()
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.ok("open-ready", str(self.dir))
        self.open_pr("int", 9, ready_sha=HEAD_A, base="integration/x")
        lines = self.status()
        self.assertIn("  - int PR #9 targets integration/x, not main: tell its child to retarget it", lines)
        self.assertFalse([line for line in lines if "owner merges" in line])

    def test_an_orphan_holds_the_final_pr_until_it_is_recorded(self):
        for command in ("merged", "closed"):
            with self.subTest(command=command):
                self.dir = self.tmp / f"run-{command}"
                self.init_integration()
                self.ok("open-ready", str(self.dir))
                self.ok("merged", str(self.dir), "a", "7")
                self.ok("open-ready", str(self.dir))
                self.open_pr("int", 9, ready_sha=HEAD_A)
                state = self.state()
                state["marks"]["a"] = {"orphans": [11]}
                (self.dir / "run.json").write_text(json.dumps(state))
                lines = self.status()
                self.assertIn("- final PR #9 waits on a", lines)
                self.assertFalse([line for line in lines if "owner merges" in line])
                self.ok(command, str(self.dir), "a", "11")
                lines = self.status()
                self.assertFalse([line for line in lines if "waits on" in line])
                self.assertIn("- final PR #9 ready: the owner merges it", lines)

    def clean_pr(self, base="integration/x"):
        pr = make_pr(base=base)
        self.fake_gh({"pr_list": [{"number": 7}], "graphql": {"data": {"repository": {"pullRequest": pr}}}})

    def test_check_blocks_a_unit_pr_on_the_wrong_base(self):
        self.clean_pr(base="main")
        self.init_integration("owner")
        self.ok("open-ready", str(self.dir))
        self.assertEqual(
            self.ok("check", str(self.dir), "a"),
            f"NOT-READY 7 head={HEAD_A[:7]} blocked: targets main, not integration/x\n",
        )

    def test_check_blocks_a_final_pr_on_the_wrong_base(self):
        self.clean_pr()
        self.init_integration("manager")
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.assertEqual(
            self.ok("check", str(self.dir), "int"),
            f"NOT-READY 7 head={HEAD_A[:7]} blocked: targets integration/x, not main\n",
        )

    def test_check_in_a_default_run_ignores_the_pr_base(self):
        self.clean_pr(base="elsewhere")
        self.init()
        self.ok("unit", str(self.dir), "a", "--branch", "feat/a")
        self.assertEqual(self.ok("check", str(self.dir), "a"), f"READY 7 {HEAD_A}\n")

    def test_check_reports_final_ready_for_an_owner_merged_final_pr(self):
        self.clean_pr(base="main")
        self.init_integration("owner")
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.assertEqual(self.ok("check", str(self.dir), "int"), f"FINAL-READY 7 {HEAD_A}\n")

    def test_check_reports_ready_for_a_manager_merged_final_pr(self):
        self.clean_pr(base="main")
        self.init_integration("manager")
        self.ok("open-ready", str(self.dir))
        self.ok("merged", str(self.dir), "a", "7")
        self.assertEqual(self.ok("check", str(self.dir), "int"), f"READY 7 {HEAD_A}\n")

    def test_check_blocks_the_final_pr_while_a_unit_is_open(self):
        self.clean_pr(base="main")
        self.init_integration("owner")
        self.ok("open-ready", str(self.dir))
        self.assertEqual(self.ok("check", str(self.dir), "int"), f"NOT-READY 7 head={HEAD_A[:7]} blocked: waits on a\n")

    def test_check_blocks_a_unit_while_a_sync_is_in_flight(self):
        self.clean_pr()
        self.init_integration("owner")
        self.ok("open-ready", str(self.dir))
        self.ok("go", str(self.dir), "int", SHA1)
        self.assertEqual(
            self.ok("check", str(self.dir), "a"),
            f"NOT-READY 7 head={HEAD_A[:7]} blocked: integration sync in flight\n",
        )
        self.ok("synced", str(self.dir), "int", SHA1)
        self.assertEqual(self.ok("check", str(self.dir), "a"), f"READY 7 {HEAD_A}\n")

    def test_check_blocks_a_unit_on_a_syncing_line_no_wait_has_read(self):
        self.clean_pr()
        self.init_integration("owner")
        self.ok("open-ready", str(self.dir))
        self.append_request(f"int syncing {SHA1} integration/x\n")
        self.assertEqual(
            self.ok("check", str(self.dir), "a"),
            f"NOT-READY 7 head={HEAD_A[:7]} blocked: integration sync in flight\n",
        )
        self.assertIn("- integration sync in flight", self.ok("status", str(self.dir)).splitlines())

    def test_status_lists_no_ready_unit_while_a_syncing_line_is_unread(self):
        self.fake_gh({"pr_list": [], "graphql": None})
        self.init_integration("owner")
        self.ok("open-ready", str(self.dir))
        self.open_pr("a", 7, ready_sha=HEAD_A)
        ready = "  - a PR #7 ready since "
        self.assertTrue([line for line in self.status() if line.startswith(ready)])
        self.append_request(f"int syncing {SHA1} integration/x\n")
        self.assertFalse([line for line in self.status() if line.startswith(ready)])

    def test_status_says_when_an_announced_sync_began(self):
        self.fake_gh({"pr_list": [], "graphql": None})
        self.init_integration("owner")
        state = self.state()
        state["marks"]["int"] = {"syncing": {"sha": SHA1, "at": 1000.0}}
        (self.dir / "run.json").write_text(json.dumps(state))
        lines = self.ok("status", str(self.dir)).splitlines()
        prefix = "- integration sync in flight (resolving a conflicted merge of main since "
        self.assertTrue([line for line in lines if line.startswith(prefix)], lines)

    def test_check_leaves_other_answers_alone(self):
        self.fake_gh({"pr_list": [], "graphql": None})
        self.init_integration("owner")
        self.assertEqual(self.ok("check", str(self.dir), "int"), "NOT-READY - no open PR from integration/x\n")


class ItemCliTests(CliTestCase):
    def setUp(self):
        super().setUp()
        self.init()
        self.ok("unit", str(self.dir), "a")
        self.ok("unit", str(self.dir), "b")

    def body_file(self, text, name="body.md"):
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return str(path)

    def item_add(self, unit, ref, *extra):
        path = self.body_file(f"text of {unit} {ref}", f"body-{unit}-{ref}.md")
        return self.ok(
            "item",
            "add",
            str(self.dir),
            unit,
            "--ref",
            ref,
            "--kind",
            "question",
            "--blocks",
            "no",
            "--summary",
            f"{unit} {ref}",
            "--body-file",
            path,
            *extra,
        )

    def answer(self, item_id, text="keep the retry"):
        return self.ok("item", "done", str(self.dir), item_id, "--answer-file", self.body_file(text, "ans.md"))

    def test_add_current_done_flow(self):
        out = self.item_add("a", "Q1")
        self.assertTrue(out.startswith("current a-1 (Q1) \u00b7 a \u00b7 question \u00b7 blocks merge: no"))
        self.assertTrue(out.endswith("\n"))
        self.assertEqual(self.item_add("b", "Q1"), "queued b-1 for b, 1 waiting\n")
        self.assertIn("text of a Q1", self.ok("item", "current", str(self.dir)))
        out = self.answer("a-1")
        self.assertEqual(out.splitlines()[-1], "forward due a a-1")
        self.assertEqual((self.dir / "items" / "a-1.answer.md").read_text(encoding="utf-8"), "keep the retry")

    def test_current_with_nothing_open(self):
        self.assertEqual(self.ok("item", "current", str(self.dir)), "none\n")
        self.assertEqual(self.ok("item", "list", str(self.dir)), "none\n")

    def test_depends_and_run_wide(self):
        self.item_add("a", "Q1")
        self.item_add("a", "Q2", "--depends", "Q1")
        self.assertEqual(self.state()["items"]["list"][1]["depends"], ["a-1"])
        path = self.body_file("pick one")
        out = self.ok(
            "item", "add", str(self.dir), "--run-wide", "--kind", "decision", "--blocks", "no",
            "--summary", "which way", "--body-file", path,
        )  # fmt: skip
        self.assertEqual(out, "queued run-1 for run, 2 waiting\n")  # a-2 and run-1

    def test_status_lists_forward_due(self):
        self.item_add("a", "Q1")
        self.item_add("b", "Q1")
        self.answer("a-1")
        status = self.ok("status", str(self.dir))
        self.assertIn("- items: current b-1 (Q1) b Q1 \u00b7 nothing more", status)
        self.assertIn("  - forward due a a-1", status)
        self.assertEqual(self.ok("item", "forwarded", str(self.dir), "a"), "forwarded a-1\n")
        self.assertNotIn("forward due", self.ok("status", str(self.dir)))

    def test_status_counts_by_unit_in_queue_order(self):
        self.item_add("a", "Q1")
        self.item_add("b", "Q1")
        self.item_add("a", "Q2")
        self.item_add("b", "Q2")
        self.ok("item", "add", str(self.dir), "--run-wide", "--kind", "decision", "--blocks", "no",
                "--summary", "d", "--body-file", self.body_file("x"))  # fmt: skip
        status = self.ok("status", str(self.dir))
        self.assertIn("- items: current a-1 (Q1) a Q1 \u00b7 4 more: a \u00d71, b \u00d72, run \u00d71", status)

    def test_status_with_no_items(self):
        status = self.ok("status", str(self.dir))
        self.assertIn("- items: none", status)
        self.assertNotIn("- parked:", status)
        self.assertLess(status.index("- gated:"), status.index("- items:"))

    def test_status_parked_line(self):
        self.item_add("a", "Q1")
        self.ok("item", "park", str(self.dir), "a-1", "ask", "Sam")
        status = self.ok("status", str(self.dir))
        self.assertIn("- items: none", status)
        self.assertRegex(status, r"- parked: a-1 \(Q1\) a Q1, ask Sam, since \d\d:\d\d\n")

    def test_park_unpark_and_drop(self):
        self.item_add("a", "Q1")
        self.item_add("b", "Q1")
        out = self.ok("item", "park", str(self.dir), "a-1", "ask", "Sam")
        self.assertEqual(out.splitlines()[0], "parked a-1")
        self.assertTrue(out.splitlines()[1].startswith("b-1 (Q1)"))
        self.assertTrue(self.ok("item", "unpark", str(self.dir), "a-1").startswith("a-1 (Q1)"))
        self.assertEqual(self.state()["items"]["resume"], "b-1")
        self.ok("item", "drop", str(self.dir), "b-1", "no", "longer", "needed")
        item = self.state()["items"]["list"][1]
        self.assertEqual((item["status"], item["reason"]), ("dropped", "no longer needed"))

    def test_unpark_queue(self):
        self.item_add("a", "Q1")
        self.ok("item", "park", str(self.dir), "a-1", "later")
        out = self.ok("item", "unpark", str(self.dir), "a-1", "--queue")
        self.assertTrue(out.startswith("current a-1 (Q1)"))

    def test_park_with(self):
        self.item_add("a", "Q1")
        self.item_add("a", "Q2")
        out = self.ok("item", "park", str(self.dir), "a-1", "ask", "Sam", "--with", "a-2")
        self.assertEqual(out.splitlines()[:2], ["parked a-1", "parked a-2"])

    def test_stop_drops_items_and_promotes(self):
        self.item_add("a", "Q1")
        self.item_add("a", "Q2")
        self.item_add("b", "Q1")
        self.assertEqual(self.ok("stop", str(self.dir), "a", "cancelled"), "dropped a-1\ndropped a-2\n")
        self.assertEqual(self.state()["items"]["current"], "b-1")

    def test_a_new_worktree_drops_the_old_sessions_items(self):
        self.ok("unit", str(self.dir), "a", "--worktree", "/w/a1")
        self.item_add("a", "Q1")
        self.item_add("a", "Q2")
        self.answer("a-1")  # a-1 answered, a-2 current
        self.item_add("b", "Q1")
        self.assertEqual(self.ok("unit", str(self.dir), "a", "--worktree", "/w/a1/"), "")  # same path
        self.assertEqual(self.ok("unit", str(self.dir), "a", "--worktree", "/w/a2"), "dropped a-1\ndropped a-2\n")
        self.assertEqual(self.state()["items"]["list"][0]["reason"], "new session")
        self.item_add("a", "Q1")  # the new session's Q1
        lines = self.ok("item", "list", str(self.dir)).splitlines()
        self.assertEqual([line.split(" ", 2)[:2] for line in lines], [["current", "b-1"], ["queued", "a-3"]])
        self.assertNotIn("forward due", self.ok("status", str(self.dir)))

    def test_forwarded_refuses_a_unit_that_is_not_forward_due(self):
        self.item_add("a", "Q1")
        self.item_add("a", "Q2")
        self.answer("a-1")
        before = self.read("run.json")
        result = self.run_cli("item", "forwarded", str(self.dir), "a")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(
            result.stderr, "run.py item forwarded: unit 'a' is not forward due: it still has a current or queued item\n"
        )
        self.assertEqual(self.read("run.json"), before)

    def test_stop_without_items_prints_nothing(self):
        self.assertEqual(self.ok("stop", str(self.dir), "a", "cancelled"), "")

    def test_add_missing_body_file_changes_nothing(self):
        before = self.read("run.json")
        result = self.run_cli(
            "item", "add", str(self.dir), "a", "--ref", "Q1", "--kind", "question",
            "--blocks", "no", "--summary", "s", "--body-file", str(self.tmp / "nope.md"),
        )  # fmt: skip
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("run.py item add: "), result.stderr)
        self.assertEqual(self.read("run.json"), before)
        self.assertFalse((self.dir / "items").exists())

    def test_done_missing_answer_file_changes_nothing(self):
        self.item_add("a", "Q1")
        before = self.read("run.json")
        result = self.run_cli("item", "done", str(self.dir), "a-1", "--answer-file", str(self.tmp / "nope.md"))
        self.assertEqual(result.returncode, 1)
        self.assertTrue(result.stderr.startswith("run.py item done: "), result.stderr)
        self.assertEqual(self.read("run.json"), before)

    def test_refusals_name_the_subcommand(self):
        result = self.run_cli("item", "unpark", str(self.dir), "zz-9")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "run.py item unpark: unknown item 'zz-9'\n")

    def test_unit_and_run_wide_are_exclusive(self):
        common = ["--kind", "decision", "--blocks", "no", "--summary", "s", "--body-file", self.body_file("x")]
        both = self.run_cli("item", "add", str(self.dir), "a", "--run-wide", *common)
        neither = self.run_cli("item", "add", str(self.dir), *common)
        for result in (both, neither):
            self.assertEqual(result.returncode, 1)
            self.assertTrue(result.stderr.startswith("run.py item add: "), result.stderr)
        self.assertEqual(self.state()["items"]["list"], [])

    def test_usage_errors_exit_2(self):
        self.assertEqual(self.run_cli("item", str(self.dir)).returncode, 2)
        self.assertEqual(self.run_cli("item").returncode, 2)

    def test_list_order(self):
        self.item_add("a", "Q1")
        self.item_add("b", "Q1")
        self.item_add("b", "Q2")
        self.item_add("a", "Q2")
        self.ok("item", "park", str(self.dir), "a-2", "ask", "Sam")
        self.item_add("a", "Q3")
        self.answer("a-1")  # the owner stays on unit a: a-3 current
        lines = self.ok("item", "list", str(self.dir)).splitlines()
        self.assertEqual([line.split(" ", 2)[0] + " " + line.split(" ", 2)[1] for line in lines],
                         ["current a-3", "queued b-1", "queued b-2", "parked a-2", "answered a-1"])  # fmt: skip
        self.assertRegex(lines[3], r" \u00b7 ask Sam since \d\d:\d\d$")
        self.assertTrue(lines[4].endswith(" (not forwarded)"))

    def test_list_keeps_each_unit_together(self):
        self.ok("unit", str(self.dir), "c")
        self.item_add("a", "Q1")  # current
        self.item_add("b", "Q1")
        self.item_add("c", "Q1")
        self.item_add("b", "Q2")
        lines = self.ok("item", "list", str(self.dir)).splitlines()
        self.assertEqual([line.split(" ", 2)[1] for line in lines], ["a-1", "b-1", "b-2", "c-1"])

    def test_old_run_json_without_items(self):
        state = self.state()
        del state["items"]
        for unit in state["units"].values():
            del unit["generation"]
        (self.dir / "run.json").write_text(json.dumps(state), encoding="utf-8")
        self.assertEqual(self.ok("item", "list", str(self.dir)), "none\n")
        self.assertTrue(self.item_add("a", "Q1").startswith("current a-1"))
        self.assertEqual(self.state()["units"]["a"]["generation"], 1)


if __name__ == "__main__":
    unittest.main()
