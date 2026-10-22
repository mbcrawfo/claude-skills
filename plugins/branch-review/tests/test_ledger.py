import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from helpers import make_repo, run
import review_state


def finding(**over):
    base = {
        "severity": "major",
        "category": "logic",
        "file": "b.txt",
        "lines": "1-2",
        "title": "Handle the empty case.",
        "trigger": "empty input",
        "consequence": "crash",
        "reachable_now": "yes",
        "fix": "add a guard",
    }
    base.update(over)
    return base


def output(**over):
    base = {
        "summary": "Adds b. Looks fine.",
        "merge_risk": {"level": "low", "reason": "small change"},
        "intent_check": {"unmet_claims": [], "unmentioned_behavior": []},
        "verdicts": [],
        "findings": [finding(), finding(title="Second problem.")],
        "outside_change": [],
    }
    base.update(over)
    return base


def report(data):
    return "# Review\n\nProse.\n\n```json\n" + json.dumps(data) + "\n```\n"


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.state_dir = self.tmp / "state"
        self.repo = make_repo(self.tmp / "r")

    # helpers
    def cli(self, *argv):
        return run(self.repo, *argv, state_dir=self.state_dir)

    def call(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = review_state.main([*argv, "--state-dir", str(self.state_dir), "--repo", str(self.repo)])
        return code, out.getvalue(), err.getvalue()

    def assert_error(self, *argv):
        code, out, err = self.call(*argv)
        self.assertEqual(code, 1, err)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)

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
            "m",
            "--snapshot",
            snap,
            "--base-ref",
            "main",
        )

    def write(self, name, text):
        path = self.tmp / name
        path.write_text(text)
        return str(path)

    def record(self, data, *extra, name="report.md", raw=None):
        path = self.write(name, raw if raw is not None else report(data))
        return self.cli("record-findings", "--file", path, *extra)

    @property
    def review_json(self):
        return Path(self.cli("locate", "--base", "main")["dir"]) / "review.json"

    def state(self):
        return json.loads(self.review_json.read_text())

    def find(self, fid):
        return next(f for f in self.state()["findings"] if f["id"] == fid)

    def set_status(self, fid, status, *extra):
        return self.cli("set-status", "--id", fid, "--status", status, *extra)

    def seeded(self, **over):
        self.begin()
        self.record(output(**over))

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

    def test_record_outside_change_is_deferred(self):
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
        self.assertEqual(out["status"], "deferred")
        self.assertEqual(out["severity"], "minor")
        self.assertEqual(out["category"], "logic")
        self.assertEqual(out["resolution"]["note"], "outside the change set")
        self.assertEqual(self.state()["rounds"][0]["added"], 1)

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

    def test_status_continue_when_last_round_added(self):
        self.begin()
        self.record(output())
        self.set_status("R1-F1", "invalid", "--note", "n")
        self.set_status("R1-F2", "invalid", "--note", "n")
        result = self.cli("status")
        self.assertTrue(result["continue"])
        self.assertEqual(result["reason"], "new-findings")

    def test_status_converged(self):
        self.seeded()
        self.set_status("R1-F1", "fixed", "--commit", "c1")
        self.set_status("R1-F2", "escalated", "--note", "q")
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


if __name__ == "__main__":
    unittest.main()
