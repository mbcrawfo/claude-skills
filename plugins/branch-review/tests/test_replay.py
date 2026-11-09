"""Tests for the small replay (evals/replay/replay.py). No test calls `claude`."""

import json
from pathlib import Path
import re
import shutil
import tempfile
import unittest

import helpers  # noqa: F401  (puts the evals dirs on sys.path)
import replay
import usage_report

SKILL = Path(__file__).resolve().parent.parent / "skills" / "review" / "SKILL.md"


def run_obj(cost=1.0, dispatch=None):
    return usage_report.ReviewerRun(
        path=Path("/t/agent-1.jsonl"),
        started="2026-11-01T00:00:00Z",
        model="opus",
        cost=cost,
        turns=3,
        peak_context=1000,
        tool_calls=2,
        dispatch=dispatch or {"repo": "/r"},
    )


def rnd(number, **kw):
    base = {
        "branch": "b1",
        "number": number,
        "mode": "deep",
        "focus": None,
        "model": "opus",
        "shape": "single",
        "plugin_version": "0.13.0",
        "diff_from": "aaa",
        "diff_to": "bbb",
        "changed_files": [f"f{i}.py" for i in range(6)],
        "findings": [],
        "started": "2026-11-01T00:00:00Z",
        "mode_reason": "no-state",
        "state_file": "/s/b1/review.json",
        "base_ref": "main",
        "intent": "do it",
    }
    base.update(kw)
    runs = [run_obj(2.0), run_obj(1.5)] if base["mode"] == "deep" else []
    return {**base, "runs": runs, "run": runs[0] if runs else None}


def finding(fid, rnd_no, severity="major", file="a.py", lines="5", scope="change"):
    return {
        "id": fid,
        "round_opened": rnd_no,
        "severity": severity,
        "file": file,
        "lines": lines,
        "scope": scope,
        "title": f"title {fid}",
    }


class PickTest(unittest.TestCase):
    def test_pick_filters_by_size_version_and_second_opinion(self):
        good = [rnd(1), rnd(2, focus="second-opinion", mode_reason="second-opinion")]
        small = [
            rnd(1, branch="small", changed_files=["a"], state_file="/s/small"),
            rnd(2, branch="small", focus="second-opinion", state_file="/s/small"),
        ]
        old = [
            rnd(1, branch="old", plugin_version="0.11.9", state_file="/s/old"),
            rnd(2, branch="old", focus="second-opinion", state_file="/s/old"),
        ]
        alone = [rnd(1, branch="alone", state_file="/s/alone")]
        unmatched = rnd(1, branch="nomatch", state_file="/s/nomatch")
        unmatched.update(runs=[], run=None)
        later = rnd(1, branch="later", mode_reason="continuing", state_file="/s/later")
        so = rnd(2, branch="later", focus="second-opinion", state_file="/s/later")
        joined = good + small + old + alone + [unmatched, later, so]
        states = {r["state_file"]: {"findings": []} for r in joined}
        picked = replay.pick_candidates(joined, states, repo_for=lambda repo, sha: repo)
        self.assertEqual([e["branch"] for e in picked], ["b1"])
        e = picked[0]
        self.assertEqual(e["round"], 1)
        self.assertEqual(e["second_opinion_round"], 2)
        self.assertAlmostEqual(e["old_cost"], 3.5)
        self.assertEqual(e["old_transcript"], "/t/agent-1.jsonl")
        self.assertEqual(e["repo"], "/r")
        self.assertEqual((e["diff_from"], e["diff_to"], e["base_ref"]), ("aaa", "bbb", "main"))
        json.dumps(picked)


