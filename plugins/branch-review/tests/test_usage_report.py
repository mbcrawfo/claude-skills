"""Tests for the usage report (evals/usage/usage_report.py)."""

from datetime import date
import json
from pathlib import Path
import tempfile
import unittest

import helpers  # noqa: F401  (puts the evals dirs on sys.path)
import usage_report

PROMPT = """Review this change set as your agent definition describes. Inputs:

MODE: deep
FOCUS: second-opinion
REPO: /r
BASE_REF: main
DIFF_FROM: aaa
DIFF_TO: bbb
CHECKLIST_PATH: /x/branch-review/0.14.0/skills/review/reviewer-checklist.md
CHANGED_FILES (the review's scope):
a.py
b/c.py
d.md
INTENT:
do things
"""


def assistant(mid, out, ts, model="claude-opus-5-5", tools=0, **usage):
    return {
        "type": "assistant",
        "timestamp": ts,
        "message": {
            "id": mid,
            "model": model,
            "content": [{"type": "tool_use", "id": f"{mid}-t{i}"} for i in range(tools)],
            "usage": {"input_tokens": 100, "output_tokens": out, **usage},
        },
    }


def write_agent(session: Path, name, agent_type, lines):
    sub = session / "subagents"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"agent-{name}.meta.json").write_text(json.dumps({"agentType": agent_type}))
    (sub / f"agent-{name}.jsonl").write_text("\n".join(json.dumps(x) for x in lines))


def round_(number, mode, model, diff_from, diff_to, started, **extra):
    return {
        "number": number,
        "mode": mode,
        "focus": extra.pop("focus", None),
        "model": model,
        "mode_reason": "r",
        "plugin_version": "0.14.0",
        "diff_from": diff_from,
        "diff_to": diff_to,
        "changed_files": ["a.py"],
        "started": started,
        **extra,
    }


def finding(fid, rnd, severity, status="open", scope="change"):
    return {
        "id": fid,
        "round_opened": rnd,
        "scope": scope,
        "severity": severity,
        "status": status,
    }


def write_state(root: Path, repo, branch, rounds, findings):
    d = root / repo / branch
    d.mkdir(parents=True)
    (d / "review.json").write_text(
        json.dumps(
            {
                "branch": branch,
                "base_ref": "main",
                "intent": "i",
                "rounds": rounds,
                "findings": findings,
            }
        )
    )


class UsageReportTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        session = self.root / "projects" / "proj" / "sess"
        ts = "2026-11-01T10:00:00.000Z"
        write_agent(
            session,
            "one",
            "branch-review:reviewer",
            [
                {"type": "user", "timestamp": ts, "message": {"content": PROMPT}},
                assistant("m1", 1000, ts, tools=2),
                assistant("m2", 2000, ts, tools=1),
            ],
        )
        write_agent(
            session,
            "two",
            "branch-review:reviewer",
            [
                {"type": "user", "timestamp": ts, "message": {"content": "MODE: deep"}},
                assistant("m1", 500, ts),
            ],
        )
        write_agent(session, "other", "general-purpose", [assistant("m1", 5, ts)])
        self.state = self.root / "state"
        write_state(
            self.state,
            "o__r",
            "feat",
            [
                round_(1, "deep", "opus", "a0", "a1", ts),
                round_(2, "deep", "opus", "aaa", "bbb", ts, focus="second-opinion", shape="split"),
                round_(3, "recorded", None, "bbb", "ccc", ts),
            ],
            [
                finding("R1-F1", 1, "major"),
                finding("R1-F2", 1, "minor", "invalid"),
                finding("R1-F3", 1, "major", "duplicate"),
                finding("R2-F1", 2, "major"),
                finding("R2-F2", 2, "critical", "open", "outside"),
                finding("R2-F3", 2, "critical"),
                finding("R2-F4", 2, "major", "invalid"),
                finding("R2-F5", 2, "critical", "duplicate"),
                finding("R2-F6", 2, "major", "invalid"),
            ],
        )
        write_state(
            self.state,
            "o__r",
            "fake",
            [round_(1, "deep", "test-model", "x", "y", ts)],
            [],
        )
        self.since, self.until = date(2026, 10, 25), date(2026, 11, 8)

    def runs(self):
        return usage_report.reviewer_runs(self.root / "projects", self.since, self.until)

    def rounds(self):
        return usage_report.state_rounds([self.state], self.since, self.until)

    def test_repeated_message_lines_count_once(self):
        lines = [json.dumps(assistant("m", n, "t")) for n in (10, 20, 30)]
        usages = usage_report.message_usages(lines)
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0][1]["output_tokens"], 30)

    def test_cost_uses_cache_breakdown(self):
        usage = {
            "input_tokens": 1_000_000,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 1_000_000,
                "ephemeral_1h_input_tokens": 1_000_000,
            },
            "cache_read_input_tokens": 1_000_000,
            "output_tokens": 1_000_000,
        }
        self.assertAlmostEqual(usage_report.cost_usd(usage, "opus"), 46.75)

    def test_cost_falls_back_to_total_cache_creation(self):
        usage = {"cache_creation_input_tokens": 1_000_000}
        self.assertAlmostEqual(usage_report.cost_usd(usage, "sonnet"), 3.75)

    def test_model_family(self):
        self.assertEqual(usage_report.model_family("claude-opus-5-5"), "opus")
        self.assertEqual(usage_report.model_family("sonnet"), "sonnet")
        self.assertIsNone(usage_report.model_family("test-model"))
        self.assertIsNone(usage_report.model_family(None))

    def test_parse_dispatch_reads_fields(self):
        d = usage_report.parse_dispatch(PROMPT.replace("FOCUS: second-opinion\n", ""))
        self.assertEqual(d["mode"], "deep")
        self.assertIsNone(d["focus"])
        self.assertEqual(d["changed_files"], ["a.py", "b/c.py", "d.md"])
        self.assertEqual(d["plugin_version"], "0.14.0")
        self.assertEqual((d["diff_from"], d["diff_to"]), ("aaa", "bbb"))

    def test_only_reviewer_subagents_are_read(self):
        runs = self.runs()
        self.assertEqual(len(runs), 2)
        run = next(r for r in runs if r.dispatch.get("diff_from") == "aaa")
        self.assertEqual(run.turns, 2)
        self.assertEqual(run.tool_calls, 3)
        self.assertEqual(run.model, "opus")

    def test_runs_outside_date_range_are_dropped(self):
        runs = usage_report.reviewer_runs(self.root / "projects", date(2026, 1, 1), date(2026, 1, 2))
        self.assertEqual(runs, [])

    def test_test_model_state_is_skipped(self):
        rounds = self.rounds()
        self.assertEqual({r["branch"] for r in rounds}, {"feat"})
        self.assertEqual(len(rounds), 2)
        second = rounds[1]
        self.assertEqual(second["shape"], "split")
        self.assertEqual(rounds[0]["shape"], "single")
        self.assertEqual([f["id"] for f in second["findings"]], ["R2-F1", "R2-F3", "R2-F4", "R2-F5", "R2-F6"])
        for key in ("mode_reason", "state_file", "base_ref", "intent"):
            self.assertIn(key, second)
        self.assertEqual(second["base_ref"], "main")

    def test_join_matches_on_diff_range(self):
        joined = usage_report.join(self.runs(), self.rounds())
        self.assertIsNone(joined[0]["run"])
        self.assertEqual(joined[1]["run"].dispatch["diff_from"], "aaa")

    def _run(self, name, mode, focus, out):
        prompt = f"MODE: {mode}\n" + (f"FOCUS: {focus}\n" if focus else "")
        prompt += "DIFF_FROM: same\nDIFF_TO: range\n"
        ts = "2026-11-02T10:00:00.000Z"
        write_agent(
            self.root / "projects" / "proj" / "sess2",
            name,
            "branch-review:reviewer",
            [
                {"type": "user", "timestamp": ts, "message": {"content": prompt}},
                assistant("m", out, ts),
            ],
        )

    def _round_pair(self):
        ts = "2026-11-02T10:00:00.000Z"
        return [
            {
                **round_(n, "deep", "opus", "same", "range", ts, focus=f),
                "shape": "single",
                "branch": "b",
                "findings": [],
            }
            for n, f in ((1, None), (2, "second-opinion"))
        ]

    def test_same_range_rounds_get_their_own_run(self):
        self._run("deep", "deep", None, 1000)
        self._run("second", "deep", "second-opinion", 2000)
        runs = [r for r in self.runs() if r.dispatch["diff_from"] == "same"]
        joined = usage_report.join(runs, self._round_pair())
        self.assertEqual([len(j["runs"]) for j in joined], [1, 1])
        self.assertNotEqual(joined[0]["runs"][0].path, joined[1]["runs"][0].path)

    def test_round_sums_all_its_runs(self):
        self._run("a", "deep", None, 1000)
        self._run("b", "deep", None, 1000)
        runs = [r for r in self.runs() if r.dispatch["diff_from"] == "same"]
        joined = usage_report.join(runs, self._round_pair())
        self.assertEqual(len(joined[0]["runs"]), 2)
        self.assertEqual(joined[1]["runs"], [])
        groups = usage_report.summarize(joined)["groups"]
        g = groups["deep/-/opus/single/0.14.0"]
        self.assertAlmostEqual(g["cost_total"], sum(r.cost for r in runs))
        self.assertAlmostEqual(g["cost_median"], g["cost_total"])
        self.assertEqual(g["turns_median"], 2)

    def test_summarize_groups_and_counts(self):
        out = usage_report.summarize(usage_report.join(self.runs(), self.rounds()))
        self.assertEqual(
            set(out["groups"]),
            {
                "deep/-/opus/single/0.14.0",
                "deep/second-opinion/opus/split/0.14.0",
            },
        )
        g = out["groups"]["deep/-/opus/single/0.14.0"]
        # invalid and duplicate findings are counted only in their own columns.
        self.assertEqual(
            (g["rounds"], g["major"], g["minor"], g["critical"], g["invalid"], g["duplicate"]), (1, 1, 0, 0, 1, 1)
        )
        g2 = out["groups"]["deep/second-opinion/opus/split/0.14.0"]
        self.assertEqual((g2["major"], g2["critical"], g2["invalid"], g2["duplicate"]), (1, 1, 2, 1))

    def test_second_opinion_majors_count_critical_but_not_invalid_or_duplicate(self):
        out = usage_report.summarize(usage_report.join(self.runs(), self.rounds()))
        self.assertEqual(out["second_opinions"], [{"branch": "feat", "majors": 2, "prior_shape": "single"}])

    def test_round_intent_and_base_ref_win_over_the_states(self):
        ts = "2026-11-01T10:00:00.000Z"
        write_state(
            self.state,
            "o__r",
            "own",
            [
                round_(1, "deep", "opus", "a0", "a1", ts, intent="round one intent", base_ref="dev"),
                round_(2, "deep", "opus", "a1", "a2", ts),
            ],
            [],
        )
        rounds = [r for r in self.rounds() if r["branch"] == "own"]
        self.assertEqual((rounds[0]["intent"], rounds[0]["base_ref"]), ("round one intent", "dev"))
        self.assertEqual((rounds[1]["intent"], rounds[1]["base_ref"]), ("i", "main"))

    def test_recorded_rounds_do_not_hide_the_rest_of_the_state_file(self):
        self.assertEqual([r["number"] for r in self.rounds()], [1, 2])

    def test_peak_context_includes_cache_reads_and_creation(self):
        write_agent(
            self.root / "projects" / "proj" / "sess3",
            "peak",
            "branch-review:reviewer",
            [
                {
                    "type": "user",
                    "timestamp": "2026-11-03T10:00:00Z",
                    "message": {"content": "MODE: deep\nDIFF_FROM: p\nDIFF_TO: q\n"},
                },
                assistant(
                    "m", 5, "2026-11-03T10:00:00Z", cache_creation_input_tokens=200, cache_read_input_tokens=5000
                ),
            ],
        )
        run = next(r for r in self.runs() if r.dispatch.get("diff_from") == "p")
        self.assertEqual(run.peak_context, 5300)

    def test_p90_is_the_ninetieth_percentile_not_the_maximum(self):
        self.assertEqual(usage_report._p90([float(n) for n in range(1, 11)]), 9.0)
        self.assertEqual(usage_report._p90([]), 0.0)
        joined = []
        for n in range(1, 11):
            run = usage_report.ReviewerRun(Path("/t"), "t", "opus", float(n), 1, 1, 0, {})
            joined.append(
                {"mode": "deep", "focus": None, "model": "opus", "shape": "single", "plugin_version": "v",
                 "branch": f"b{n}", "number": 1, "findings": [], "runs": [run], "run": run}
            )  # fmt: skip
        g = usage_report.summarize(joined)["groups"]["deep/-/opus/single/v"]
        self.assertEqual((g["cost_p90"], g["cost_median"]), (9.0, 5.5))

    def test_model_without_a_family_is_unpriced_not_opus(self):
        ts = "2026-11-04T10:00:00Z"
        prompt = "MODE: deep\nDIFF_FROM: u\nDIFF_TO: v\n"
        write_agent(
            self.root / "projects" / "proj" / "sess4",
            "mystery",
            "branch-review:reviewer",
            [
                {"type": "user", "timestamp": ts, "message": {"content": prompt}},
                assistant("m", 1_000_000, ts, model="mystery-model-9"),
            ],
        )
        runs = self.runs()
        run = next(r for r in runs if r.dispatch.get("diff_from") == "u")
        self.assertEqual(run.cost, 0.0)
        self.assertEqual(run.unpriced_model, "mystery-model-9")
        priced = next(r for r in runs if r.dispatch.get("diff_from") == "aaa")
        self.assertIsNone(priced.unpriced_model)
        summary = usage_report.summarize(usage_report.join(runs, self.rounds()), runs)
        self.assertEqual(summary["unpriced"], 1)
        self.assertEqual(summary["unpriced_models"], ["mystery-model-9"])
        text = usage_report._format(summary)
        self.assertRegex(text, r"warning: .*mystery-model-9")
        clean = usage_report.summarize(usage_report.join([priced], self.rounds()), [priced])
        self.assertEqual(clean["unpriced"], 0)
        self.assertNotIn("warning", usage_report._format(clean))


if __name__ == "__main__":
    unittest.main()
