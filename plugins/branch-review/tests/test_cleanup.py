import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from helpers import git, make_repo, run, sandbox_cwd
import review_state
from test_ledger import finding, output, report

REPO_KEY = "owner__repo"  # derived from make_repo's default origin


def days_ago(days):
    when = datetime.now(timezone.utc) - timedelta(days=days)
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


class CleanupTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "state"
        self.repo = make_repo(self.tmp / "r")
        # These tests run prune and cleanup: never with the checkout as cwd.
        sandbox = contextlib.ExitStack()
        self.addCleanup(sandbox.close)
        self.sandbox = sandbox.enter_context(sandbox_cwd())

    # helpers
    def cli(self, *argv):
        return run(self.repo, *argv, state_dir=self.state_dir)

    def call(self, *argv, repo=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main([*argv, "--state-dir", str(self.state_dir), "--repo", str(repo or self.repo)])
        return code, out.getvalue(), err.getvalue()

    def raw(self, *argv):
        """Run main() with only --state-dir appended (no --repo)."""
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main([*argv, "--state-dir", str(self.state_dir)])
        return code, out.getvalue(), err.getvalue()

    def raw_json(self, *argv):
        code, out, err = self.raw(*argv)
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def begin(self, mode="deep"):
        if mode == "followup":
            # An empty round is never saved, so give each follow-up a delta.
            self._changes = getattr(self, "_changes", 0) + 1
            (self.repo / f"change-{self._changes}.txt").write_text("x\n")
        snap = self.cli("snapshot")["snapshot"]
        return self.cli(
            "begin-round",
            "--mode",
            mode,
            "--model",
            "opus",
            "--snapshot",
            snap,
            "--base-ref",
            "main",
        )

    def record(self, data):
        path = self.tmp / "report.md"
        path.write_text(report(data))
        return self.cli("record-findings", "--file", str(path))

    def seed(self, rkey, bdir, branch, updated=None, rounds=1, findings=None):
        """Write a minimal review.json for a (possibly foreign) state dir."""
        d = self.state_dir / rkey / bdir
        d.mkdir(parents=True, exist_ok=True)
        state = {
            "repo_key": rkey,
            "branch": branch,
            "updated": updated or days_ago(0),
            "rounds": [{"number": i + 1, "mode": "deep"} for i in range(rounds)],
            "findings": findings or [],
        }
        (d / "review.json").write_text(json.dumps(state))
        return d

    def current_dir(self):
        return Path(self.cli("locate", "--base", "main")["dir"])

    def set_updated(self, directory, updated):
        path = Path(directory) / "review.json"
        state = json.loads(path.read_text())
        state["updated"] = updated
        path.write_text(json.dumps(state))

    # summary
    def test_summary_sections_in_order(self):
        self.begin()
        self.record(
            output(
                findings=[
                    finding(title="Verified one."),
                    finding(title="Invalid one."),
                    finding(title="Deferred one."),
                    finding(title="Escalated one."),
                    finding(title="Still open one."),
                ]
            )
        )
        self.cli("set-status", "--id", "R1-F1", "--status", "fixed", "--commit", "abc123")
        self.cli("set-status", "--id", "R1-F2", "--status", "invalid", "--note", "guarded upstream")
        self.cli("discuss", "--id", "R1-F2", "--from", "reviewer", "--text", "guard is bypassed by X")
        self.cli("set-status", "--id", "R1-F3", "--status", "escalated", "--note", "needs a call")
        self.cli("resolve", "--id", "R1-F3", "--decision", "defer", "--note", "later")
        self.cli("set-status", "--id", "R1-F4", "--status", "escalated", "--note", "design choice")
        self.begin("followup")
        self.record(
            output(
                findings=[],
                verdicts=[{"id": "R1-F1", "status": "verified", "reason": "ok"}],
            )
        )

        result = self.cli("summary")
        path = Path(result["path"])
        self.assertEqual(path, self.current_dir() / "summary.md")
        self.assertEqual(path.read_text(), result["text"])
        text = result["text"]

        header = text.splitlines()[0]
        for part in (REPO_KEY, "feature/x", "2 rounds", "followup", "opus", "low"):
            self.assertIn(part, header)

        headings = (
            "## Counts",
            "## Open and unverified",
            "## Escalated",
            "## Deferred",
            "## Invalid",
            "## Fixed and verified",
        )
        positions = [text.index(h) for h in headings]
        self.assertEqual(positions, sorted(positions))
        order = [
            text.index(t)
            for t in ("Still open one.", "Escalated one.", "Deferred one.", "Invalid one.", "Verified one.")
        ]
        self.assertEqual(order, sorted(order))
        # entries land under their own heading
        self.assertLess(text.index("## Open and unverified"), text.index("Still open one."))
        self.assertLess(text.index("Still open one."), text.index("## Escalated"))
        self.assertIn("Status: open", text)
        self.assertLess(text.index("## Escalated"), text.index("Escalated one."))
        self.assertLess(text.index("Escalated one."), text.index("## Deferred"))
        self.assertLess(text.index("Invalid one."), text.index("## Fixed and verified"))
        self.assertLess(text.index("## Fixed and verified"), text.index("Verified one."))
        # details: file:lines, notes from both sides, commit
        self.assertIn("R1-F4", text)
        self.assertIn("b.txt:1-2", text)
        self.assertIn("design choice", text)
        self.assertIn("guarded upstream", text)
        self.assertIn("guard is bypassed by X", text)
        self.assertIn("abc123", text)

    def test_summary_lists_fixed_but_unverified(self):
        self.begin()
        self.record(output(findings=[finding(title="Claimed fix.")]))
        self.cli("set-status", "--id", "R1-F1", "--status", "fixed", "--note", "guarded in b.txt")
        text = self.cli("summary")["text"]
        section = text.split("## Open and unverified")[1].split("## Escalated")[0]
        self.assertIn("Claimed fix.", section)
        self.assertIn("Status: fixed", section)
        self.assertIn("guarded in b.txt", section)

    def test_summary_empty_sections_say_none(self):
        self.begin()
        self.record(output(findings=[]))
        text = self.cli("summary")["text"]
        for heading in ("Open and unverified", "Escalated", "Deferred", "Invalid", "Fixed and verified"):
            body = text.split("## " + heading)[1].split("##")[0]
            self.assertIn("None.", body)

    def test_summary_without_state_errors(self):
        code, out, err = self.call("summary")
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("error: "))

    # prune
    def test_prune_removes_branch_missing_locally_and_remotely(self):
        self.begin()
        gone = self.seed(REPO_KEY, "feature__gone", "feature/gone")
        other = self.seed("other__repo", "feature__gone", "feature/gone")
        result = self.cli("prune")
        self.assertEqual(result["removed"], [str(gone)])
        self.assertFalse(gone.exists())
        self.assertTrue(other.exists())  # a different repo key is never touched
        self.assertTrue(self.current_dir().exists())

    def test_prune_keeps_branch_present_on_remote(self):
        self.begin()
        d = self.seed(REPO_KEY, "feature__y", "feature/y")
        git(self.repo, "update-ref", "refs/remotes/origin/feature/y", "HEAD")
        self.assertEqual(self.cli("prune")["removed"], [])
        self.assertTrue(d.exists())

    def test_prune_keeps_branch_present_locally(self):
        self.begin()
        git(self.repo, "branch", "local-only")
        d = self.seed(REPO_KEY, "local-only", "local-only")
        self.assertEqual(self.cli("prune")["removed"], [])
        self.assertTrue(d.exists())

    def test_prune_removes_stale_by_days(self):
        self.begin()
        git(self.repo, "branch", "old")
        git(self.repo, "branch", "recent")
        old = self.seed(REPO_KEY, "old", "old", updated=days_ago(31))
        recent = self.seed(REPO_KEY, "recent", "recent", updated=days_ago(29))
        result = self.cli("prune")
        self.assertEqual(result["removed"], [str(old)])
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())

    def test_prune_days_option(self):
        self.begin()
        git(self.repo, "branch", "old")
        old = self.seed(REPO_KEY, "old", "old", updated=days_ago(5))
        self.assertEqual(self.cli("prune")["removed"], [])
        self.assertEqual(self.cli("prune", "--days", "3")["removed"], [str(old)])

    def _corrupt(self, name):
        d = self.state_dir / REPO_KEY / name
        d.mkdir(parents=True)
        (d / "review.json").write_text("{not json")
        return d

    def test_prune_keeps_corrupt_state_when_branch_is_live(self):
        self.begin()
        git(self.repo, "branch", "feature/live")
        git(self.repo, "update-ref", "refs/remotes/origin/on-remote", "HEAD")
        local = self._corrupt("feature__live")
        remote = self._corrupt("on-remote")
        missing = self.state_dir / REPO_KEY / "feature__live2"
        missing.mkdir()  # no review.json at all, branch absent
        git(self.repo, "branch", "feature/live2")
        result = self.cli("prune")
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["skipped"], [str(missing)])  # not review state at all
        self.assertTrue(local.exists() and remote.exists() and missing.exists())

    def test_prune_removes_corrupt_state_when_branch_is_gone(self):
        self.begin()
        broken = self._corrupt("feature__gone")
        empty = self.state_dir / REPO_KEY / "gone-empty"
        empty.mkdir()
        rounds_only = self.state_dir / REPO_KEY / "gone-rounds"
        (rounds_only / "rounds").mkdir(parents=True)
        result = self.cli("prune")
        self.assertEqual(sorted(result["removed"]), sorted([str(broken), str(rounds_only)]))
        self.assertEqual(result["skipped"], [str(empty)])
        self.assertFalse(broken.exists() or rounds_only.exists())
        self.assertTrue(empty.exists())

    def test_prune_skips_dirs_that_are_not_review_state(self):
        self.begin()
        stray = self.state_dir / REPO_KEY / "gone-stray"
        (stray / "nested").mkdir(parents=True)
        (stray / "nested" / "keep.txt").write_text("x")
        result = self.cli("prune")
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["skipped"], [str(stray)])
        self.assertTrue((stray / "nested" / "keep.txt").exists())

    def test_prune_keeps_unparseable_updated_when_branch_is_live(self):
        self.begin()
        git(self.repo, "branch", "odd")
        d = self.seed(REPO_KEY, "odd", "odd", updated="yesterday-ish")
        self.assertEqual(self.cli("prune")["removed"], [])
        self.assertTrue(d.exists())

    def test_prune_keeps_dir_without_readable_branch_name(self):
        self.begin()
        git(self.repo, "branch", "a__b")
        d = self.seed(REPO_KEY, "a__b", "a__b")
        state = json.loads((d / "review.json").read_text())
        del state["branch"]
        (d / "review.json").write_text(json.dumps(state))
        result = self.cli("prune")
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["skipped"], [str(d)])
        self.assertTrue(d.exists())

    def test_prune_checks_exact_branch_names_not_globs(self):
        self.begin()
        git(self.repo, "branch", "feature/live")
        star = self.seed(REPO_KEY, "feature__star", "feature/*")
        result = self.cli("prune")
        self.assertEqual(result["removed"], [str(star)])

    def test_live_branch_keys_git_failure_is_an_error(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        with self.assertRaises(review_state.StateError):
            review_state._live_branch_keys(plain)

    def test_prune_detached_dirs_only_by_age(self):
        self.begin()
        fresh = self.seed(REPO_KEY, "detached-1234567", "HEAD")
        old = self.seed(REPO_KEY, "detached-89abcde", "HEAD", updated=days_ago(31))
        fresh_corrupt = self._corrupt("detached-aaaaaaa")
        old_corrupt = self._corrupt("detached-bbbbbbb")
        stamp = (datetime.now(timezone.utc) - timedelta(days=31)).timestamp()
        os.utime(str(old_corrupt / "review.json"), (stamp, stamp))
        result = self.cli("prune")
        self.assertEqual(sorted(result["removed"]), sorted([str(old), str(old_corrupt)]))
        self.assertTrue(fresh.exists() and fresh_corrupt.exists())

    def test_prune_records_check_failures_and_keeps_going(self):
        self.begin()
        first = self.seed(REPO_KEY, "a-gone", "a-gone")
        broken = self.seed(REPO_KEY, "b-check", "b-check")
        last = self.seed(REPO_KEY, "c-gone", "c-gone")
        real = review_state._ref_exists

        def flaky(repo, ref):
            if ref.endswith("/b-check"):
                raise review_state.StateError(f"cannot check {ref}: simulated failure")
            return real(repo, ref)

        with mock.patch.object(review_state, "_ref_exists", side_effect=flaky):
            code, out, err = self.call("prune")
        self.assertEqual(code, 1, err)
        result = json.loads(out)
        self.assertEqual(result["removed"], [str(first), str(last)])
        self.assertEqual(
            result["errors"], [{"dir": str(broken), "error": "cannot check refs/heads/b-check: simulated failure"}]
        )
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertTrue(broken.exists())

    def test_prune_detached_prefix_needs_a_sha(self):
        self.begin()
        branch_like = self.seed(REPO_KEY, "detached-ui", "detached-ui")  # a real branch name, now gone
        sha_like = self.seed(REPO_KEY, "detached-1234567", "HEAD")
        result = self.cli("prune")
        self.assertEqual(result["removed"], [str(branch_like)])
        self.assertTrue(sha_like.exists())

    def test_symlinked_repo_key_dir_is_skipped(self):
        outside = self.tmp / "outside-repo-dir"
        for name, branch in (("feature__gone", "feature/gone"), ("feature__x", "feature/x")):
            (outside / name).mkdir(parents=True)
            (outside / name / "review.json").write_text(json.dumps({"branch": branch, "updated": days_ago(90)}))
        self.state_dir.mkdir()
        link = self.state_dir / REPO_KEY
        link.symlink_to(outside, target_is_directory=True)
        for argv in (("prune",), ("cleanup", "--scope", "repo"), ("cleanup", "--scope", "branch")):
            with self.subTest(command=argv):
                code, out, err = self.call(*argv)
                self.assertEqual(code, 0, err)
                result = json.loads(out)
                self.assertEqual(result, {"removed": [], "skipped": [str(link)], "errors": []})
        self.assertTrue(link.is_symlink())
        self.assertTrue((outside / "feature__gone" / "review.json").is_file())
        self.assertTrue((outside / "feature__x" / "review.json").is_file())

    def _read_only(self, directory):
        """Make `directory`'s contents undeletable until the test ends."""
        directory.chmod(0o500)
        self.addCleanup(directory.chmod, 0o700)

    def test_prune_keeps_going_after_a_removal_error(self):
        self.begin()
        first = self.seed(REPO_KEY, "a-gone", "a-gone")
        stuck = self.seed(REPO_KEY, "b-gone", "b-gone")
        (stuck / "rounds").mkdir()
        (stuck / "rounds" / "1.md").write_text("x")
        last = self.seed(REPO_KEY, "c-gone", "c-gone")
        try:
            self._read_only(stuck / "rounds")
            code, out, err = self.call("prune")
        finally:
            (stuck / "rounds").chmod(0o700)
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertEqual(result["removed"], [str(first), str(last)])
        self.assertEqual([e["dir"] for e in result["errors"]], [str(stuck)])
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertFalse(first.exists() or last.exists())

    def test_cleanup_keeps_going_after_a_removal_error(self):
        mine, sibling, foreign = self._three_dirs()
        (mine / "rounds").mkdir()
        (mine / "rounds" / "1.md").write_text("x")
        try:
            self._read_only(mine / "rounds")
            code, out, err = self.call("cleanup", "--scope", "all")
        finally:
            (mine / "rounds").chmod(0o700)
        self.assertEqual(code, 1)
        result = json.loads(out)
        self.assertEqual(sorted(result["removed"]), sorted([str(sibling), str(foreign)]))
        self.assertEqual([e["dir"] for e in result["errors"]], [str(mine)])
        self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_prune_and_cleanup_report_no_errors_when_clean(self):
        self.begin()
        self.assertEqual(self.cli("prune")["errors"], [])
        self.assertEqual(self.cli("cleanup", "--scope", "repo")["errors"], [])

    def test_symlinked_children_are_skipped_not_followed(self):
        self.begin()
        outside = self.tmp / "outside"
        target_branch = outside / "owner__repo" / "feature__z"
        target_branch.mkdir(parents=True)
        (target_branch / "review.json").write_text(json.dumps({"branch": "feature/z", "updated": days_ago(90)}))
        branch_link = self.state_dir / REPO_KEY / "feature__link"
        branch_link.symlink_to(target_branch, target_is_directory=True)
        repo_link = self.state_dir / "linked__repo"
        repo_link.symlink_to(outside / "owner__repo", target_is_directory=True)
        result = self.cli("prune")
        self.assertIn(str(branch_link), result["skipped"])
        result = self.cli("cleanup", "--scope", "all")
        self.assertIn(str(branch_link), result["skipped"])
        self.assertIn(str(repo_link), result["skipped"])
        self.assertNotIn(str(branch_link), result["removed"])
        self.assertTrue(branch_link.is_symlink() and repo_link.is_symlink())
        self.assertTrue((target_branch / "review.json").is_file())

    def test_prune_never_removes_current_branch(self):
        self.begin()
        current = self.current_dir()
        self.set_updated(current, days_ago(40))
        self.assertEqual(self.cli("prune")["removed"], [])
        self.assertTrue(current.exists())

    # list
    def test_list_sorted_by_updated(self):
        self.seed(
            REPO_KEY,
            "a",
            "a",
            updated=days_ago(3),
            rounds=2,
            findings=[
                {"status": "open"},
                {"status": "fixed"},
                {"status": "still-open"},
                {"status": "verified"},
                {"status": "invalid"},
            ],
        )
        self.seed("other__repo", "b", "b", updated=days_ago(1))
        self.seed(REPO_KEY, "c", "c", updated=days_ago(10))
        bad = self.state_dir / REPO_KEY / "bad"
        bad.mkdir()
        (bad / "review.json").write_text("{nope")
        entries = self.cli("list")["reviews"]
        self.assertEqual([e["branch"] for e in entries], ["b", "a", "c", "bad"])
        a = entries[1]
        self.assertEqual(a["repo_key"], REPO_KEY)
        self.assertEqual(a["rounds"], 2)
        self.assertEqual(a["open"], 3)
        self.assertEqual(a["dir"], str(self.state_dir / REPO_KEY / "a"))
        self.assertEqual(a["updated"], days_ago(3))
        self.assertNotIn("error", a)
        broken = entries[3]
        self.assertIsNone(broken["rounds"])
        self.assertIn("error", broken)
        self.assertEqual(broken["repo_key"], REPO_KEY)

    def test_list_reports_malformed_state_as_error_entries(self):
        self.seed(REPO_KEY, "good", "good", updated=days_ago(1))
        odd = self.seed(REPO_KEY, "odd", "odd")
        state = json.loads((odd / "review.json").read_text())
        state["updated"] = 12345
        (odd / "review.json").write_text(json.dumps(state))
        arr = self.state_dir / REPO_KEY / "arr"
        arr.mkdir()
        (arr / "review.json").write_text("[1, 2]")
        entries = self.cli("list")["reviews"]
        self.assertEqual(entries[0]["branch"], "good")
        errors = {e["dir"]: e for e in entries if "error" in e}
        self.assertEqual(sorted(errors), sorted([str(odd), str(arr)]))
        self.assertIsNone(errors[str(odd)]["updated"])

    def test_list_empty_when_no_state_dir(self):
        self.assertEqual(self.cli("list"), {"reviews": []})

    # cleanup
    def _three_dirs(self):
        self.begin()
        mine = self.current_dir()
        sibling = self.seed(REPO_KEY, "feature__y", "feature/y")
        foreign = self.seed("other__repo", "main", "main")
        return mine, sibling, foreign

    def test_cleanup_branch_repo_all(self):
        mine, sibling, foreign = self._three_dirs()
        self.assertEqual(self.cli("cleanup", "--scope", "branch")["removed"], [str(mine)])
        self.assertFalse(mine.exists())
        self.assertTrue(sibling.exists() and foreign.exists())

        self.assertEqual(self.cli("cleanup", "--scope", "repo")["removed"], [str(sibling)])
        self.assertFalse((self.state_dir / REPO_KEY).exists())  # emptied repo dir goes too
        self.assertTrue(foreign.exists())

        self.assertEqual(self.cli("cleanup", "--scope", "all")["removed"], [str(foreign)])
        self.assertFalse((self.state_dir / "other__repo").exists())
        self.assertTrue(self.state_dir.exists())

    def test_cleanup_all_needs_no_git_repo(self):
        foreign = self.seed("other__repo", "main", "main")
        plain = self.tmp / "plain"
        plain.mkdir()
        code, out, err = self.call("cleanup", "--scope", "all", repo=plain)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["removed"], [str(foreign)])

    def test_cleanup_repo_and_branch_need_git_repo(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        for scope in ("repo", "branch"):
            code, out, err = self.call("cleanup", "--scope", scope, repo=plain)
            self.assertEqual(code, 1, scope)
            self.assertEqual(out, "")
            self.assertTrue(err.startswith("error: "))

    def test_cleanup_older_than(self):
        mine, sibling, foreign = self._three_dirs()
        self.set_updated(sibling, days_ago(20))
        self.set_updated(foreign, days_ago(20))
        removed = self.cli("cleanup", "--scope", "repo", "--older-than", "10")["removed"]
        self.assertEqual(removed, [str(sibling)])
        self.assertTrue(mine.exists())  # fresh, so kept along with its repo dir
        removed = self.cli("cleanup", "--scope", "all", "--older-than", "10")["removed"]
        self.assertEqual(removed, [str(foreign)])
        self.assertTrue(mine.exists())
        removed = self.cli("cleanup", "--scope", "all", "--older-than", "10")["removed"]
        self.assertEqual(removed, [])

    def test_cleanup_older_than_keeps_unknown_age(self):
        self.begin()
        odd = self.seed(REPO_KEY, "odd", "odd", updated="not a time")
        corrupt = self._corrupt("corrupt")
        for scope in ("repo", "all"):
            removed = self.cli("cleanup", "--scope", scope, "--older-than", "1")["removed"]
            self.assertEqual(removed, [], scope)
        self.assertTrue(odd.exists() and corrupt.exists())
        # without an age filter they are removed like any other dir
        removed = self.cli("cleanup", "--scope", "repo")["removed"]
        self.assertIn(str(odd), removed)
        self.assertIn(str(corrupt), removed)

    def test_cleanup_requires_one_scope(self):
        self._three_dirs()
        for argv in (
            ("cleanup",),
            ("cleanup", "--scope", "bogus"),
            ("cleanup", "--scope"),
            ("cleanup", "--scope", "all", "--scope", "bogus"),
            ("cleanup", "--all"),
            ("cleanup", "--repo", str(self.repo)),
        ):
            code, out, err = self.raw(*argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(out, "")
            self.assertTrue(err.startswith("error: "), err)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertEqual(len(self.cli("list")["reviews"]), 3)

    def test_cleanup_skips_dirs_that_are_not_review_state(self):
        mine, sibling, foreign = self._three_dirs()
        stray_here = self.state_dir / REPO_KEY / "stray"
        (stray_here / "src").mkdir(parents=True)
        (stray_here / "src" / "keep.py").write_text("x")
        stray_there = self.state_dir / "not-a-repo-key" / "project"
        (stray_there / ".git").mkdir(parents=True)
        rounds_only = self.seed("other__repo", "x", "x")
        (rounds_only / "review.json").unlink()
        (rounds_only / "rounds").mkdir()

        result = self.cli("cleanup", "--scope", "repo")
        self.assertEqual(result["removed"], [str(mine), str(sibling)])
        self.assertEqual(result["skipped"], [str(stray_here)])

        result = self.cli("cleanup", "--scope", "all")
        self.assertEqual(sorted(result["removed"]), sorted([str(foreign), str(rounds_only)]))
        self.assertEqual(sorted(result["skipped"]), sorted([str(stray_here), str(stray_there)]))
        self.assertTrue((stray_here / "src" / "keep.py").exists())
        self.assertTrue((stray_there / ".git").is_dir())

    def test_cleanup_branch_skips_current_dir_without_state(self):
        self.begin()
        mine = self.current_dir()
        (mine / "review.json").unlink()
        (mine / "notes.txt").write_text("x")
        result = self.cli("cleanup", "--scope", "branch")
        self.assertEqual(result, {"removed": [], "skipped": [str(mine)], "errors": []})
        self.assertTrue((mine / "notes.txt").exists())

    def test_remove_refuses_paths_outside_state_dir(self):
        outside = self.tmp / "elsewhere"
        outside.mkdir()
        (outside / "keep.txt").write_text("x")
        self.state_dir.mkdir()
        with self.assertRaises(review_state.StateError):
            review_state._remove_dir(self.state_dir, outside)
        with self.assertRaises(review_state.StateError):
            review_state._remove_dir(self.state_dir, self.state_dir)
        self.assertTrue((outside / "keep.txt").exists())


if __name__ == "__main__":
    unittest.main()