class ResolveRepoTest(unittest.TestCase):
    def test_falls_back_to_main_checkout_of_deleted_worktree(self):
        main = "/p/Proj"
        wt = main + "/.claude/worktrees/feat-1"
        self.assertEqual(replay.resolve_repo(wt, "s", lambda r, s: r == main), main)
        self.assertEqual(replay.resolve_repo(wt, "s", lambda r, s: True), wt)
        self.assertIsNone(replay.resolve_repo(wt, "s", lambda r, s: False))
        self.assertIsNone(replay.resolve_repo("/p/other", "s", lambda r, s: r == main))
        self.assertIsNone(replay.resolve_repo(None, "s", lambda r, s: True))

    def test_pick_skips_and_reports_unresolvable_repo(self):
        joined = [rnd(1), rnd(2, focus="second-opinion")]
        skipped = []
        picked = replay.pick_candidates(
            joined, {"/s/b1/review.json": {}}, repo_for=lambda repo, sha: None, skipped=skipped
        )
        self.assertEqual(picked, [])
        self.assertEqual(len(skipped), 1)
        self.assertIn("b1", skipped[0])


class KnownFindingsTest(unittest.TestCase):
    def test_second_opinion_findings_outside_snapshot_are_excluded(self):
        state = {
            "findings": [
                finding("R1-F1", 1, file="gone.py", lines="40"),
                finding("R2-F1", 2, file="gone.py", lines="2"),
                finding("R2-F2", 2, file="short.py", lines="12-14"),
                finding("R2-F3", 2, file="short.py", lines="3", severity="minor"),
                finding("R2-F4", 2, file="short.py", lines="3", scope="outside"),
                finding("R3-F1", 3),
            ]
        }
        entry = {"round": 1, "second_opinion_round": 2}
        counts = {"short.py": 10}
        known, excluded = replay.known_findings(state, entry, counts.get)
        self.assertEqual([(k["id"], k["origin"]) for k in known], [("R1-F1", "round-1"), ("R2-F3", "second-opinion")])
        self.assertEqual([k["id"] for k in excluded], ["R2-F1", "R2-F2"])


class DispatchTest(unittest.TestCase):
    def fields(self):
        return {
            "MODE": "deep",
            "REPO": "/w",
            "BASE_REF": "aaa",
            "DIFF_FROM": "aaa",
            "DIFF_TO": "bbb",
            "REPORT_PATH": "/o/round.md",
            "CHECKLIST_PATH": "/p/skills/review/reviewer-checklist.md",
            "SCHEMA_PATH": "/p/skills/review/report-schema.md",
            "CHECK_COMMAND": '"/p/scripts/rs" check-report --state-dir /o/state --file /o/round.md',
            "CHANGED_FILES": ["a.py", "b.py"],
            "INTENT": "do it",
        }

    def test_dispatch_prompt_matches_skill_template(self):
        text = SKILL.read_text(encoding="utf-8")
        section = text.split("## 5. Dispatch", 1)[1].split("\n## 6.", 1)[0]
        template_starts = set(re.findall(r"^([A-Z_]+)\b", section, re.M))
        prompt = replay.dispatch_prompt(self.fields())
        labels = re.findall(r"^([A-Z_]+)[: (]", prompt, re.M)
        self.assertIn("OPEN_FINDINGS", labels)
        self.assertIn("CHANGED_FILES", labels)
        for label in labels:
            self.assertIn(label, template_starts)
        self.assertIn("SCHEMA_PATH: /p/skills/review/report-schema.md", prompt)
        self.assertTrue(replay.SCHEMA_NAME.endswith("report-schema.md"))
        self.assertIn("OPEN_FINDINGS:\n[]", prompt)
        self.assertIn("a.py\nb.py\nINTENT:", prompt)
        self.assertTrue(prompt.rstrip().endswith("not from your final message."))
        parsed = usage_report.parse_dispatch(prompt)
        self.assertEqual(parsed["changed_files"], ["a.py", "b.py"])
        self.assertEqual(parsed["diff_to"], "bbb")


def labels(*items):
    return [
        {"replay": i, "label": lab, "of": of, "holds": holds, "check": "x"} for i, (lab, of, holds) in enumerate(items)
    ]


def result(branch="b", old=5.0, new=2.0, status="ok", known=None, judge=None):
    return {
        "branch": branch,
        "status": status,
        "old_cost": old,
        "new_cost": new,
        "judge_cost": 0.5,
        "known": known
        if known is not None
        else [
            {"id": "R1-F1", "origin": "round-1", "severity": "major"},
            {"id": "R1-F2", "origin": "round-1", "severity": "minor"},
            {"id": "R2-F1", "origin": "second-opinion", "severity": "major"},
        ],
        "judge": judge if judge is not None else labels(("matches", "R1-F1", None), ("matches", "R1-F2", None)),
    }


