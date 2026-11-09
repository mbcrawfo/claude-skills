"""Tests for the small replay (evals/replay/replay.py). No test calls `claude`."""

import contextlib
from datetime import date
import io
import json
from pathlib import Path
import re
import shlex
import shutil
import tempfile
import unittest
from unittest import mock

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


class PickIntentTest(unittest.TestCase):
    def test_picked_entry_carries_round_one_intent_from_the_state(self):
        ts = "2026-11-01T10:00:00.000Z"
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        rounds = [
            {"number": 1, "mode": "deep", "focus": None, "model": "opus", "mode_reason": "no-state",
             "plugin_version": "0.13.0", "diff_from": "aaa", "diff_to": "bbb", "started": ts,
             "changed_files": [f"f{i}.py" for i in range(6)], "intent": "round one intent"},
            {"number": 2, "mode": "deep", "focus": "second-opinion", "model": "opus", "mode_reason": "second-opinion",
             "plugin_version": "0.13.0", "diff_from": "aaa", "diff_to": "ccc", "started": ts,
             "changed_files": ["f0.py"], "intent": "final intent naming the defects"},
        ]  # fmt: skip
        d = tmp / "o__r" / "b1"
        d.mkdir(parents=True)
        (d / "review.json").write_text(
            json.dumps({"branch": "b1", "base_ref": "main", "intent": "final intent naming the defects",
                        "rounds": rounds, "findings": []}),
            encoding="utf-8",
        )  # fmt: skip
        state_rounds = usage_report.state_rounds([tmp], date(2026, 10, 1), date(2026, 12, 1))
        run = run_obj(2.0, {"repo": "/r", "diff_from": "aaa", "diff_to": "bbb", "mode": "deep", "focus": None})
        by_key = {(r["diff_from"], r["diff_to"]): [run] for r in state_rounds[:1]}
        joined = [{**r, "runs": by_key.get((r["diff_from"], r["diff_to"]), []), "run": None} for r in state_rounds]
        states = {r["state_file"]: {"findings": []} for r in joined}
        picked = replay.pick_candidates(joined, states, repo_for=lambda repo, sha: repo)
        self.assertEqual(len(picked), 1)
        self.assertEqual(picked[0]["intent"], "round one intent")


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

    def test_finding_on_the_last_line_is_kept(self):
        state = {
            "findings": [
                finding("R2-F1", 2, file="short.py", lines="10"),
                finding("R2-F2", 2, file="short.py", lines="11"),
            ]
        }
        known, excluded = replay.known_findings(state, {"round": 1, "second_opinion_round": 2}, {"short.py": 10}.get)
        self.assertEqual([k["id"] for k in known], ["R2-F1"])
        self.assertEqual([k["id"] for k in excluded], ["R2-F2"])

    def test_invalid_and_duplicate_findings_are_excluded_not_known(self):
        state = {"findings": []}
        for status in ("invalid", "duplicate"):
            for rnd_no in (1, 2):
                f = finding(f"R{rnd_no}-{status}", rnd_no)
                f["status"] = status
                state["findings"].append(f)
        state["findings"].append({**finding("R1-open", 1), "status": "open"})
        known, excluded = replay.known_findings(state, {"round": 1, "second_opinion_round": 2}, lambda f: 100)
        self.assertEqual([k["id"] for k in known], ["R1-open"])
        self.assertEqual(
            sorted(k["id"] for k in excluded), ["R1-duplicate", "R1-invalid", "R2-duplicate", "R2-invalid"]
        )


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

    def test_score_fails_on_missed_round1_critical(self):
        known = [{"id": "R1-F1", "origin": "round-1", "severity": "critical"}]
        s = replay.score([result("a", known=known, judge=[])])
        self.assertEqual(s["branches"][0]["majors_missed"], 1)
        self.assertFalse(s["passed"])

    def test_score_counts_equal_cost_as_costlier(self):
        s = replay.score([result("a", old=5.0, new=5.0), result("b", old=5.0, new=5.0), result("c")])
        self.assertFalse(s["passed"])
        s = replay.score([result("a", old=5.0, new=5.0), result("b")])
        self.assertTrue(s["passed"])

    def test_score_fails_on_failed_branch(self):
        s = replay.score([result("a"), result("b", status="failed")])
        self.assertFalse(s["passed"])


