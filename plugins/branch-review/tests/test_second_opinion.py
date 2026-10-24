import json
from pathlib import Path
import unittest

from helpers import LedgerHarness, finding, output
import review_state

MODERATE = {"level": "moderate", "reason": "touches shared state"}
VERIFY_R1_F1 = [{"id": "R1-F1", "status": "verified", "reason": "ok"}]


class SecondOpinionTest(LedgerHarness, unittest.TestCase):
    def deep(self, *extra, **over):
        self.begin("deep", *extra)
        self.record(output(findings=[], **over))

    def status_of(self):
        return self.cli("status")

    def assert_second_opinion(self, result, reasons):
        self.assertTrue(result["continue"], result)
        self.assertEqual(result["reason"], "second-opinion")
        self.assertEqual(result["next_mode"], "deep")
        self.assertEqual(result["next_focus"], "second-opinion")
        self.assertEqual(result["second_opinion_reasons"], reasons)

    def test_low_risk_minor_only_is_not_due(self):
        self.begin()
        self.record(output(findings=[finding(severity="minor")]))
        self.set_status("R1-F1", "fixed", "--commit", "c1")
        self.begin("followup")
        self.record(output(findings=[], verdicts=VERIFY_R1_F1))
        result = self.status_of()
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")
        self.assertEqual(result["second_opinion_reasons"], [])
        self.assertIsNone(result["next_mode"])
        self.assertIsNone(result["next_focus"])

    def test_moderate_risk_is_due_at_convergence(self):
        self.deep(merge_risk=MODERATE)
        self.assert_second_opinion(self.status_of(), ["merge-risk"])

    def test_high_risk_is_due_at_convergence(self):
        self.deep(merge_risk={"level": "high", "reason": "migration"})
        self.assert_second_opinion(self.status_of(), ["merge-risk"])

    def test_open_findings_come_before_second_opinion(self):
        self.begin()
        self.record(output(findings=[finding()], merge_risk=MODERATE))
        result = self.status_of()
        self.assertEqual(result["reason"], "open-findings")
        self.assertEqual(result["second_opinion_reasons"], [])
        self.assertIsNone(result["next_mode"])

    def test_major_finding_is_due_even_when_escalated(self):
        self.begin()
        self.record(output(findings=[finding(severity="major")]))
        self.set_status("R1-F1", "escalated", "--note", "needs a decision")
        self.assert_second_opinion(self.status_of(), ["major-finding"])

    def test_critical_finding_is_due_whatever_its_status(self):
        self.begin()
        self.record(output(findings=[finding(severity="critical")]))
        self.set_status("R1-F1", "invalid", "--note", "guarded upstream")
        self.assert_second_opinion(self.status_of(), ["major-finding"])

    def test_both_reasons_are_listed_in_order(self):
        self.begin()
        self.record(output(findings=[finding(severity="major")], merge_risk=MODERATE))
        self.set_status("R1-F1", "escalated", "--note", "q")
        self.assert_second_opinion(self.status_of(), ["merge-risk", "major-finding"])

    def test_second_opinion_round_clears_due(self):
        self.begin()
        self.record(output(findings=[finding(severity="major")]))
        self.set_status("R1-F1", "escalated", "--note", "needs a decision")
        self.assertEqual(self.status_of()["reason"], "second-opinion")
        self.deep("--focus", "second-opinion")
        result = self.status_of()
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")
        self.assertEqual(result["second_opinion_reasons"], [])
        reasons = self.cli("covered")["reasons"]
        self.assertIn("escalated", reasons)
        self.assertNotIn("second-opinion-pending", reasons)

    def test_only_one_automatic_second_opinion(self):
        self.deep(merge_risk=MODERATE)
        self.deep("--focus", "second-opinion")
        self.begin("followup")
        self.record(output(findings=[finding(severity="major")]))
        self.set_status("R3-F1", "fixed", "--commit", "c1")
        self.begin("followup")
        self.record(output(findings=[], verdicts=[{"id": "R3-F1", "status": "verified", "reason": "ok"}]))
        result = self.status_of()
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")

    def test_requested_overrides_skip_and_repeats(self):
        self.deep("--no-second-opinion", merge_risk=MODERATE)
        self.assertEqual(self.status_of()["reason"], "converged")
        self.assertEqual(self.state()["second_opinion"], {"requested": None, "skipped": True})

        self.begin("followup", "--second-opinion")
        self.record(output(findings=[]))
        self.assertEqual(self.state()["second_opinion"], {"requested": 2, "skipped": False})
        # Asking clears the skip, so the moderate risk counts again.
        self.assert_second_opinion(self.status_of(), ["requested", "merge-risk"])

        self.deep("--focus", "second-opinion")
        self.assertEqual(self.status_of()["reason"], "converged")

        self.begin("followup", "--second-opinion")
        self.record(output(findings=[]))
        self.assert_second_opinion(self.status_of(), ["requested"])

    def test_request_is_not_met_by_an_earlier_second_opinion_round(self):
        self.deep("--focus", "second-opinion")
        self.begin("followup", "--second-opinion")
        self.record(output(findings=[]))
        self.assert_second_opinion(self.status_of(), ["requested"])

    def begin_empty_followup(self, *extra):
        """A follow-up on the anchor's own snapshot: no changes, so the round is not saved."""
        snap = self.cli("snapshot")["snapshot"]
        result = self.cli(
            "begin-round", "--mode", "followup", "--model", "m", "--snapshot", snap, "--base-ref", "main", *extra
        )
        self.assertTrue(result["empty"], result)
        self.assertFalse(result["saved"], result)
        return result

    def test_second_opinion_flag_on_empty_round_is_stored(self):
        self.deep()
        self.assertEqual(self.status_of()["reason"], "converged")
        self.begin_empty_followup("--second-opinion")
        self.assertEqual(self.state()["second_opinion"], {"requested": 2, "skipped": False})
        self.assertEqual(len(self.state()["rounds"]), 1)
        self.assert_second_opinion(self.status_of(), ["requested"])
        self.assertIn("second-opinion-pending", self.cli("covered")["reasons"])

    def test_no_second_opinion_on_empty_round_is_stored(self):
        self.deep(merge_risk=MODERATE)
        self.assertEqual(self.status_of()["reason"], "second-opinion")
        self.begin_empty_followup("--no-second-opinion")
        self.assertEqual(self.state()["second_opinion"], {"requested": None, "skipped": True})
        result = self.status_of()
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")
        self.assertNotIn("second-opinion-pending", self.cli("covered")["reasons"])

    def test_second_opinion_flags_are_mutually_exclusive(self):
        snap = self.cli("snapshot")["snapshot"]
        self.assert_error(
            "begin-round",
            "--mode",
            "deep",
            "--model",
            "m",
            "--snapshot",
            snap,
            "--second-opinion",
            "--no-second-opinion",
        )

    def test_v07_state_is_exempt(self):
        self.deep(merge_risk=MODERATE)
        self.begin("followup")
        outside = {"file": "z.py", "lines": "3", "title": "Old bug.", "detail": "d"}
        self.record(output(findings=[], outside_change=[outside]))
        path = self.review_json
        data = json.loads(path.read_text())
        data.pop("second_opinion")
        for rnd in data["rounds"]:
            for key in ("plugin_version", "focus", "mode_reason"):
                rnd.pop(key)
        finding_ = data["findings"][0]
        finding_["status"] = "deferred"
        finding_["resolution"]["status"] = "deferred"
        path.write_text(json.dumps(data))
        self.assertEqual(review_state.second_opinion_due(data), (False, []))
        result = self.status_of()
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")
        self.assertEqual(result["second_opinion_reasons"], [])
        self.assertEqual(result["counts"]["outside"], 1)
        covered = self.cli("covered")
        self.assertEqual(covered["reasons"], ["changes-since-review"])
        self.assertNotIn("second-opinion-pending", covered["reasons"])

    def test_v07_state_honors_a_request(self):
        self.deep(merge_risk=MODERATE)
        path = self.review_json
        data = json.loads(path.read_text())
        data.pop("second_opinion")
        for rnd in data["rounds"]:
            for key in ("plugin_version", "focus", "mode_reason"):
                rnd.pop(key)
        path.write_text(json.dumps(data))
        self.assertEqual(self.status_of()["reason"], "converged")
        self.begin_empty_followup("--second-opinion")
        # The request counts; the moderate risk is still exempt.
        self.assert_second_opinion(self.status_of(), ["requested"])
        self.assertIn("second-opinion-pending", self.cli("covered")["reasons"])

    def test_repeat_request_at_cap_runs_then_cap_stops(self):
        self.deep("--focus", "second-opinion")
        for _ in range(4):
            self.begin("followup")
            self.record(output(findings=[]))
        self.begin("followup", "--second-opinion")
        self.record(output(findings=[]))
        result = self.status_of()
        self.assertEqual(result["rounds"], 6)
        self.assert_second_opinion(result, ["requested"])
        self.assertIn("second-opinion-pending", self.cli("covered")["reasons"])
        # The past-cap second opinion finds something: the cap now stops the loop.
        self.begin("deep", "--focus", "second-opinion")
        self.record(output(findings=[finding()]))
        result = self.status_of()
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "max-rounds")

    def test_forced_deep_starts_new_cycle(self):
        self.deep(merge_risk=MODERATE)
        self.assertEqual(review_state.second_opinion_due(self.state()), (True, ["merge-risk"]))
        self.deep("--mode-reason", "history-rewritten")
        self.assertEqual(review_state.second_opinion_due(self.state()), (False, []))
        self.assertEqual(self.status_of()["reason"], "converged")

    def test_forced_deep_ignores_majors_from_earlier_cycles(self):
        self.begin()
        self.record(output(findings=[finding(severity="major")]))
        self.set_status("R1-F1", "invalid", "--note", "n")
        self.deep("--mode-reason", "paths-changed")
        self.assertEqual(review_state.second_opinion_due(self.state()), (False, []))

    def test_cycle_start_is_the_last_forced_deep_round(self):
        rounds = [
            {"number": 1, "mode_reason": None},
            {"number": 2, "mode_reason": "snapshot-missing"},
            {"number": 3, "mode_reason": "continuing"},
            {"number": 4, "mode_reason": "paths-changed"},
            {"number": 5},
        ]
        self.assertEqual(review_state._cycle_start(rounds), 4)
        self.assertEqual(review_state._cycle_start(rounds[:3]), 2)
        self.assertEqual(review_state._cycle_start(rounds[:1]), 1)
        self.assertEqual(review_state._cycle_start([]), 1)

    def test_begin_round_records_plugin_version_focus_and_reason(self):
        self.begin("deep", "--focus", "second-opinion", "--mode-reason", "continuing")
        rnd = self.state()["rounds"][0]
        self.assertEqual(rnd["plugin_version"], review_state.PLUGIN_VERSION)
        self.assertEqual(rnd["focus"], "second-opinion")
        self.assertEqual(rnd["mode_reason"], "continuing")

    def test_begin_round_defaults_leave_focus_and_reason_empty(self):
        self.begin()
        rnd = self.state()["rounds"][0]
        self.assertIsNone(rnd["focus"])
        self.assertIsNone(rnd["mode_reason"])
        self.assertEqual(self.state()["second_opinion"], {"requested": None, "skipped": False})

    def test_plugin_version_comes_from_the_manifest(self):
        manifest = Path(review_state.__file__).resolve().parent.parent / ".claude-plugin" / "plugin.json"
        self.assertEqual(review_state.PLUGIN_VERSION, json.loads(manifest.read_text())["version"])

    def test_begin_round_rejects_an_unknown_focus(self):
        snap = self.cli("snapshot")["snapshot"]
        self.assert_error("begin-round", "--mode", "deep", "--model", "m", "--snapshot", snap, "--focus", "nope")


if __name__ == "__main__":
    unittest.main()
