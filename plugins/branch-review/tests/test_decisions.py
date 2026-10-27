"""The developer's decisions: accepted, the kept decision, and the fix-options gate."""

import json
import unittest

from helpers import LedgerHarness, finding, output

INTENT = "Keys are written bare.\nNo other spelling is accepted.\n"


class DecisionTest(LedgerHarness, unittest.TestCase):
    def seed(self, **over):
        self.begin("deep", "--intent-file", self.write("intent.md", INTENT))
        self.record(output(findings=[finding(**over)]))

    def escalate(self, fid="R1-F1"):
        self.set_status(fid, "escalated", "--note", "needs a decision")

    def resolve(self, decision, fid="R1-F1"):
        return self.cli("resolve", "--id", fid, "--decision", decision, "--note", "owner said so")

    def gate_error(self, *extra):
        code, out, err = self.call("set-status", "--id", "R1-F1", "--status", "fixed", "--note", "n", *extra)
        self.assertEqual(code, 1, out)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertIn("R1-F1 offers fix options", err)
        return err

    # an intent that predates a decision
    def test_begin_round_flags_decisions_the_unchanged_intent_predates(self):
        self.seed()
        self.escalate()
        self.cli("resolve", "--id", "R1-F1", "--decision", "fix", "--note", "do it the owner's way")
        result = self.begin("followup", "--intent-file", self.write("intent.md", INTENT))
        self.assertEqual(result["decisions_since_intent"], ["R1-F1"])

    def test_begin_round_again_with_an_updated_intent_clears_the_flag(self):
        self.seed()
        self.escalate()
        self.cli("resolve", "--id", "R1-F1", "--decision", "fix", "--note", "do it the owner's way")
        self.begin("followup", "--intent-file", self.write("intent.md", INTENT))
        updated = INTENT + "Per the owner, the old spelling is kept for one release.\n"
        result = self.begin("followup", "--intent-file", self.write("intent2.md", updated))
        self.assertEqual(result["decisions_since_intent"], [])

    def test_a_whitespace_only_change_to_the_intent_keeps_the_flag(self):
        self.seed()
        self.escalate()
        self.cli("resolve", "--id", "R1-F1", "--decision", "fix", "--note", "do it the owner's way")
        respaced = "  " + INTENT.replace(" ", "   ").replace("\n", "\n\n") + "\n\n"
        result = self.begin("followup", "--intent-file", self.write("intent.md", respaced))
        self.assertEqual(result["decisions_since_intent"], ["R1-F1"])

    def test_no_flag_without_a_decision_since_the_last_round(self):
        self.seed()
        self.escalate()
        self.resolve("accept")
        self.begin("followup", "--intent-file", self.write("intent.md", INTENT))
        self.record(output(findings=[]), name="r2.md")
        result = self.begin("followup", "--intent-file", self.write("intent.md", INTENT))
        self.assertEqual(result["decisions_since_intent"], [])

    # accepted
    def test_accept_decision(self):
        self.seed()
        self.escalate()
        result = self.resolve("accept")
        self.assertEqual(result["status"], "accepted")
        found = self.find("R1-F1")
        self.assertIsNotNone(found["round_closed"])
        self.assertEqual(found["decision"]["decision"], "accept")
        self.assertEqual(found["decision"]["by"], "developer")

    def test_accepted_does_not_block_covered(self):
        self.seed()
        self.escalate()
        self.resolve("accept")
        covered = self.cli("covered")
        self.assertTrue(covered["covered"], covered)
        self.assertEqual(covered["counts"]["accepted"], 1)

    # the fix-options gate
    def test_fixed_with_options_refused_without_basis(self):
        self.seed(fix_options=["a", "b"])
        err = self.gate_error()
        self.assertIn("resolve --decision fix", err)
        self.assertIn("--intent-quote-file", err)

    def test_fixed_with_options_refused_with_quote_not_in_intent(self):
        self.seed(fix_options=["a", "b"])
        self.gate_error("--intent-quote-file", self.write("q.md", "Keys may be spelled any way"))

    def test_empty_quote_refused(self):
        self.seed(fix_options=["a", "b"])
        self.gate_error("--intent-quote-file", self.write("q.md", "  \n"))

    def test_quote_checked_against_opening_round(self):
        self.seed(fix_options=["a", "b"])
        (self.repo / "more.txt").write_text("x\n")
        self.begin("followup", "--intent-file", self.write("intent2.md", INTENT + "Option b is chosen.\n"))
        self.gate_error("--intent-quote-file", self.write("q.md", "Option b is chosen."))

    def test_fixed_with_options_allowed_after_developer_fix(self):
        self.seed(fix_options=["a", "b"])
        self.escalate()
        self.resolve("fix")
        self.assertEqual(self.set_status("R1-F1", "fixed", "--note", "n")["status"], "fixed")
        self.assertEqual(self.find("R1-F1")["decision"]["decision"], "fix")

    def test_fixed_with_options_allowed_with_quote(self):
        self.seed(fix_options=["a", "b"])
        quote = self.write("q.md", "Keys are written   bare.")
        self.set_status("R1-F1", "fixed", "--note", "n", "--intent-quote-file", quote)
        self.assertEqual(self.find("R1-F1")["resolution"]["intent_quote"], "Keys are written bare.")

    def test_only_a_fix_decision_opens_the_gate(self):
        for decision in ("defer", "accept"):
            with self.subTest(decision=decision):
                self.setUp()
                self.seed(fix_options=["a", "b"])
                self.escalate()
                self.resolve(decision)
                self.set_status("R1-F1", "open", "--note", "reopened")
                self.gate_error()

    def test_quote_spanning_a_line_break_is_accepted(self):
        self.seed(fix_options=["a", "b"])
        quote = self.write("q.md", "written bare. No other spelling")
        self.assertEqual(
            self.set_status("R1-F1", "fixed", "--note", "n", "--intent-quote-file", quote)["status"], "fixed"
        )

    def test_quote_in_another_case_is_refused(self):
        self.seed(fix_options=["a", "b"])
        self.gate_error("--intent-quote-file", self.write("q.md", "keys are written bare."))

    def test_quote_uses_a_later_opening_round(self):
        self.seed()
        (self.repo / "more.txt").write_text("x\n")
        self.begin(
            "followup", "--intent-file", self.write("intent2.md", "Keys are written bare.\nOption b is chosen.\n")
        )
        self.record(output(findings=[finding(fix_options=["a", "b"])]))
        code, _, err = self.call(
            "set-status",
            "--id",
            "R2-F1",
            "--status",
            "fixed",
            "--note",
            "n",
            "--intent-quote-file",
            self.write("q1.md", "No other spelling is accepted."),
        )
        self.assertEqual(code, 1, err)
        self.assertIn("R2-F1 offers fix options", err)
        quote = self.write("q2.md", "Option b is chosen.")
        self.assertEqual(
            self.set_status("R2-F1", "fixed", "--note", "n", "--intent-quote-file", quote)["status"], "fixed"
        )

    def test_v08_finding_without_fix_options_unaffected(self):
        self.seed()
        state = self.state()
        del state["findings"][0]["fix_options"]
        self.review_json.write_text(json.dumps(state))
        self.assertEqual(self.set_status("R1-F1", "fixed", "--note", "n")["status"], "fixed")


class ConfirmBehaviorTest(LedgerHarness, unittest.TestCase):
    def confirm(self, text):
        return self.cli("confirm-behavior", "--text-file", self.write("b.md", text))

    def test_confirm_behavior_appends(self):
        self.begin()
        self.record(output(findings=[]))
        self.assertEqual(self.confirm("int4=Guid is now accepted")["behavior_to_confirm"], 1)
        self.assertEqual(self.confirm("SQLSRC014 no longer fires")["behavior_to_confirm"], 2)
        self.assertEqual(self.confirm("int4=Guid is now accepted\n")["behavior_to_confirm"], 2)
        texts = [b["text"] for b in self.state()["behavior_to_confirm"]]
        self.assertEqual(texts, ["int4=Guid is now accepted", "SQLSRC014 no longer fires"])

    def test_confirm_behavior_empty_refused(self):
        self.begin()
        self.record(output(findings=[]))
        self.assert_error("confirm-behavior", "--text-file", self.write("b.md", " \n"))

    def test_confirm_behavior_does_not_block_covered(self):
        self.begin()
        self.record(output(findings=[]))
        self.confirm("int4=Guid is now accepted")
        self.assertTrue(self.cli("covered")["covered"])


if __name__ == "__main__":
    unittest.main()
