import json
import unittest

from helpers import LedgerHarness, finding, output, report
import review_state


class LedgerTest(LedgerHarness, unittest.TestCase):
    # record-findings
    def test_record_assigns_ids_and_open_status(self):
        self.begin()
        result = self.record(output())
        self.assertEqual(result["added"], 2)
        findings = self.state()["findings"]
        self.assertEqual([f["id"] for f in findings], ["R1-F1", "R1-F2"])
        for f in findings:
            self.assertEqual(f["status"], "open")
            self.assertEqual(f["scope"], "change")
            self.assertEqual(f["round_opened"], 1)
            self.assertIsNone(f["round_closed"])
            self.assertIsNone(f["resolution"])
            self.assertEqual(f["discussion"], [])
            self.assertEqual(f["effort"], "quick")
            self.assertEqual(f["evidence"], "")
        rnd = self.state()["rounds"][0]
        self.assertEqual(rnd["summary"], "Adds b. Looks fine.")
        self.assertEqual(rnd["merge_risk"]["level"], "low")
        self.assertEqual(rnd["intent_check"]["unmet_claims"], [])
        self.assertEqual(rnd["added"], 2)

    def test_record_accepts_bare_json_file(self):
        self.begin()
        self.record(None, raw=json.dumps(output()), name="bare.json")
        self.assertEqual(len(self.state()["findings"]), 2)

    def test_record_uses_last_json_block(self):
        self.begin()
        text = '```json\n{"example": true}\n```\n\n' + report(output())
        self.record(None, raw=text)
        self.assertEqual(len(self.state()["findings"]), 2)

    def test_record_outside_change_is_outside(self):
        self.begin()
        self.record(
            output(
                findings=[finding()],
                outside_change=[{"file": "z.py", "lines": "3", "title": "Old bug.", "detail": "d"}],
            )
        )
        findings = self.state()["findings"]
        self.assertEqual([f["id"] for f in findings], ["R1-F1", "R1-F2"])
        out = findings[1]
        self.assertEqual(out["scope"], "outside")
        self.assertEqual(out["status"], "outside")
        self.assertEqual(out["resolution"]["status"], "outside")
        self.assertEqual(self.cli("status")["counts"]["outside"], 1)
        self.assertEqual(out["severity"], "minor")
        self.assertEqual(out["category"], "logic")
        self.assertEqual(out["resolution"]["note"], "outside the change set")
        self.assertEqual(self.state()["rounds"][0]["added"], 1)

    def outside_seeded(self):
        self.seeded(
            findings=[],
            outside_change=[{"file": "z.py", "lines": "3", "title": "Old bug.", "detail": "d"}],
        )

    def test_load_maps_v07_outside_deferred_to_outside(self):
        self.begin()
        self.record(output(findings=[]))
        state = self.state()
        state["rounds"][0].pop("plugin_version", None)  # a v0.7 round carries none
        state["findings"] = [
            {
                "id": "R1-F1",
                "status": "deferred",
                "scope": "outside",
                "round_opened": 1,
                "round_closed": None,
                "severity": "minor",
                "category": "logic",
                "file": "z.py",
                "lines": "3",
                "title": "Old bug.",
                "resolution": {
                    "status": "deferred",
                    "note": "outside the change set",
                    "commit": "",
                    "by": "reviewer",
                    "round": 1,
                },
                "discussion": [],
            }
        ]
        self.review_json.write_text(json.dumps(state))
        counts = self.cli("status")["counts"]
        self.assertEqual(counts["outside"], 1)
        self.assertEqual(counts["deferred"], 0)
        # Loading again changes nothing.
        self.assertEqual(self.cli("status")["counts"], counts)

    def test_developer_deferred_stays_deferred(self):
        self.seeded()
        self.set_status("R1-F1", "escalated", "--note", "q")
        self.cli("resolve", "--id", "R1-F1", "--decision", "defer", "--note", "later")
        self.assertEqual(self.find("R1-F1")["status"], "deferred")
        self.assertEqual(self.cli("status")["counts"]["deferred"], 1)
        self.assertEqual(self.find("R1-F1")["status"], "deferred")

    def test_set_status_escalated_from_outside(self):
        self.outside_seeded()
        self.assertEqual(self.set_status("R1-F1", "escalated", "--note", "n")["status"], "escalated")
        result = self.cli("status")
        self.assertEqual(result["reason"], "converged")
        self.assertFalse(result["continue"])

    def test_summary_lists_outside_section(self):
        self.outside_seeded()
        text = self.cli("summary")["text"]
        invalid, outside, fixed = (
            text.index(h) for h in ("## Invalid", "## Outside the change", "## Fixed and verified")
        )
        self.assertLess(invalid, outside)
        self.assertLess(outside, fixed)
        self.assertIn("R1-F1", text[outside:fixed])
        deferred = text[text.index("## Deferred") : invalid]
        self.assertNotIn("R1-F1", deferred)

    def test_summary_intent_check_section(self):
        self.begin()
        self.record(
            output(
                findings=[],
                intent_check={"unmet_claims": ["A"], "unmentioned_behavior": ["X"]},
            )
        )
        self.begin("followup")
        self.record(
            output(
                findings=[],
                intent_check={"unmet_claims": [], "unmentioned_behavior": ["X", "Y"]},
            )
        )
        text = self.cli("summary")["text"]
        section = text[text.index("## Intent check") : text.index("## Escalated")]
        self.assertLess(text.index("## Open and unverified"), text.index("## Intent check"))
        self.assertEqual(section.count("A"), 1, section)
        self.assertEqual(section.count("X"), 1, section)
        self.assertEqual(section.count("Y"), 1, section)
        self.assertLess(section.index("X"), section.index("Y"))

    def test_summary_header_names_second_opinion(self):
        self.begin()
        self.record(output(findings=[finding(severity="major")]))
        self.set_status("R1-F1", "escalated", "--note", "needs a decision")
        self.begin("deep", "--focus", "second-opinion")
        self.record(output(findings=[]))
        self.assertIn("last deep (second opinion) with", self.cli("summary")["text"])

    def test_summary_lists_every_round(self):
        self.begin()
        self.record(output(findings=[finding(severity="major")]))
        self.set_status("R1-F1", "escalated", "--note", "needs a decision")
        self.begin("deep", "--focus", "second-opinion")
        self.record(output(findings=[]))
        self.begin("followup")
        self.record(output(findings=[]))
        lines = self.cli("summary")["text"].splitlines()
        self.assertIn("last followup with", lines[0])
        self.assertEqual(lines[2], "Rounds: 1 deep (m), 2 deep (second opinion) (m), 3 followup (m)")

    def assert_rejected(self, text_or_data, **kw):
        self.begin()
        before = self.review_json.read_bytes()
        raw = text_or_data if isinstance(text_or_data, str) else report(text_or_data)
        path = self.write("bad.md", raw)
        self.assert_error("record-findings", "--file", path, *kw.get("extra", ()))
        self.assertEqual(self.review_json.read_bytes(), before)

    def test_record_rejects_missing_json_block(self):
        self.assert_rejected("# Review\n\nno block here\n")

    def test_record_rejects_unparseable_block(self):
        self.assert_rejected("```json\n{not json\n```\n")

    def test_record_rejects_bad_enum(self):
        self.assert_rejected(output(findings=[finding(severity="high")]))

    def test_record_rejects_missing_keys(self):
        data = output()
        del data["merge_risk"]
        self.assert_rejected(data)
        bad = finding()
        del bad["trigger"]
        self.assert_rejected(output(findings=[bad]))
        self.assert_rejected(output(merge_risk={"level": "severe", "reason": "x"}))

    def test_record_rejects_unknown_verdict_id(self):
        self.assert_rejected(output(verdicts=[{"id": "R9-F9", "status": "verified", "reason": "x"}]))

    def test_record_rejects_non_utf8_file(self):
        self.begin()
        before = self.review_json.read_bytes()
        bad = self.tmp / "bad.md"
        bad.write_bytes(b"# Review\n\xff\xfe\n")
        self.assert_error("record-findings", "--file", str(bad))
        self.assertEqual(self.review_json.read_bytes(), before)

    def test_record_bare_json_parse_error_says_invalid_json(self):
        self.begin()
        path = self.write("bare.json", '{"summary": "x", ')
        code, out, err = self.call("record-findings", "--file", path)
        self.assertEqual((code, out), (1, ""))
        self.assertIn("invalid JSON", err)
        self.assertNotIn("no json block", err)

    def test_record_rejects_empty_merge_risk_reason_and_non_string_intent_items(self):
        self.assert_rejected(output(merge_risk={"level": "low", "reason": ""}))
        self.assert_rejected(output(merge_risk={"level": "low", "reason": "   "}))
        self.assert_rejected(output(intent_check={"unmet_claims": [1], "unmentioned_behavior": []}))
        self.assert_rejected(output(intent_check={"unmet_claims": [], "unmentioned_behavior": [{"a": 1}]}))

    def test_record_rejects_duplicate_verdict_ids(self):
        self.begin()
        self.record(output())
        self.assert_rejected(
            output(
                findings=[],
                verdicts=[
                    {"id": "R1-F1", "status": "verified", "reason": "ok"},
                    {"id": "R1-F1", "status": "still-open", "reason": "no"},
                ],
            )
        )

    def test_record_rejects_verdict_on_closed_finding(self):
        self.begin()
        self.record(output(outside_change=[{"file": "z.py", "title": "Old bug."}]))
        self.set_status("R1-F1", "invalid", "--note", "n")
        self.set_status("R1-F2", "escalated", "--note", "q")
        for fid in ("R1-F1", "R1-F2", "R1-F3"):
            with self.subTest(fid=fid):
                self.assert_rejected(output(findings=[], verdicts=[{"id": fid, "status": "verified"}]))

    def test_record_rejects_valid_verdict_with_later_invalid_finding(self):
        self.begin()
        self.record(output())
        self.assert_rejected(
            output(
                verdicts=[{"id": "R1-F1", "status": "verified", "reason": "ok"}],
                findings=[finding(), finding(reachable_now="maybe")],
            )
        )

    def test_still_open_verdict_clears_round_closed(self):
        self.seeded()
        self.set_status("R1-F1", "fixed", "--note", "guarded")
        state = self.state()
        state["findings"][0]["round_closed"] = 1  # a stale value from a hand edit
        self.review_json.write_text(json.dumps(state))
        self.begin("followup")
        self.record(output(findings=[], verdicts=[{"id": "R1-F1", "status": "still-open", "reason": "no"}]))
        f = self.find("R1-F1")
        self.assertEqual(f["status"], "still-open")
        self.assertIsNone(f["round_closed"])

    def test_record_report_copy_failure_leaves_state_untouched(self):
        self.begin()
        before = self.review_json.read_bytes()
        (self.review_json.parent / "rounds").write_text("not a directory")
        path = self.write("report.md", report(output()))
        self.assert_error("record-findings", "--file", path, "--report")
        self.assertEqual(self.review_json.read_bytes(), before)

    def test_record_report_from_its_own_copy(self):
        self.begin()
        rounds = self.review_json.parent / "rounds"
        rounds.mkdir()
        copy = rounds / "1.md"
        copy.write_text(report(output()))
        result = self.cli("record-findings", "--file", str(copy), "--report")
        self.assertEqual(result["added"], 2)
        self.assertEqual(copy.read_text(), report(output()))

    def test_record_rejects_without_round(self):
        path = self.write("r.md", report(output()))
        self.assert_error("record-findings", "--file", path)

    def test_record_rejects_second_record_for_round(self):
        self.begin()
        self.record(output())
        before = self.review_json.read_bytes()
        path = self.write("again.md", report(output()))
        self.assert_error("record-findings", "--file", path)
        self.assertEqual(self.review_json.read_bytes(), before)

    def test_record_applies_verdicts_by_id(self):
        self.begin()
        self.record(output())
        (self.repo / "b.txt").unlink()  # the finding's file no longer exists
        self.begin("followup")
        self.record(
            output(
                findings=[],
                verdicts=[
                    {"id": "R1-F1", "status": "verified", "reason": "gone"},
                    {"id": "R1-F2", "status": "still-open", "reason": "not done"},
                ],
            )
        )
        f1, f2 = self.find("R1-F1"), self.find("R1-F2")
        self.assertEqual(f1["status"], "verified")
        self.assertEqual(f1["round_closed"], 2)
        self.assertEqual(f2["status"], "still-open")
        self.assertIsNone(f2["round_closed"])
        self.assertEqual(f1["discussion"][-1]["from"], "reviewer")

    def test_record_ids_continue_per_round(self):
        self.begin()
        self.record(output())
        self.begin("followup")
        self.record(output(findings=[finding()]))
        self.assertEqual([f["id"] for f in self.state()["findings"]], ["R1-F1", "R1-F2", "R2-F1"])

    def test_record_copies_report(self):
        self.begin()
        text = report(output())
        self.record(None, "--report", raw=text)
        copy = self.review_json.parent / "rounds" / "1.md"
        self.assertEqual(copy.read_text(), text)

    # set-status
    def test_set_status_fixed_requires_commit_or_note(self):
        self.seeded()
        self.assert_error("set-status", "--id", "R1-F1", "--status", "fixed")
        self.assertEqual(self.find("R1-F1")["status"], "open")
        self.set_status("R1-F1", "fixed", "--commit", "abc123")
        f = self.find("R1-F1")
        self.assertEqual(f["status"], "fixed")
        self.assertEqual(
            f["resolution"],
            {"status": "fixed", "note": "", "commit": "abc123", "by": "orchestrator", "round": 1},
        )
        self.assertIsNone(f["round_closed"])

    def test_set_status_invalid_records_note(self):
        self.seeded()
        self.assert_error("set-status", "--id", "R1-F1", "--status", "invalid")
        self.set_status("R1-F1", "invalid", "--note", "not reachable")
        f = self.find("R1-F1")
        self.assertEqual(f["status"], "invalid")
        self.assertEqual(f["resolution"]["note"], "not reachable")
        self.assertEqual(f["round_closed"], 1)

    def test_set_status_escalated_and_open(self):
        self.seeded()
        self.assert_error("set-status", "--id", "R1-F1", "--status", "escalated")
        self.set_status("R1-F1", "escalated", "--note", "needs a call")
        self.assertEqual(self.find("R1-F1")["round_closed"], 1)
        self.set_status("R1-F1", "open")
        f = self.find("R1-F1")
        self.assertEqual(f["status"], "open")
        self.assertIsNone(f["round_closed"])

    def test_set_status_rejects_bad_target_and_unknown_id(self):
        self.seeded()
        self.assert_error("set-status", "--id", "R1-F1", "--status", "verified")
        self.assert_error("set-status", "--id", "R7-F1", "--status", "open")

    # discuss
    def test_discuss_appends(self):
        self.seeded()
        self.cli("discuss", "--id", "R1-F1", "--from", "orchestrator", "--text", "one")
        self.cli("discuss", "--id", "R1-F1", "--from", "reviewer", "--text", "two")
        self.assertEqual(
            self.find("R1-F1")["discussion"],
            [
                {"from": "orchestrator", "text": "one", "round": 1},
                {"from": "reviewer", "text": "two", "round": 1},
            ],
        )
        self.assert_error("discuss", "--id", "R1-F1", "--from", "nobody", "--text", "x")

    # resolve
    def test_resolve_only_from_escalated(self):
        self.seeded()
        self.assert_error("resolve", "--id", "R1-F1", "--decision", "fix", "--note", "do it")
        self.set_status("R1-F1", "escalated", "--note", "q")
        self.cli("resolve", "--id", "R1-F1", "--decision", "fix", "--note", "do it")
        f = self.find("R1-F1")
        self.assertEqual(f["status"], "open")
        self.assertEqual(f["resolution"]["note"], "do it")
        self.assertEqual(f["resolution"]["by"], "developer")
        self.assertIsNone(f["round_closed"])

        self.set_status("R1-F2", "escalated", "--note", "q")
        self.cli("resolve", "--id", "R1-F2", "--decision", "defer", "--note", "later")
        f = self.find("R1-F2")
        self.assertEqual(f["status"], "deferred")
        self.assertEqual(f["round_closed"], 1)
        self.assertEqual(f["resolution"]["by"], "developer")

    # status
    def test_status_continue_when_open(self):
        self.seeded()
        result = self.cli("status")
        self.assertTrue(result["continue"])
        self.assertEqual(result["reason"], "open-findings")
        self.assertEqual(result["counts"]["open"], 2)

    def test_status_continue_when_fixed_unverified(self):
        self.seeded()
        self.set_status("R1-F1", "fixed", "--commit", "c1")
        self.set_status("R1-F2", "invalid", "--note", "n")
        result = self.cli("status")
        self.assertTrue(result["continue"])
        self.assertEqual(result["reason"], "unverified-fixes")

    def test_status_new_findings_all_invalid_or_escalated_converges(self):
        self.begin()
        self.record(output())
        self.set_status("R1-F1", "invalid", "--note", "n")
        self.set_status("R1-F2", "escalated", "--note", "q")
        result = self.cli("status")
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")

    def test_status_converged(self):
        self.seeded(findings=[finding(), finding(title="Second problem."), finding(title="Not a problem.")])
        self.set_status("R1-F1", "fixed", "--commit", "c1")
        self.set_status("R1-F2", "escalated", "--note", "q")
        self.set_status("R1-F3", "invalid", "--note", "guarded upstream")
        self.begin("followup")
        self.record(
            output(
                findings=[],
                verdicts=[{"id": "R1-F1", "status": "verified", "reason": "ok"}],
            )
        )
        result = self.cli("status")
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")
        self.assertEqual(result["counts"]["verified"], 1)
        self.assertEqual(result["counts"]["escalated"], 1)
        self.assertEqual(result["counts"]["invalid"], 1)

    def test_status_deep_round_with_nothing_is_converged(self):
        self.begin()
        self.record(output(findings=[]))
        result = self.cli("status")
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")

    def rounds_with_open_findings(self, count):
        for _ in range(count):
            self.begin()
            self.record(output())

    def test_status_max_rounds(self):
        self.rounds_with_open_findings(6)
        result = self.cli("status")
        self.assertEqual(result["rounds"], 6)
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "max-rounds")

    def test_status_max_rounds_counts_rounds_after_since_round(self):
        self.rounds_with_open_findings(7)
        result = self.cli("status", "--since-round", "3")
        self.assertEqual(result["rounds"], 7)
        self.assertTrue(result["continue"])
        self.assertEqual(result["reason"], "open-findings")
        result = self.cli("status", "--since-round", "0")
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "max-rounds")

    def test_status_converged_on_sixth_round_is_not_max_rounds(self):
        for _ in range(6):
            self.begin()
            self.record(output(findings=[]))
        result = self.cli("status")
        self.assertEqual(result["rounds"], 6)
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")

    def test_cap_counts_from_last_forced_deep_round(self):
        self.rounds_with_open_findings(5)
        self.begin("deep", "--mode-reason", "history-rewritten")
        self.record(output())
        result = self.cli("status")
        self.assertEqual(result["rounds"], 6)
        self.assertTrue(result["continue"])
        self.assertEqual(result["reason"], "open-findings")

    def test_final_verification_at_cap(self):
        for _ in range(5):
            self.begin()
            self.record(output(findings=[]))
        self.begin()
        self.record(output(findings=[finding()]))
        self.set_status("R6-F1", "fixed", "--commit", "c1")
        result = self.cli("status")
        self.assertTrue(result["continue"])
        self.assertEqual(result["reason"], "final-verification")
        self.assertEqual(result["next_mode"], "followup")
        self.assertIsNone(result["next_focus"])
        self.assertEqual(result["second_opinion_reasons"], [])
        self.begin("followup")
        self.record(output(findings=[], verdicts=[{"id": "R6-F1", "status": "verified", "reason": "ok"}]))
        result = self.cli("status")
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "converged")

    def test_unverified_fixes_after_final_verification_hit_the_cap(self):
        for _ in range(5):
            self.begin()
            self.record(output(findings=[]))
        self.begin()
        self.record(output(findings=[finding()]))
        self.set_status("R6-F1", "fixed", "--commit", "c1")
        self.begin("followup")
        self.record(output(findings=[]))
        result = self.cli("status")
        self.assertFalse(result["continue"])
        self.assertEqual(result["reason"], "max-rounds")

    def test_second_opinion_runs_past_cap(self):
        self.begin()
        self.record(output(findings=[], merge_risk={"level": "moderate", "reason": "shared state"}))
        for _ in range(5):
            self.begin()
            self.record(output(findings=[]))
        result = self.cli("status")
        self.assertEqual(result["rounds"], 6)
        self.assertTrue(result["continue"])
        self.assertEqual(result["reason"], "second-opinion")
        self.assertEqual(result["next_mode"], "deep")
        self.assertEqual(result["next_focus"], "second-opinion")

    def test_cap_floor(self):
        plain = [{"number": n} for n in range(1, 5)]
        self.assertEqual(review_state._cap_floor(plain, 0), 0)
        self.assertEqual(review_state._cap_floor(plain, 2), 2)
        forced = plain + [{"number": 5, "mode_reason": "paths-changed"}]
        self.assertEqual(review_state._cap_floor(forced, 0), 4)
        self.assertEqual(review_state._cap_floor(forced, 4), 4)
        self.assertEqual(review_state._cap_floor(forced, 5), 5)

    def test_status_without_state_is_an_error(self):
        self.assert_error("status")

    def test_status_and_summary_reject_unrecorded_last_round(self):
        self.seeded()
        self.begin("followup")  # dispatched, never recorded
        for command in ("status", "summary"):
            with self.subTest(command=command):
                code, out, err = self.call(command)
                self.assertEqual(code, 1, command)
                self.assertEqual(out, "")
                self.assertIn("round 2 has no recorded findings", err)
                self.assertEqual(len(err.strip().splitlines()), 1, err)

    def test_status_rejects_unrecorded_only_round(self):
        self.begin()
        code, out, err = self.call("status")
        self.assertEqual(code, 1)
        self.assertIn("round 1 has no recorded findings", err)

    # text files
    def test_set_status_note_file(self):
        self.seeded()
        text = 'it\'s "quoted" $(touch pwned) `x` \\ done'
        note = self.write("note.md", "\n" + text + "\n\n")
        self.set_status("R1-F1", "escalated", "--note-file", note)
        f = self.find("R1-F1")
        self.assertEqual(f["status"], "escalated")
        self.assertEqual(f["resolution"]["note"], text)
        self.assertFalse((self.repo / "pwned").exists())

    def test_set_status_note_file_empty_counts_as_no_note(self):
        self.seeded()
        empty = self.write("empty.md", "  \n\n")
        self.assert_error("set-status", "--id", "R1-F1", "--status", "invalid", "--note-file", empty)
        self.assertEqual(self.find("R1-F1")["status"], "open")

    def test_discuss_text_file(self):
        self.seeded()
        path = self.write("reply.md", "R1-F1 upheld: the guard on line 3 is skipped when x='a\"b'\n")
        self.cli("discuss", "--id", "R1-F1", "--from", "reviewer", "--text-file", path)
        entry = self.find("R1-F1")["discussion"][-1]
        self.assertEqual(entry["text"], "R1-F1 upheld: the guard on line 3 is skipped when x='a\"b'")
        self.assertEqual(entry["from"], "reviewer")

    def test_resolve_note_file(self):
        self.seeded()
        self.set_status("R1-F1", "escalated", "--note", "q")
        path = self.write("decision.md", "Fix it; keep the 'legacy' path.\n")
        self.cli("resolve", "--id", "R1-F1", "--decision", "fix", "--note-file", path)
        f = self.find("R1-F1")
        self.assertEqual(f["status"], "open")
        self.assertEqual(f["resolution"]["note"], "Fix it; keep the 'legacy' path.")

    def test_text_file_forms_reject_empty_missing_and_both(self):
        self.seeded()
        self.set_status("R1-F2", "escalated", "--note", "q")
        empty = self.write("empty.md", "\n \n")
        good = self.write("good.md", "ok\n")
        missing = str(self.tmp / "missing.md")
        binary = self.tmp / "binary.md"
        binary.write_bytes(b"\xff\xfe\x00")
        before = self.review_json.read_bytes()
        for argv in (
            ("discuss", "--id", "R1-F1", "--from", "reviewer", "--text-file", empty),
            ("discuss", "--id", "R1-F1", "--from", "reviewer", "--text-file", missing),
            ("discuss", "--id", "R1-F1", "--from", "reviewer", "--text-file", str(binary)),
            ("discuss", "--id", "R1-F1", "--from", "reviewer", "--text", "x", "--text-file", good),
            ("discuss", "--id", "R1-F1", "--from", "reviewer"),
            ("resolve", "--id", "R1-F2", "--decision", "defer", "--note-file", empty),
            ("resolve", "--id", "R1-F2", "--decision", "defer", "--note", "x", "--note-file", good),
            ("resolve", "--id", "R1-F2", "--decision", "defer"),
            ("set-status", "--id", "R1-F1", "--status", "fixed", "--note", "x", "--note-file", good),
            ("set-status", "--id", "R1-F1", "--status", "fixed", "--note-file", missing),
        ):
            with self.subTest(argv=argv):
                self.assert_error(*argv)
        self.assertEqual(self.review_json.read_bytes(), before)


class ExtractJsonBlockTest(unittest.TestCase):
    def test_returns_last_block(self):
        text = '```json\n{"a": 1}\n```\ntext\n```json\n{"a": 2}\n```\n'
        self.assertEqual(review_state.extract_json_block(text), {"a": 2})

    def test_no_block(self):
        with self.assertRaisesRegex(ValueError, "no json block"):
            review_state.extract_json_block("nothing")

    def test_parse_error(self):
        with self.assertRaisesRegex(ValueError, "json parse error"):
            review_state.extract_json_block("```json\n{oops\n```")

    def test_fence_tag_is_case_insensitive(self):
        self.assertEqual(review_state.extract_json_block('```JSON\n{"a": 1}\n```\n'), {"a": 1})
        self.assertEqual(review_state.extract_json_block('```Json \n{"a": 2}\n```'), {"a": 2})

    def test_crlf_report(self):
        text = '# Review\r\n\r\n```json\r\n{"a": 1,\r\n "b": [2]}\r\n```\r\n'
        self.assertEqual(review_state.extract_json_block(text), {"a": 1, "b": [2]})


if __name__ == "__main__":
    unittest.main()
