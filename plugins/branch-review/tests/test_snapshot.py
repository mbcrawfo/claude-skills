import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from helpers import commit_file, git, make_repo, run
import review_state
from test_ledger import finding, output, report

IDENTITY_VARS = ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL", "EMAIL")


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "state"
        self.repo = make_repo(self.tmp / "r")

    def snapshot(self):
        return run(self.repo, "snapshot", state_dir=self.state_dir)

    def begin(self, *extra, mode="deep", snapshot=None, base_ref="main"):
        if snapshot is None:
            snapshot = self.snapshot()["snapshot"]
        base = ["--base-ref", base_ref] if base_ref is not None else []
        return run(
            self.repo,
            "begin-round",
            "--mode",
            mode,
            "--model",
            "test-model",
            "--snapshot",
            snapshot,
            *base,
            *extra,
            state_dir=self.state_dir,
        )

    def record(self, findings=()):
        """Record a reviewer report for the latest round."""
        path = self.tmp / "report.md"
        path.write_text(report(output(findings=list(findings))))
        return run(self.repo, "record-findings", "--file", str(path), state_dir=self.state_dir)

    def locate(self, *extra, base="main"):
        args = ["--base", base] if base is not None else []
        return run(self.repo, "locate", *args, *extra, state_dir=self.state_dir)

    def review_json(self):
        return json.loads((review_state.state_path(self.state_dir, self.repo) / "review.json").read_text())

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main([*argv, "--state-dir", str(self.state_dir), "--repo", str(self.repo)])
        return code, out.getvalue(), err.getvalue()

    def test_snapshot_clean_tree_is_head(self):
        result = self.snapshot()
        self.assertEqual(result["snapshot"], git(self.repo, "rev-parse", "HEAD"))
        self.assertFalse(result["dirty"])

    def test_snapshot_includes_untracked_file(self):
        (self.repo / "new.txt").write_text("fresh\n")
        (self.repo / "b.txt").write_text("changed\n")
        status_before = git(self.repo, "status", "--porcelain")
        refs_before = git(self.repo, "for-each-ref")
        result = self.snapshot()
        self.assertTrue(result["dirty"])
        self.assertNotEqual(result["snapshot"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(git(self.repo, "show", result["snapshot"] + ":new.txt"), "fresh")
        self.assertEqual(git(self.repo, "show", result["snapshot"] + ":b.txt"), "changed")
        self.assertEqual(git(self.repo, "status", "--porcelain"), status_before)
        self.assertEqual(git(self.repo, "for-each-ref"), refs_before)

    def test_snapshot_honors_gitignore(self):
        commit_file(self.repo, ".gitignore", "ignored.txt\n", "ignore")
        (self.repo / "ignored.txt").write_text("x\n")
        (self.repo / "kept.txt").write_text("y\n")
        result = self.snapshot()
        tree = git(self.repo, "ls-tree", "--name-only", result["snapshot"])
        self.assertIn("kept.txt", tree.splitlines())
        self.assertNotIn("ignored.txt", tree.splitlines())

    def test_begin_round_deep_uses_merge_base(self):
        result = self.begin()
        self.assertEqual(result["number"], 1)
        self.assertEqual(result["diff_from"], git(self.repo, "merge-base", "HEAD", "main"))
        self.assertEqual(result["changed_files"], ["b.txt"])
        self.assertEqual(result["files"], 1)
        self.assertEqual(result["lines"], 1)
        self.assertFalse(result["oversized"])
        self.assertFalse(result["empty"])
        self.assertTrue(result["saved"])
        self.assertEqual(result["merge_base"], result["diff_from"])
        state = json.loads((Path(self.locate()["dir"]) / "review.json").read_text())
        self.assertEqual(len(state["rounds"]), 1)
        rnd = state["rounds"][0]
        self.assertEqual(rnd["head"], git(self.repo, "rev-parse", "HEAD"))
        self.assertEqual(rnd["merge_base"], git(self.repo, "merge-base", "HEAD", "main"))
        self.assertIsNone(rnd["summary"])
        self.assertIsNone(rnd["merge_risk"])
        self.assertEqual(state["findings"], [])
        self.assertTrue(state["created"].endswith("Z"))
        self.assertTrue(state["updated"].endswith("Z"))

    def test_begin_round_stores_intent(self):
        intent = self.tmp / "intent.txt"
        intent.write_text("Make b exist\n")
        self.begin("--intent-file", str(intent))
        state = json.loads((Path(self.locate()["dir"]) / "review.json").read_text())
        self.assertEqual(state["intent"].strip(), "Make b exist")

    def test_begin_round_on_base_branch_is_empty(self):
        git(self.repo, "checkout", "main")
        result = self.begin()
        self.assertTrue(result["empty"])
        self.assertFalse(result["saved"])
        self.assertEqual(result["changed_files"], [])

    def test_begin_round_followup_uses_previous_snapshot(self):
        first = self.begin()
        self.record()
        (self.repo / "c.txt").write_text("c\n")
        snap = self.snapshot()["snapshot"]
        second = self.begin(mode="followup", snapshot=snap)
        self.assertEqual(second["number"], 2)
        self.assertEqual(second["diff_from"], first["diff_to"])
        self.assertEqual(second["diff_to"], snap)
        self.assertEqual(second["changed_files"], ["c.txt"])

    def test_begin_round_followup_without_previous_round_fails(self):
        snap = self.snapshot()["snapshot"]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = review_state.main(
                [
                    "begin-round",
                    "--mode",
                    "followup",
                    "--model",
                    "m",
                    "--snapshot",
                    snap,
                    "--base-ref",
                    "main",
                    "--state-dir",
                    str(self.state_dir),
                    "--repo",
                    str(self.repo),
                ]
            )
        self.assertEqual(code, 1)
        self.assertEqual(len(err.getvalue().strip().splitlines()), 1)
        self.assertIn("no recorded round", err.getvalue())

    def test_begin_round_followup_after_unrecorded_round_fails(self):
        self.begin()
        snap = self.snapshot()["snapshot"]
        code, out, err = self.call("begin-round", "--mode", "followup", "--model", "m", "--snapshot", snap)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertIn("no recorded round", err)

    def test_locate_followup_after_commit(self):
        self.begin()
        self.record()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.locate()
        self.assertEqual(result["mode"], "followup")
        self.assertEqual(result["reason"], "continuing")
        self.assertEqual(result["rounds"], 1)

    def test_locate_deep_after_amend(self):
        self.begin()
        self.record()
        git(self.repo, "commit", "--amend", "-m", "rewritten")
        result = self.locate()
        self.assertEqual(result["mode"], "deep")
        self.assertEqual(result["reason"], "history-rewritten")

    def test_locate_deep_when_snapshot_gone(self):
        self.begin()
        self.record()
        path = Path(self.locate()["dir"]) / "review.json"
        state = json.loads(path.read_text())
        state["rounds"][-1]["snapshot"] = "0" * 40
        path.write_text(json.dumps(state))
        result = self.locate()
        self.assertEqual(result["mode"], "deep")
        self.assertEqual(result["reason"], "snapshot-missing")

    def test_oversized_flag(self):
        for i in range(151):
            (self.repo / f"f{i:03d}.txt").write_text("x\n")
        result = self.begin()
        self.assertEqual(result["files"], 152)
        self.assertTrue(result["oversized"])

    def test_not_oversized_at_threshold(self):
        for i in range(149):
            (self.repo / f"f{i:03d}.txt").write_text("x\n")
        result = self.begin()
        self.assertEqual(result["files"], 150)
        self.assertFalse(result["oversized"])

    def test_lines_respect_paths_filter(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "keep.py").write_text("k\n" * 3)
        (self.repo / "big.txt").write_text("x\n" * 10001)
        (self.repo / "blob.bin").write_bytes(b"\x00\x01\x02" * 10)
        everything = self.begin()
        self.assertTrue(everything["oversized"])
        narrowed = self.begin("--paths", "src/*", "*.bin")
        self.assertEqual(narrowed["changed_files"], ["blob.bin", "src/keep.py"])
        self.assertEqual(narrowed["files"], 2)
        self.assertEqual(narrowed["lines"], 3)  # binary counts 0
        self.assertFalse(narrowed["oversized"])

    def test_snapshot_ignores_gpg_signing_and_missing_identity(self):
        git(self.repo, "config", "commit.gpgSign", "true")
        git(self.repo, "config", "gpg.program", "/nonexistent/gpg")
        (self.repo / "new.txt").write_text("fresh\n")
        result = self.snapshot()
        self.assertTrue(result["dirty"])
        obj = git(self.repo, "cat-file", "-p", result["snapshot"])
        self.assertNotIn("gpgsig", obj)

    def committer(self, sha):
        return git(self.repo, "log", "-1", "--format=%cn <%ce>|%an <%ae>", sha)

    def test_snapshot_keeps_configured_identity(self):
        (self.repo / "new.txt").write_text("fresh\n")
        with mock.patch.dict(os.environ):
            for var in IDENTITY_VARS:
                os.environ.pop(var, None)
            result = self.snapshot()
        self.assertTrue(result["dirty"])
        self.assertEqual(
            self.committer(result["snapshot"]),
            "Test User <test@example.com>|Test User <test@example.com>",
        )

    def test_snapshot_without_git_identity_uses_fallback(self):
        git(self.repo, "config", "--unset", "user.name")
        git(self.repo, "config", "--unset", "user.email")
        git(self.repo, "config", "user.useConfigOnly", "true")  # no auto-detected identity either
        (self.repo / "new.txt").write_text("fresh\n")
        with mock.patch.dict(os.environ, {"GIT_CONFIG_NOSYSTEM": "1"}):
            for var in IDENTITY_VARS:
                os.environ.pop(var, None)
            result = self.snapshot()
        self.assertTrue(result["dirty"])
        self.assertEqual(
            self.committer(result["snapshot"]),
            "branch-review <branch-review@localhost>|branch-review <branch-review@localhost>",
        )

    def test_snapshot_does_not_rewrite_the_index(self):
        index = self.repo / ".git" / "index"
        # Make the index stat-dirty without changing content; a plain `git status`
        # would refresh and rewrite it.
        stamp = index.stat().st_mtime + 5
        os.utime(str(self.repo / "a.txt"), (stamp, stamp))
        before = index.read_bytes()
        self.assertFalse(self.snapshot()["dirty"])
        self.assertEqual(index.read_bytes(), before)

    def test_round_head_comes_from_the_snapshot(self):
        before = git(self.repo, "rev-parse", "HEAD")
        (self.repo / "c.txt").write_text("c\n")
        snap = self.snapshot()
        self.assertTrue(snap["dirty"])
        self.assertEqual(snap["head"], before)
        commit_file(self.repo, "d.txt", "d\n", "commit between snapshot and begin-round")
        result = self.begin(snapshot=snap["snapshot"])
        self.assertEqual(result["head"], before)
        self.assertEqual(self.review_json()["rounds"][-1]["head"], before)

    def test_real_commit_with_snapshot_message_is_its_own_head(self):
        commit_file(self.repo, "c.txt", "c\n", review_state.SNAPSHOT_MESSAGE)
        sha = git(self.repo, "rev-parse", "HEAD")
        snap = self.snapshot()
        self.assertEqual((snap["snapshot"], snap["dirty"], snap["head"]), (sha, False, sha))
        self.assertEqual(self.begin(snapshot=sha)["head"], sha)

    def test_round_head_of_clean_snapshot_is_the_snapshot(self):
        snap = self.snapshot()["snapshot"]
        commit_file(self.repo, "d.txt", "d\n", "commit between snapshot and begin-round")
        self.assertEqual(self.begin(snapshot=snap)["head"], snap)

    def test_begin_round_rejects_non_utf8_intent_file(self):
        intent = self.tmp / "intent.txt"
        intent.write_bytes(b"\xff\xfe intent")
        snap = self.snapshot()["snapshot"]
        code, out, err = self.call(
            "begin-round", "--mode", "deep", "--model", "m", "--snapshot", snap, "--base-ref", "main",
            "--intent-file", str(intent),
        )  # fmt: skip
        self.assertEqual((code, out), (1, ""))
        self.assertIn("not valid UTF-8", err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_begin_round_unknown_base_ref(self):
        self.begin()
        self.record()
        before = self.review_json()
        (self.repo / "c.txt").write_text("c\n")
        snap = self.snapshot()["snapshot"]
        for mode in ("deep", "followup"):
            with self.subTest(mode=mode):
                code, out, err = self.call(
                    "begin-round", "--mode", mode, "--model", "m", "--snapshot", snap, "--base-ref", "nope",
                )  # fmt: skip
                self.assertEqual((code, out), (1, ""))
                self.assertEqual(err.strip(), "error: base ref not found: nope")
                self.assertEqual(self.review_json(), before)

    def test_malformed_anchor_round_is_a_one_line_error(self):
        self.begin()
        self.record()
        path = review_state.state_path(self.state_dir, self.repo) / "review.json"
        good = path.read_text()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        snap = self.snapshot()["snapshot"]
        for key in ("snapshot", "head", "merge_base"):
            state = json.loads(good)
            del state["rounds"][-1][key]
            path.write_text(json.dumps(state))
            for argv in (
                ("locate", "--base", "main"),
                ("begin-round", "--mode", "followup", "--model", "m", "--snapshot", snap, "--base-ref", "main"),
            ):
                with self.subTest(key=key, command=argv[0]):
                    code, out, err = self.call(*argv)
                    self.assertEqual((code, out), (1, ""), err)
                    self.assertIn(f"round 1 is missing {key}", err)
                    self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_save_state_writes_through_a_unique_temp_file(self):
        directory = review_state.state_path(self.state_dir, self.repo)
        (directory / "review.json.tmp").mkdir(parents=True)  # the old fixed temp name is taken
        with mock.patch.object(review_state, "_now", return_value="2026-01-01T00:00:00Z"):
            review_state.save_state(directory, {"rounds": []})
        first = self.review_json()["updated"]
        with mock.patch.object(review_state, "_now", return_value="2026-01-02T00:00:00Z"):
            review_state.save_state(directory, {"rounds": []})
        self.assertEqual(first, "2026-01-01T00:00:00Z")
        self.assertEqual(self.review_json()["updated"], "2026-01-02T00:00:00Z")
        self.assertEqual(sorted(p.name for p in directory.iterdir()), ["review.json", "review.json.tmp"])

    def test_oversized_by_lines(self):
        (self.repo / "big.txt").write_text("x\n" * 9999)
        at_limit = self.begin()
        self.assertEqual(at_limit["lines"], 10000)  # b.txt adds the 10000th line
        self.assertFalse(at_limit["oversized"])
        (self.repo / "big.txt").write_text("x\n" * 10000)
        over = self.begin()
        self.assertEqual((over["files"], over["lines"]), (2, 10001))
        self.assertTrue(over["oversized"])

    def test_locate_followup_after_dirty_dangling_snapshot(self):
        (self.repo / "c.txt").write_text("c\n")
        snap = self.snapshot()
        self.assertTrue(snap["dirty"])
        self.begin(snapshot=snap["snapshot"])
        self.record()
        self.assertEqual(git(self.repo, "branch", "--contains", snap["snapshot"]), "")  # dangling
        located = self.locate()
        self.assertEqual((located["mode"], located["reason"]), ("followup", "continuing"))
        commit_file(self.repo, "c.txt", "c2\n", "add c")
        located = self.locate()
        self.assertEqual((located["mode"], located["reason"]), ("followup", "continuing"))

    def test_paths_dot_means_no_filter(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "keep.py").write_text("k\n")
        for patterns in (["."], ["./"], ["/"], [".", "src"], ["./."]):
            with self.subTest(patterns=patterns):
                self.assertEqual(review_state.normalize_paths(self.repo, patterns), [])
        result = self.begin("--paths", ".")
        self.assertEqual(result["changed_files"], ["b.txt", "src/keep.py"])
        self.assertEqual(result["paths"], [])

    def test_paths_filter(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "keep.py").write_text("k\n")
        result = self.begin("--paths", "src/*")
        self.assertEqual(result["changed_files"], ["src/keep.py"])
        self.assertEqual(result["files"], 1)

    # unrecorded rounds
    def test_unrecorded_round_does_not_anchor_followup(self):
        self.begin()  # the reviewer never reported, so record-findings never ran
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.locate()
        self.assertEqual((result["mode"], result["reason"]), ("deep", "no-rounds"))
        self.assertEqual(result["rounds"], 0)

    def test_unrecorded_round_is_dropped_by_next_begin(self):
        first = self.begin()
        self.record()
        self.begin(mode="followup")  # never recorded
        commit_file(self.repo, "c.txt", "c\n", "add c")
        located = self.locate()
        self.assertEqual((located["mode"], located["reason"]), ("followup", "continuing"))
        self.assertEqual(located["rounds"], 1)
        again = self.begin(mode="followup")
        self.assertEqual(again["number"], 2)
        self.assertEqual(again["diff_from"], first["diff_to"])
        self.assertEqual(again["changed_files"], ["c.txt"])
        self.assertEqual([r["number"] for r in self.review_json()["rounds"]], [1, 2])

    def test_empty_first_round_is_not_saved(self):
        git(self.repo, "checkout", "-b", "feature/empty", "main")
        result = self.begin()
        self.assertTrue(result["empty"])
        self.assertFalse(result["saved"])
        self.assertFalse((review_state.state_path(self.state_dir, self.repo) / "review.json").exists())
        commit_file(self.repo, "c.txt", "c\n", "add c")
        located = self.locate()
        self.assertEqual((located["mode"], located["reason"]), ("deep", "no-state"))
        self.assertEqual(self.begin()["changed_files"], ["c.txt"])

    def test_empty_followup_round_is_not_saved(self):
        self.begin()
        self.record()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        self.begin(mode="followup")  # never recorded
        git(self.repo, "reset", "--hard", "HEAD~1")
        result = self.begin(mode="followup")
        self.assertTrue(result["empty"])
        self.assertFalse(result["saved"])
        self.assertEqual(result["number"], 2)
        rounds = self.review_json()["rounds"]
        self.assertEqual([r["number"] for r in rounds], [1])  # the unrecorded round is dropped too
        self.assertEqual(run(self.repo, "status", state_dir=self.state_dir)["reason"], "converged")

    # base ref
    def test_locate_and_begin_round_reuse_stored_base_ref(self):
        git(self.repo, "branch", "develop", "main")
        self.begin(base_ref="develop")
        self.record()
        self.assertEqual(self.locate(base=None)["base"], "develop")
        self.assertEqual(self.locate(base="main")["base"], "main")
        result = self.begin(mode="followup", base_ref=None)
        self.assertEqual(result["base_ref"], "develop")
        self.assertEqual(self.review_json()["base_ref"], "develop")

    def test_begin_round_without_base_ref_or_state_uses_default_base(self):
        result = self.begin(base_ref=None)
        self.assertEqual(result["base_ref"], "main")
        self.assertEqual(result["diff_from"], git(self.repo, "merge-base", "HEAD", "main"))

    # base moved
    def merge_main_cleanly(self):
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "upstream.txt", "u\n", "upstream work")
        git(self.repo, "checkout", "feature/x")
        git(self.repo, "merge", "--no-edit", "main")

    def conflicting_base_update(self, path="b.txt"):
        """Review the branch, then merge a main that adds `path` too, with other content.

        Main also adds `upstream.txt`, a change only the base makes. The branch
        adds `path` first when it does not have it yet.
        """
        if not (self.repo / path).exists():
            commit_file(self.repo, path, "branch\n", "branch adds the file")
        self.begin()
        self.record(findings=[finding()])
        git(self.repo, "checkout", "main")
        (self.repo / path).parent.mkdir(parents=True, exist_ok=True)
        (self.repo / path).write_text("main\n")
        commit_file(self.repo, "upstream.txt", "u\n", "main adds the file")
        git(self.repo, "checkout", "feature/x")
        merge = subprocess.run(["git", "merge", "main"], cwd=str(self.repo), capture_output=True, text=True)
        self.assertNotEqual(merge.returncode, 0)
        (self.repo / path).write_text("resolved\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "--no-edit")

    def test_locate_followup_when_base_merged_cleanly(self):
        self.begin()
        self.record(findings=[finding()])
        self.merge_main_cleanly()
        result = self.locate()
        self.assertEqual((result["mode"], result["reason"]), ("followup", "base-updated"))
        self.assertEqual([f["id"] for f in result["open_findings"]], ["R1-F1"])

    def test_followup_after_clean_base_update_reports_only_branch_work(self):
        first = self.begin()
        self.record(findings=[finding()])
        self.merge_main_cleanly()
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.begin(mode="followup")
        self.assertEqual(result["changed_files"], ["c.txt"])
        parents = git(self.repo, "rev-list", "--parents", "-n1", result["diff_from"]).split()[1:]
        self.assertEqual(parents, [first["snapshot"], git(self.repo, "merge-base", "HEAD", "main")])

    def test_followup_after_clean_base_update_alone_is_empty(self):
        self.begin()
        self.record(findings=[finding()])
        self.merge_main_cleanly()
        result = self.begin(mode="followup")
        self.assertTrue(result["empty"])
        self.assertFalse(result["saved"])

    def test_conflicting_base_merge_gives_merge_check(self):
        self.conflicting_base_update()
        result = self.locate()
        self.assertEqual((result["mode"], result["reason"]), ("merge-check", "base-moved"))
        self.assertEqual(result["conflicts"], ["b.txt"])
        self.assertEqual([f["id"] for f in result["open_findings"]], ["R1-F1"])
        deep = self.begin()
        self.assertEqual(deep["changed_files"], ["b.txt"])  # upstream.txt is not part of the change

    def test_locate_conflicts_empty_outside_merge_check(self):
        self.assertEqual(self.locate()["conflicts"], [])
        self.begin()
        self.record()
        self.merge_main_cleanly()
        result = self.locate()
        self.assertEqual(result["mode"], "followup")
        self.assertEqual(result["conflicts"], [])

    def test_merge_check_scope_includes_branch_work_and_excludes_base_only_files(self):
        self.conflicting_base_update()
        anchor = self.review_json()["rounds"][-1]
        commit_file(self.repo, "fix.txt", "f\n", "fix alongside the merge")
        result = self.begin(mode="merge-check")
        self.assertEqual(result["mode"], "merge-check")
        self.assertEqual(result["diff_from"], anchor["snapshot"])
        self.assertEqual(result["changed_files"], ["b.txt", "fix.txt"])
        self.assertEqual(result["conflicts"], ["b.txt"])
        self.assertEqual(result["upstream_overlap"], ["b.txt"])
        self.assertTrue(result["saved"])
        stored = self.review_json()["rounds"][-1]
        self.assertEqual((stored["mode"], stored["conflicts"]), ("merge-check", ["b.txt"]))
        self.assertNotIn("upstream_overlap", stored)

    def test_merge_check_keeps_branch_deletion_since_anchor(self):
        self.conflicting_base_update(path="c.txt")
        git(self.repo, "rm", "-q", "b.txt")
        git(self.repo, "commit", "-m", "branch drops its own b")
        result = self.begin(mode="merge-check")
        self.assertIn("b.txt", result["changed_files"])
        self.assertEqual(result["changed_files"], ["b.txt", "c.txt"])
        self.assertNotIn("upstream.txt", result["changed_files"])

    def test_merge_check_keeps_branch_revert_since_anchor(self):
        commit_file(self.repo, "a.txt", "branch\n", "branch edits a")
        self.conflicting_base_update(path="c.txt")
        commit_file(self.repo, "a.txt", "a\n", "branch reverts its a edit")
        result = self.begin(mode="merge-check")
        self.assertIn("a.txt", result["changed_files"])
        self.assertEqual(result["changed_files"], ["a.txt", "c.txt"])

    def test_modify_delete_conflict_is_listed(self):
        git(self.repo, "checkout", "main")
        git(self.repo, "merge", "--ff-only", "feature/x")  # main now has b.txt
        git(self.repo, "checkout", "feature/x")
        git(self.repo, "rm", "-q", "b.txt")
        git(self.repo, "commit", "-m", "branch deletes b")
        self.begin()
        self.record()
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "b.txt", "main edit\n", "main modifies b")
        git(self.repo, "checkout", "feature/x")
        merge = subprocess.run(["git", "merge", "main"], cwd=str(self.repo), capture_output=True, text=True)
        self.assertNotEqual(merge.returncode, 0)
        git(self.repo, "rm", "-q", "b.txt")
        git(self.repo, "commit", "--no-edit")
        located = self.locate()
        self.assertEqual((located["mode"], located["conflicts"]), ("merge-check", ["b.txt"]))
        result = self.begin(mode="merge-check")
        self.assertEqual(result["conflicts"], ["b.txt"])
        self.assertEqual(result["changed_files"], ["b.txt"])
        self.assertTrue(result["saved"])

    def test_conflict_path_with_space_and_unicode(self):
        path = "dir a/\u00e9.txt"
        self.conflicting_base_update(path=path)
        self.assertEqual(self.locate()["conflicts"], [path])
        result = self.begin(mode="merge-check")
        self.assertEqual(result["conflicts"], [path])
        self.assertEqual(result["changed_files"], [path])

    def test_merge_check_falls_back_to_deep_when_merge_tree_fails(self):
        self.conflicting_base_update()
        with mock.patch.object(review_state, "base_conflicts", return_value=None):
            result = self.locate()
            self.assertEqual((result["mode"], result["reason"]), ("deep", "base-moved"))
            self.assertEqual(result["conflicts"], [])
            snapshot = self.snapshot()["snapshot"]
            code, out, err = self.call(
                "begin-round", "--mode", "merge-check", "--model", "m", "--snapshot", snapshot, "--base-ref", "main"
            )
        self.assertEqual((code, out), (1, ""))
        message = "cannot start a merge-check round: the base update cannot be simulated; run a deep round"
        self.assertEqual(err.strip(), "error: " + message)

    def test_merge_check_without_recorded_round_fails(self):
        snapshot = self.snapshot()["snapshot"]
        code, out, err = self.call(
            "begin-round", "--mode", "merge-check", "--model", "m", "--snapshot", snapshot, "--base-ref", "main"
        )
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(err.strip(), "error: cannot start a merge-check round: no recorded round")

    def test_paths_change_forces_deep_over_merge_check(self):
        self.conflicting_base_update()
        result = self.locate("--paths", "b.txt")
        self.assertEqual((result["mode"], result["reason"]), ("deep", "paths-changed"))
        self.assertEqual(result["conflicts"], [])

    def test_base_conflicts_conflicted_clean_and_failed(self):
        self.conflicting_base_update()
        anchor = self.review_json()["rounds"][-1]
        new_base = git(self.repo, "merge-base", "HEAD", "main")
        self.assertEqual(review_state.base_conflicts(self.repo, anchor, new_base), ["b.txt"])
        self.assertEqual(review_state.base_conflicts(self.repo, anchor, anchor["merge_base"]), [])
        self.assertIsNone(review_state.base_conflicts(self.repo, dict(anchor, merge_base="0" * 40), new_base))

    def test_begin_followup_rejects_conflicting_base_update(self):
        self.conflicting_base_update()
        snapshot = self.snapshot()["snapshot"]
        code, _, err = self.call(
            "begin-round", "--mode", "followup", "--model", "test-model", "--snapshot", snapshot, "--base-ref", "main"
        )
        self.assertEqual(code, 1)
        self.assertIn("conflicts", err)

    def test_simulate_base_update_without_merge_tree_support_is_none(self):
        self.begin()
        self.record()
        self.merge_main_cleanly()
        anchor = self.review_json()["rounds"][-1]
        new_base = git(self.repo, "merge-base", "HEAD", "main")
        self.assertIsNotNone(review_state.simulate_base_update(self.repo, anchor, new_base))
        real = review_state._git_try

        def fake(repo, *args):
            return None if "merge-tree" in args else real(repo, *args)

        with mock.patch.object(review_state, "_git_try", fake):
            self.assertIsNone(review_state.simulate_base_update(self.repo, anchor, new_base))

    def test_locate_followup_when_base_advances_without_merge(self):
        self.begin()
        self.record()
        git(self.repo, "checkout", "main")
        commit_file(self.repo, "upstream.txt", "u\n", "upstream work")
        git(self.repo, "checkout", "feature/x")
        commit_file(self.repo, "c.txt", "c\n", "add c")
        result = self.locate()
        self.assertEqual((result["mode"], result["reason"]), ("followup", "continuing"))

    # paths
    def test_locate_deep_when_paths_change(self):
        (self.repo / "src").mkdir()
        (self.repo / "src" / "keep.py").write_text("k\n")
        self.begin("--paths", "src/*")
        self.record(findings=[finding()])
        same = self.locate("--paths", "./src/*")
        self.assertEqual((same["mode"], same["reason"]), ("followup", "continuing"))
        for extra in ((), ("--paths", "lib/*"), ("--paths", "src/*", "lib/*")):
            with self.subTest(paths=extra):
                result = self.locate(*extra)
                self.assertEqual((result["mode"], result["reason"]), ("deep", "paths-changed"))
                self.assertEqual([f["id"] for f in result["open_findings"]], ["R1-F1"])

    def test_paths_directory_forms_match_nested_files(self):
        (self.repo / "src" / "a").mkdir(parents=True)
        (self.repo / "src" / "a" / "b.py").write_text("b\n")
        (self.repo / "srcfile.py").write_text("s\n")
        for pattern in ("src/", "src", "./src/"):
            with self.subTest(pattern=pattern):
                result = self.begin("--paths", pattern)
                self.assertEqual(result["changed_files"], ["src/a/b.py"])
                self.assertEqual(result["paths"], ["src/**"])
        self.record()
        for pattern in ("src", "src/", "./src"):
            with self.subTest(locate=pattern):
                result = self.locate("--paths", pattern)
                self.assertEqual((result["mode"], result["reason"]), ("followup", "continuing"))


if __name__ == "__main__":
    unittest.main()