def init_event(
    tools=("Read", "Grep", "Glob", "Bash"), plugin="/plugin", mcp=(), agents=("branch-review:reviewer", "Explore")
):
    return {
        "type": "system",
        "subtype": "init",
        "tools": list(tools),
        "mcp_servers": list(mcp),
        "agents": list(agents),
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
        self.assertIn("reviewer agent not loaded", self.check(init_event(agents=("Explore",))))
        self.assertIn("no init", replay.check_context("", Path("/plugin")))


class StreamCostTest(unittest.TestCase):
    def test_stream_costs_takes_the_larger(self):
        low = replay.stream_costs(stream(1_000_000, reported=1.0), "opus")
        self.assertAlmostEqual(low["spend"], 25.0)
        high = replay.stream_costs(stream(1_000_000, reported=30.0), "opus")
        self.assertEqual((high["spend"], high["reported"]), (30.0, 30.0))
        self.assertIsNone(replay.stream_costs(stream(10), "opus")["reported"])


class JsonBlockTest(unittest.TestCase):
    FENCE = "`" * 3

    def block(self, body):
        return f"text\n{self.FENCE}json\n{body}\n{self.FENCE}\n"

    def test_last_json_block_reads_backticks_inside_a_string(self):
        evidence = "quote:\n" + self.FENCE + "py\nx = 1\n" + self.FENCE
        report = self.block(json.dumps({"findings": [{"title": "t", "evidence": evidence}]}))
        self.assertEqual(replay.last_json_block(report)["findings"][0]["evidence"], evidence)
        judge = self.block(json.dumps([{"replay": 0, "label": "new", "holds": True, "check": evidence}]))
        self.assertEqual(replay.last_json_block(judge)[0]["check"], evidence)

    def test_last_json_block_plain_and_missing(self):
        self.assertEqual(replay.last_json_block(self.block('{"findings": []}')), {"findings": []})
        self.assertIsNone(replay.last_json_block("no block here"))
        self.assertIsNone(replay.last_json_block(self.block("{not json")))


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
    def __init__(
        self,
        diff_from="aaa",
        reports=(True, True),
        cost_tokens=100_000,
        reviewer_init=None,
        reported=None,
        judge_text=JUDGE,
        fail_at=None,
        remove_fails=False,
    ):
        self.judge_text = judge_text
        self.fail_at = fail_at
        self.remove_fails = remove_fails
        self.cwds = []
        self.trees = []
        self.reviewer_init = reviewer_init
        self.reported = reported
        self.diff_from = diff_from
        self.reports = list(reports)
        self.cost_tokens = cost_tokens
        self.calls = []
        self.budgets = []

    def worktree_add(self, repo, path, rev):
        Path(path).mkdir(parents=True)
        self.trees.append(Path(path))
        self.calls.append("add")

    def worktree_remove(self, repo, path):
        shutil.rmtree(path, ignore_errors=True)
        self.calls.append("remove")
        if self.remove_fails:
            raise RuntimeError("git worktree remove failed")

    def rs(self, args):
        self.calls.append("rs")
        self.rs_args = args
        return {"diff_from": self.diff_from, "diff_to": "bbb"}

    def line_count(self, cwd, rev, file):
        if self.fail_at == "line_count":
            raise RuntimeError("line_count boom")
        return 100

    def reviewer(self, prompt, cwd, budget):
        self.cwds.append(("reviewer", Path(cwd)))
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
        self.cwds.append(("judge", Path(cwd)))
        return stream(1000, self.judge_text)


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
        self.assertIn("--state-dir '" + str(self.out / "2" / "state") + "'", runner.prompt)
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

    def test_worktree_is_outside_any_checkout_and_removed(self):
        runner = FakeRunner()
        self.go(runner)
        self.assertEqual(len(runner.trees), 2)
        for tree in runner.trees:
            for forbidden in (replay.PLUGIN_ROOT, replay.RESULTS_DIR, self.out, self.tmp):
                self.assertNotIn(forbidden.resolve(), tree.resolve().parents)
            self.assertFalse(tree.parent.exists())
        self.assertEqual({c for _, c in runner.cwds}, set(runner.trees))
        self.assertEqual({n for n, _ in runner.cwds}, {"reviewer", "judge"})
        self.assertFalse((self.out / "1" / "repo").exists())

    def test_check_command_quotes_a_state_dir_with_a_space(self):
        self.out = self.tmp / "my out"
        runner = FakeRunner()
        self.go(runner)
        line = re.search(r"^\"?/plugin.*check-report.*$", runner.prompt, re.M).group(0)
        parts = shlex.split(line)
        self.assertEqual(parts[parts.index("--state-dir") + 1], str(self.out / "2" / "state"))

    def test_run_fails_when_judge_gives_no_json(self):
        runner = FakeRunner(judge_text="no json at all")
        results = self.go(runner)
        self.assertEqual((results[0]["status"], results[0]["reason"]), ("failed", "no judge output"))
        self.assertEqual(results[0]["judge"], [])

    def test_runner_step_that_raises_fails_entry_and_removes_worktree(self):
        runner = FakeRunner(fail_at="line_count")
        results = self.go(runner)
        self.assertEqual([r["status"] for r in results], ["failed", "failed"])
        self.assertIn("line_count boom", results[0]["reason"])
        self.assertEqual(runner.calls.count("remove"), 2)

    def test_failed_worktree_removal_keeps_the_first_error_first(self):
        runner = FakeRunner(fail_at="line_count", remove_fails=True)
        results = self.go(runner)
        reason = results[0]["reason"]
        self.assertTrue(reason.startswith("line_count boom"), reason)
        self.assertIn("git worktree remove failed", reason)
        ok = self.go(FakeRunner(remove_fails=True))
        self.assertEqual(ok[0]["status"], "ok")
        self.assertIn("git worktree remove failed", ok[0]["reason"])

    def test_cost_is_kept_when_writing_the_stream_fails(self):
        real = Path.write_text

        def flaky(path, *a, **kw):
            if path.name == "stream.jsonl":
                raise OSError("disk full")
            return real(path, *a, **kw)

        with mock.patch.object(Path, "write_text", flaky):
            results = self.go(FakeRunner())
        self.assertEqual(results[0]["status"], "failed")
        self.assertIn("disk full", results[0]["reason"])
        self.assertGreater(results[0]["new_cost"], 0)

    def test_run_skips_an_entry_when_less_than_a_dollar_is_left(self):
        runner = FakeRunner()
        results = self.go(runner, max_total=2.5)
        self.assertEqual([(r["status"], r["reason"]) for r in results], [("skipped", "budget")] * 2)
        self.assertEqual(runner.calls, [])

    def test_judge_cost_counts_toward_the_running_total(self):
        runner = FakeRunner()
        judge_json = '[{"replay": 0, "label": "matches", "of": "R1-F1", "holds": null, "check": "x"}]'
        # judge reported cost $1.50; reviewer ~$2.50: spend 4.0, left 6.5 - 4.0 - 2 = 0.5 < 1.
        runner.judge = lambda prompt, cwd, budget: (
            runner.calls.append("judge") or stream(1000, "ok\n```json\n" + judge_json + "\n```\n", reported=1.5)
        )
        results = self.go(runner, max_total=6.5)
        self.assertEqual([(r["status"], r["reason"]) for r in results], [("ok", ""), ("skipped", "budget")])

    def test_run_starts_an_entry_with_exactly_a_dollar_left(self):
        runner = FakeRunner()
        results = self.go(runner, max_total=3.0)
        self.assertEqual(results[0]["status"], "ok")
        self.assertAlmostEqual(runner.budgets[0], 1.0)

    def test_reviewer_budget_never_exceeds_remaining_cap(self):
        runner = FakeRunner()
        self.go(runner, max_total=10.0, round_budget=15.0)
        self.assertLessEqual(runner.budgets[0], 8.0)


class ExitCodeTest(unittest.TestCase):
    def main_exit(self, results):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        (tmp / "set.json").write_text("[]", encoding="utf-8")
        argv = ["run", "--set", str(tmp / "set.json"), "--plugin", str(tmp), "--out", str(tmp / "out")]
        with mock.patch.object(replay, "run_replay", return_value=results) as fake:
            with contextlib.redirect_stdout(io.StringIO()):
                code = replay.main(argv)
        fake.assert_called_once()
        return code

    def test_run_exits_1_when_the_set_fails_and_0_when_it_passes(self):
        self.assertEqual(self.main_exit([result("a"), result("b")]), 0)
        self.assertEqual(self.main_exit([result("a", status="failed")]), 1)


class ClaudeCommandTest(unittest.TestCase):
    def command(self, method):
        runner = replay.Runner(Path("/plugin"))
        with mock.patch.object(replay.subprocess, "run") as fake:
            fake.return_value.stdout = ""
            getattr(runner, method)("prompt", Path("/w"), 1.0)
        return fake.call_args.args[0]

    def test_reviewer_and_judge_both_use_strict_mcp_config(self):
        for method in ("reviewer", "judge"):
            self.assertIn("--strict-mcp-config", self.command(method), method)


if __name__ == "__main__":
    unittest.main()