class ScoreTest(unittest.TestCase):
    def test_score_passes_clean_set(self):
        s = replay.score([result("a"), result("b")])
        self.assertTrue(s["passed"])
        self.assertEqual(s["branches"][0]["majors_missed"], 0)
        self.assertEqual(s["branches"][0]["minors_found"], 1)
        self.assertAlmostEqual(s["spend"], 2 * 2.5)

    def test_score_fails_on_missed_round1_major(self):
        s = replay.score([result("a", judge=labels(("matches", "R1-F2", None)))])
        self.assertEqual(s["branches"][0]["majors_missed"], 1)
        self.assertFalse(s["passed"])

    def test_missing_second_opinion_major_is_not_a_miss(self):
        s = replay.score([result("a")])
        self.assertEqual(s["branches"][0]["majors_missed"], 0)

    def test_score_fails_on_not_real_finding(self):
        j = labels(("matches", "R1-F1", None), ("new", None, False), ("new", None, True), ("duplicate", "2", None))
        s = replay.score([result("a", judge=j)])
        b = s["branches"][0]
        self.assertEqual((b["new_held"], b["new_not_real"], b["duplicates"]), (1, 1, 1))
        self.assertFalse(s["passed"])

    def test_unverified_new_finding_is_listed_and_does_not_fail(self):
        j = labels(("matches", "R1-F1", None), ("new", None, None))
        r = result("a", judge=j)
        r["replay_findings"] = [{"title": "x"}, {"title": "unsure thing"}]
        s = replay.score([r])
        b = s["branches"][0]
        self.assertEqual((b["new_not_real"], b["new_unverified"]), (0, 1))
        self.assertEqual(b["unverified"][0]["title"], "unsure thing")
        self.assertTrue(s["passed"])

    def test_score_allows_one_costlier_branch(self):
        s = replay.score([result("a"), result("b", new=9.0), result("c")])
        self.assertTrue(s["passed"])

    def test_score_fails_on_two_costlier_branches(self):
        s = replay.score([result("a", new=9.0), result("b", new=9.0), result("c")])
        self.assertFalse(s["passed"])

    def test_score_fails_on_failed_branch(self):
        s = replay.score([result("a"), result("b", status="failed")])
        self.assertFalse(s["passed"])


def init_event(tools=("Read", "Grep", "Glob", "Bash"), plugin="/plugin", mcp=()):
    return {
        "type": "system",
        "subtype": "init",
        "tools": list(tools),
        "mcp_servers": list(mcp),
        "agents": ["branch-review:reviewer", "Explore"],
        "plugins": [
            {"name": "branch-review", "path": plugin},
            {"name": "superpowers", "path": "/cache/superpowers"},
            {"name": "cc-plugin-privacy", "path": "builtin"},
        ],
    }


def stream(out_tokens, text="done", model="claude-opus-5-5", init=None, reported=None):
    msg = {
        "type": "assistant",
        "message": {
            "id": "m1",
            "model": model,
            "content": [{"type": "text", "text": text}],
            "usage": {"input_tokens": 0, "output_tokens": out_tokens},
        },
    }
    final = {"type": "result", "result": text}
    if reported is not None:
        final["total_cost_usd"] = reported
    first = json.dumps(init_event() if init is None else init) + "\n"
    return first + json.dumps(msg) + "\n" + json.dumps(final) + "\n"


REPORT = (
    'report\n```json\n{"findings": [{"severity": "major", "file": "a.py", "lines": "5", "title": "t", '
    '"trigger": "x", "consequence": "y"}]}\n```\n'
)
JUDGE = 'ok\n```json\n[{"replay": 0, "label": "matches", "of": "R1-F1", "holds": null, "check": "same"}]\n```\n'


