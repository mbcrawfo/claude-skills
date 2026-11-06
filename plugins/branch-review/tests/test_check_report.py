"""check-report validates a reviewer report as record-findings would, and saves nothing."""

import unittest

from helpers import LedgerHarness, finding, output, report


def drifted_followup():
    """The shape follow-up reviewers wrote when they skipped the schema."""
    return {
        "mode": "followup",
        "merge_risk": "Low: docs only.",
        "verdicts": [{"id": "R1-F1", "verdict": "verified", "reason": "read it"}],
        "findings": [],
        "outside_change": [],
        "intent_check": {"unmet_claims": [], "unmentioned_behavior": []},
        "coverage": [],
        "not_checked": [],
    }


class CheckReportTest(LedgerHarness, unittest.TestCase):
    def check(self, data, name="report.md"):
        return self.call("check-report", "--file", self.write(name, report(data)))

    def test_valid_report_passes_and_saves_nothing(self):
        self.begin()
        before = self.review_json.read_bytes()
        out = self.cli("check-report", "--file", self.write("r.md", report(output())))
        self.assertEqual(out, {"ok": True, "round": 1, "mode": "deep", "findings": 2, "outside": 0, "verdicts": 0})
        self.assertEqual(self.review_json.read_bytes(), before)
        # The same file still records afterwards.
        self.assertEqual(self.record(output())["added"], 2)

    def test_refuses_what_record_findings_refuses(self):
        self.begin()
        self.record(output())
        self.begin("followup")
        before = self.review_json.read_bytes()
        code, out, err = self.check(drifted_followup())
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(self.review_json.read_bytes(), before)
        code2, _, err2 = self.call("record-findings", "--file", self.write("r2.md", report(drifted_followup())))
        self.assertEqual(code2, 1)
        self.assertEqual(err, err2)

    def test_checks_verdict_ids_against_the_ledger(self):
        self.begin()
        self.record(output())
        self.begin("followup")
        bad = output(findings=[], verdicts=[{"id": "R9-F9", "status": "verified"}])
        code, _, err = self.check(bad)
        self.assertEqual(code, 1)
        self.assertIn("unknown finding R9-F9", err)
        good = output(findings=[], verdicts=[{"id": "R1-F1", "status": "verified"}])
        self.assertEqual(self.check(good)[0], 0)

    def test_requires_coverage_in_a_deep_round(self):
        self.begin()
        code, _, err = self.check(output(coverage=[]))
        self.assertEqual(code, 1)
        self.assertIn("coverage is missing", err)

    def test_refuses_a_recorded_round(self):
        self.begin()
        self.record(output())
        code, _, err = self.check(output())
        self.assertEqual(code, 1)
        self.assertIn("already has recorded findings", err)

    def test_no_round_is_an_error(self):
        self.assert_error("check-report", "--file", self.write("r.md", report(output())))


class AllProblemsTest(LedgerHarness, unittest.TestCase):
    def test_one_line_lists_every_part_that_fails(self):
        self.begin()
        self.record(output())
        self.begin("followup")
        path = self.write("r.md", report(drifted_followup()))
        code, out, err = self.call("record-findings", "--file", path)
        self.assertEqual((code, out), (1, ""))
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        for problem in ("output is missing summary", "merge_risk must be an object", "verdicts[0] is missing status"):
            with self.subTest(problem=problem):
                self.assertIn(problem, err)

    def test_reports_each_bad_finding(self):
        self.begin()
        bad = output(findings=[finding(severity="huge"), finding(), finding(category="style")])
        code, _, err = self.call("record-findings", "--file", self.write("r.md", report(bad)))
        self.assertEqual(code, 1)
        self.assertIn("findings[0].severity", err)
        self.assertIn("findings[2].category", err)
        self.assertNotIn("findings[1]", err)

    def test_a_single_problem_keeps_its_message(self):
        self.begin()
        code, _, err = self.call("record-findings", "--file", self.write("r.md", report(output(summary=1))))
        self.assertEqual(code, 1)
        self.assertEqual(err.strip(), "error: invalid reviewer output: output.summary must be a string")


if __name__ == "__main__":
    unittest.main()
