import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from helpers import git, make_repo, run
from test_ledger import finding, output, report
import review_state

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

    # helpers
    def cli(self, *argv):
        return run(self.repo, *argv, state_dir=self.state_dir)

    def call(self, *argv, repo=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main(
                [*argv, "--state-dir", str(self.state_dir), "--repo", str(repo or self.repo)]
            )
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
        snap = self.cli("snapshot")["snapshot"]
        return self.cli(
            "begin-round", "--mode", mode, "--model", "opus",
            "--snapshot", snap, "--base-ref", "main",
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

        positions = [
            text.index(h)
            for h in ("## Counts", "## Escalated", "## Deferred", "## Invalid", "## Fixed and verified")
        ]
        self.assertEqual(positions, sorted(positions))
        order = [
            text.index(t)
            for t in ("Escalated one.", "Deferred one.", "Invalid one.", "Verified one.")
        ]
        self.assertEqual(order, sorted(order))
        # entries land under their own heading
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
        self.assertNotIn("Still open one.", text)  # open findings are only counted

    def test_summary_empty_sections_say_none(self):
        self.begin()
        self.record(output(findings=[]))
        text = self.cli("summary")["text"]
        for heading in ("Escalated", "Deferred", "Invalid", "Fixed and verified"):
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

    def test_prune_treats_unreadable_state_as_stale(self):
        self.begin()
        git(self.repo, "branch", "broken")
        git(self.repo, "branch", "empty")
        broken = self.state_dir / REPO_KEY / "broken"
        broken.mkdir(parents=True)
        (broken / "review.json").write_text("{not json")
        empty = self.state_dir / REPO_KEY / "empty"
        empty.mkdir()
        result = self.cli("prune")
        self.assertEqual(sorted(result["removed"]), sorted([str(broken), str(empty)]))

    def test_prune_never_removes_current_branch(self):
        self.begin()
        current = self.current_dir()
        self.set_updated(current, days_ago(40))
        self.assertEqual(self.cli("prune")["removed"], [])
        self.assertTrue(current.exists())

    # list
    def test_list_sorted_by_updated(self):
        self.seed(
            REPO_KEY, "a", "a", updated=days_ago(3), rounds=2,
            findings=[
                {"status": "open"}, {"status": "fixed"}, {"status": "still-open"},
                {"status": "verified"}, {"status": "invalid"},
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
        self.assertEqual(self.cli("cleanup", "--branch")["removed"], [str(mine)])
        self.assertFalse(mine.exists())
        self.assertTrue(sibling.exists() and foreign.exists())

        self.assertEqual(
            self.raw_json("cleanup", "--repo", str(self.repo))["removed"], [str(sibling)]
        )
        self.assertFalse((self.state_dir / REPO_KEY).exists())  # emptied repo dir goes too
        self.assertTrue(foreign.exists())

        self.assertEqual(self.raw_json("cleanup", "--all")["removed"], [str(foreign)])
        self.assertFalse((self.state_dir / "other__repo").exists())
        self.assertTrue(self.state_dir.exists())

    def test_cleanup_all_needs_no_git_repo(self):
        foreign = self.seed("other__repo", "main", "main")
        plain = self.tmp / "plain"
        plain.mkdir()
        code, out, err = self.call("cleanup", "--all", repo=plain)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["removed"], [str(foreign)])

    def test_cleanup_repo_needs_git_repo(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        code, out, err = self.raw("cleanup", "--repo", str(plain))
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "))

    def test_cleanup_branch_needs_git_repo(self):
        plain = self.tmp / "plain"
        plain.mkdir()
        code, out, err = self.call("cleanup", "--branch", repo=plain)
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "))

    def test_cleanup_older_than(self):
        mine, sibling, foreign = self._three_dirs()
        self.set_updated(sibling, days_ago(20))
        self.set_updated(foreign, days_ago(20))
        removed = self.raw_json(
            "cleanup", "--repo", str(self.repo), "--older-than", "10"
        )["removed"]
        self.assertEqual(removed, [str(sibling)])
        self.assertTrue(mine.exists())  # fresh, so kept along with its repo dir
        removed = self.raw_json("cleanup", "--all", "--older-than", "10")["removed"]
        self.assertEqual(removed, [str(foreign)])
        self.assertTrue(mine.exists())
        self.assertEqual(self.raw_json("cleanup", "--all", "--older-than", "10")["removed"], [])

    def test_cleanup_requires_one_scope(self):
        self._three_dirs()
        for argv in (
            ("cleanup",),
            ("cleanup", "--branch", "--all"),
            ("cleanup", "--branch", "--all", "--repo", str(self.repo)),
        ):
            code, out, err = self.raw(*argv)
            self.assertEqual(code, 1, argv)
            self.assertEqual(out, "")
            self.assertTrue(err.startswith("error: "), err)
            self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertEqual(len(self.cli("list")["reviews"]), 3)

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
