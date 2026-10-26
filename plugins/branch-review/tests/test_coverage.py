"""Coverage and Not checked in reviewer reports, and the variants and fix_options of findings."""

import json
import unittest

from helpers import LedgerHarness, finding, full_coverage, output


class CoverageTest(LedgerHarness, unittest.TestCase):
    def record_error(self, data):
        self.begin()
        code, out, err = self.call("record-findings", "--file", self.write("r.json", json.dumps(data)))
        self.assertEqual(code, 1, err)
        self.assertNotIn("added", self.state()["rounds"][-1])
        return err

    def test_deep_report_missing_coverage_item_is_refused(self):
        coverage = [c for c in full_coverage() if c["item"] != "consumer-build"]
        err = self.record_error(output(coverage=coverage))
        self.assertIn("coverage is missing consumer-build", err)

    def test_deep_report_without_not_checked_is_refused(self):
        data = output()
        del data["not_checked"]
        self.assertIn("not_checked", self.record_error(data))

    def test_empty_done_is_refused(self):
        coverage = full_coverage()
        coverage[0]["done"] = ""
        self.assertIn("done", self.record_error(output(coverage=coverage)))

    def test_fix_options_with_one_entry_is_refused(self):
        self.assertIn("fix_options", self.record_error(output(findings=[finding(fix_options=["only"])])))

    def test_variant_result_enum(self):
        bad = finding(variants=[{"input": "System.Int64", "result": "maybe"}])
        self.assertIn("result", self.record_error(output(findings=[bad])))

    def test_followup_report_may_omit_coverage(self):
        self.begin()
        self.record(output(findings=[]))
        self.begin("followup")
        data = output(findings=[])
        del data["coverage"], data["not_checked"]
        self.assertEqual(self.record(data)["added"], 0)

    def test_v08_round_is_not_checked(self):
        self.begin()
        state = self.state()
        state["rounds"][-1]["plugin_version"] = "0.8.1"
        self.review_json.write_text(json.dumps(state))
        data = output(findings=[])
        del data["coverage"], data["not_checked"]
        self.assertEqual(self.record(data)["added"], 0)

    def test_coverage_and_not_checked_stored(self):
        self.begin()
        missed = [{"what": "probe the decimal rounding", "reason": "Docker is not available"}]
        self.record(output(findings=[], not_checked=missed))
        rnd = self.state()["rounds"][-1]
        self.assertEqual(rnd["coverage"], full_coverage())
        self.assertEqual(rnd["not_checked"], missed)

    def test_variants_and_fix_options_stored(self):
        variants = [{"input": "System.Int64", "result": "fails"}, {"input": "long", "result": "ok"}]
        self.begin()
        self.record(output(findings=[finding(variants=variants, fix_options=["a", "b"]), finding()]))
        self.assertEqual(self.find("R1-F1")["variants"], variants)
        self.assertEqual(self.find("R1-F1")["fix_options"], ["a", "b"])
        self.assertEqual(self.find("R1-F2")["variants"], [])
        self.assertEqual(self.find("R1-F2")["fix_options"], [])

    def test_locate_earlier_not_checked(self):
        self.begin()
        self.record(output(findings=[], not_checked=[{"what": "probe X", "reason": "no docker"}]))
        found = self.cli("locate", "--base", "main")["earlier_not_checked"]
        self.assertEqual(found, [{"what": "probe X", "reason": "no docker", "round": 1}])
        (self.repo / "c.txt").write_text("c\n")
        self.begin("deep", "--focus", "second-opinion")
        self.record(output(findings=[], not_checked=[{"what": "probe X", "reason": "image not pinned"}]))
        found = self.cli("locate", "--base", "main")["earlier_not_checked"]
        self.assertEqual(found, [{"what": "probe X", "reason": "image not pinned", "round": 2}])


if __name__ == "__main__":
    unittest.main()