class ContextCheckTest(unittest.TestCase):
    def check(self, init):
        return replay.check_context(json.dumps(init) + "\n", Path("/plugin"))

    def test_check_context(self):
        self.assertIsNone(self.check(init_event()))
        self.assertIn("unexpected tools", self.check(init_event(tools=("Read", "mcp__srv__t"))))
        self.assertIn("not /plugin", self.check(init_event(plugin="/installed/x")))
        self.assertIn("MCP", self.check(init_event(mcp=("srv",))))
        self.assertIn("no init", replay.check_context("", Path("/plugin")))


class StreamCostTest(unittest.TestCase):
    def test_stream_costs_takes_the_larger(self):
        low = replay.stream_costs(stream(1_000_000, reported=1.0), "opus")
        self.assertAlmostEqual(low["spend"], 25.0)
        high = replay.stream_costs(stream(1_000_000, reported=30.0), "opus")
        self.assertEqual((high["spend"], high["reported"]), (30.0, 30.0))
        self.assertIsNone(replay.stream_costs(stream(10), "opus")["reported"])


class RealGitPathTest(unittest.TestCase):
    def test_worktree_and_begin_round_with_real_runner(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        repo = helpers.make_repo(tmp / "src")
        diff_from = helpers.git(repo, "rev-parse", "main")
        diff_to = helpers.git(repo, "rev-parse", "HEAD")
        helpers.git(repo, "checkout", "-q", "main")  # the branch commit is no longer HEAD
        plugin = Path(replay.__file__).resolve().parent.parent.parent
        runner = replay.Runner(plugin)
        intent = tmp / "intent.md"
        intent.write_text("add b", encoding="utf-8")
        tree = tmp / "out" / "repo"
        self.assertTrue(replay.has_commit(str(repo), diff_to))
        runner.worktree_add(str(repo), tree, diff_to)
        try:
            self.assertEqual(helpers.git(tree, "rev-parse", "HEAD"), diff_to)
            begun = runner.rs(
                [
                    "begin-round",
                    "--state-dir", str(tmp / "out" / "state"),
                    "--repo", str(tree),
                    "--mode", "deep",
                    "--mode-reason", "replay",
                    "--model", "opus",
                    "--snapshot", diff_to,
                    "--base-ref", diff_from,
                    "--intent-file", str(intent),
                ]
            )  # fmt: skip
            self.assertEqual((begun["diff_from"], begun["diff_to"]), (diff_from, diff_to))
            self.assertEqual(begun["changed_files"], ["b.txt"])
            self.assertEqual(runner.line_count(tree, diff_to, "b.txt"), 1)
            self.assertIsNone(runner.line_count(tree, diff_to, "gone.txt"))
        finally:
            runner.worktree_remove(str(repo), tree)
        self.assertFalse(tree.exists())


class FakeRunner:
    def __init__(self, diff_from="aaa", reports=(True, True), cost_tokens=100_000, reviewer_init=None, reported=None):
        self.reviewer_init = reviewer_init
        self.reported = reported
        self.diff_from = diff_from
        self.reports = list(reports)
        self.cost_tokens = cost_tokens
        self.calls = []
        self.budgets = []

    def worktree_add(self, repo, path, rev):
        Path(path).mkdir(parents=True)
        self.calls.append("add")

    def worktree_remove(self, repo, path):
        shutil.rmtree(path, ignore_errors=True)
        self.calls.append("remove")

    def rs(self, args):
        self.calls.append("rs")
        self.rs_args = args
        return {"diff_from": self.diff_from, "diff_to": "bbb"}

    def line_count(self, cwd, rev, file):
        return 100

    def reviewer(self, prompt, cwd, budget):
        self.calls.append("reviewer")
        self.budgets.append(budget)
        self.prompt = prompt
        if self.reports.pop(0):
            path = re.search(r"^REPORT_PATH: (.*)$", prompt, re.M).group(1)
            Path(path).write_text(REPORT, encoding="utf-8")
        return stream(self.cost_tokens, init=self.reviewer_init, reported=self.reported)

    def judge(self, prompt, cwd, budget):
        self.calls.append("judge")
        self.judge_prompt = prompt
        return stream(1000, JUDGE)


class RunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        state = {"findings": [finding("R1-F1", 1), finding("R2-F1", 2)]}
        (self.tmp / "review.json").write_text(json.dumps(state), encoding="utf-8")
        self.entries = [
            {
                "repo": "/r",
                "branch": f"b{i}",
                "base_ref": "main",
                "diff_from": "aaa",
                "diff_to": "bbb",
                "intent": "do it",
                "state_file": str(self.tmp / "review.json"),
                "round": 1,
                "old_cost": 3.0,
                "old_transcript": "/t",
                "second_opinion_round": 2,
                "changed_files": ["a.py"],
            }
            for i in (1, 2)
        ]
        self.plugin = Path("/plugin")
        self.out = self.tmp / "out"

    def go(self, runner, **kw):
        return replay.run_replay(
            self.entries,
            plugin=self.plugin,
            out=self.out,
            round_budget=kw.get("round_budget", 15.0),
            max_total=kw.get("max_total", 40.0),
            runner=runner,
        )

    def test_run_aborts_on_diff_mismatch(self):
        runner = FakeRunner(diff_from="other")
        results = self.go(runner)
        self.assertEqual([r["status"] for r in results], ["failed", "failed"])
        self.assertEqual(results[0]["reason"], "diff mismatch")
        self.assertNotIn("reviewer", runner.calls)
        self.assertEqual(runner.calls.count("remove"), 2)

    def test_run_happy_path_saves_result_and_scores(self):
        runner = FakeRunner()
        results = self.go(runner)
        self.assertEqual([r["status"] for r in results], ["ok", "ok"])
        saved = json.loads((self.out / "1" / "result.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["judge"][0]["of"], "R1-F1")
        self.assertGreater(saved["new_cost"], 0)
        self.assertIn("--snapshot", runner.rs_args)
        self.assertIn(str(self.out / "2" / "state"), runner.rs_args)
        self.assertIn("--state-dir " + str(self.out / "2" / "state"), runner.prompt)
        self.assertIn("R1-F1", runner.judge_prompt)
        again = replay.score(replay.load_results(self.out))
        self.assertEqual(len(again["branches"]), 2)

    def test_run_marks_budget_hit_as_failed_and_continues(self):
        runner = FakeRunner(reports=(False, True))
        results = self.go(runner)
        self.assertEqual([r["status"] for r in results], ["failed", "ok"])
        self.assertGreater(results[0]["new_cost"], 0)
        self.assertEqual(runner.calls.count("reviewer"), 2)

    def test_run_stops_at_total_cap(self):
        # 1M output tokens on Opus is $25, past a $20 cap.
        runner = FakeRunner(cost_tokens=1_000_000)
        results = self.go(runner, max_total=20.0)
        self.assertEqual([r["status"] for r in results], ["ok", "skipped"])
        self.assertEqual(runner.calls.count("reviewer"), 1)

    def test_run_fails_on_wrong_context_and_records_spend(self):
        for init in (init_event(tools=("Read", "mcp__x__tool")), init_event(plugin="/installed/branch-review")):
            runner = FakeRunner(reviewer_init=init)
            results = self.go(runner)
            self.assertEqual(results[0]["status"], "failed")
            self.assertTrue(results[0]["reason"].startswith("wrong context"))
            self.assertGreater(results[0]["new_cost"], 0)
            self.assertNotIn("judge", runner.calls)

    def test_spend_uses_reported_cost_when_higher(self):
        runner = FakeRunner(reported=7.0)
        results = self.go(runner)
        self.assertEqual(results[0]["new_cost"], 7.0)
        self.assertAlmostEqual(results[0]["new_cost_estimate"], 2.5)
        self.assertEqual(results[0]["new_cost_reported"], 7.0)
        self.assertIn("judge_cost_estimate", results[0])

    def test_reviewer_budget_never_exceeds_remaining_cap(self):
        runner = FakeRunner()
        self.go(runner, max_total=10.0, round_budget=15.0)
        self.assertLessEqual(runner.budgets[0], 8.0)


if __name__ == "__main__":
    unittest.main()
