"""The summary rewritten on every state change, its new sections, and review-notes.md."""

from pathlib import Path
import unittest

from helpers import LedgerHarness, finding, output
import review_state

VERIFY_R1_F1 = [{"id": "R1-F1", "status": "verified", "reason": "ok"}]


class SummaryTest(LedgerHarness, unittest.TestCase):
    @property
    def dir(self):
        return Path(self.cli("locate", "--base", "main")["dir"])

    def summary_text(self):
        return (self.dir / "summary.md").read_text()

    def section(self, text, title):
        """The lines under `## title` (or `### title`) up to the next heading."""
        lines = text.splitlines()
        start = next(i for i, line in enumerate(lines) if line.lstrip("#").strip() == title and line.startswith("#"))
        body = []
        for line in lines[start + 1 :]:
            if line.startswith(("#", "Nothing to report under:")):
                break
            body.append(line)
        return "\n".join(body).strip()

    # the rewrite
    def test_summary_rewritten_by_state_changes(self):
        self.begin()
        self.record(output(findings=[finding(), finding(title="Second problem.")]))
        path = self.dir / "summary.md"
        self.assertTrue(path.is_file())
        steps = {
            "set-status": lambda: self.set_status("R1-F1", "escalated", "--note", "decide"),
            "resolve": lambda: self.cli("resolve", "--id", "R1-F1", "--decision", "accept", "--note", "owner"),
            "discuss": lambda: self.cli("discuss", "--id", "R1-F2", "--from", "orchestrator", "--text", "hm"),
            "confirm-behavior": lambda: self.cli("confirm-behavior", "--text-file", self.write("b.md", "new")),
        }
        for name, step in steps.items():
            with self.subTest(command=name):
                path.unlink()
                step()
                self.assertTrue(path.is_file(), name)
                self.assertTrue((self.dir / "review-notes.md").is_file(), name)
        self.assertIn("R1-F1", self.section(self.summary_text(), "Accepted"))

    def test_rewrite_skipped_on_unrecorded_round(self):
        self.begin()
        self.record(output(findings=[finding()]))
        before = self.summary_text()
        self.begin("followup")
        self.cli("discuss", "--id", "R1-F1", "--from", "orchestrator", "--text", "hm")
        self.assertEqual(self.summary_text(), before)

    # summary.md
    def test_header_names_plugin_version(self):
        self.begin()
        self.record(output(findings=[]))
        header = self.summary_text().splitlines()[0]
        self.assertTrue(header.endswith(f"; written by branch-review {review_state.PLUGIN_VERSION}"), header)

    def test_accepted_and_behavior_sections(self):
        self.begin()
        self.record(output(findings=[finding()]))
        self.set_status("R1-F1", "escalated", "--note", "decide")
        self.cli("resolve", "--id", "R1-F1", "--decision", "accept", "--note", "owner keeps it")
        self.cli("confirm-behavior", "--text-file", self.write("b.md", "int4=Guid is now accepted"))
        text = self.summary_text()
        self.assertIn("R1-F1", self.section(text, "Accepted"))
        self.assertEqual(self.section(text, "Behavior changes to confirm"), "- int4=Guid is now accepted")
        titles = [line for line in text.splitlines() if line.startswith("## ")]
        self.assertLess(titles.index("## Escalated"), titles.index("## Behavior changes to confirm"))
        self.assertLess(titles.index("## Deferred"), titles.index("## Accepted"))
        self.assertEqual(titles.index("## Not checked"), titles.index("## Intent check") + 1)

    def test_not_checked_section_from_latest_deep(self):
        self.begin()
        self.record(output(findings=[], not_checked=[{"what": "probe A", "reason": "no docker"}]))
        self.assertEqual(self.section(self.summary_text(), "Not checked"), "- probe A: no docker")

    def test_unmentioned_from_latest_deep_round_on(self):
        def check(text):
            return output(findings=[], intent_check={"unmet_claims": [], "unmentioned_behavior": [text]})

        self.begin()
        self.record(check("old"))
        self.begin("followup")
        self.record(check("mid"))
        self.begin("deep")
        self.record(check("new"))
        self.begin("followup")
        self.record(check("late"))
        text = self.section(self.summary_text(), "Intent check")
        self.assertIn("- new", text)
        self.assertIn("- late", text)
        self.assertNotIn("- old", text)
        self.assertNotIn("- mid", text)

    # review-notes.md
    def round_with(self, mode, model, *extra):
        (self.repo / f"{mode}-{model}-{len(extra)}.txt").write_text("x\n")
        self.cli("begin-round", "--mode", mode, "--model", model, "--base-ref", "main", *extra)
        self.record(output(findings=[]))

    def test_review_notes_generated_line(self):
        self.round_with("deep", "opus")
        self.round_with("followup", "sonnet")
        self.round_with("deep", "fable", "--focus", "second-opinion")
        lines = self.cli("summary")["review_notes"].splitlines()
        self.assertEqual(lines[0], "## Review notes")
        self.assertEqual(
            lines[2],
            "3 rounds (2 deep with opus and fable, 1 followup with sonnet); second opinion: yes; "
            "merge risk low (round 3, the latest deep round).",
        )

    def test_review_notes_one_round(self):
        self.begin()
        self.record(output(findings=[]))
        line = self.cli("summary")["review_notes"].splitlines()[2]
        self.assertEqual(
            line, "1 round (1 deep with m); second opinion: no; merge risk low (round 1, the latest deep round)."
        )

    def test_review_notes_collapses_empty(self):
        self.begin()
        self.record(output(findings=[finding()]))
        self.set_status("R1-F1", "fixed", "--commit", "c1")
        self.begin("followup")
        self.record(output(findings=[], verdicts=VERIFY_R1_F1))
        notes = self.cli("summary")["review_notes"]
        self.assertNotIn("### Escalated", notes)
        self.assertEqual(self.section(notes, "Found and fixed"), "- R1-F1 (minor) Handle the empty case.")
        self.assertEqual(
            notes.strip().splitlines()[-1],
            "Nothing to report under: Escalated, Behavior changes to confirm, Deferred, Accepted, Invalid, "
            "Outside the change, Intent check, Not checked.",
        )

    def test_review_notes_lists_non_empty_sections(self):
        self.begin()
        self.record(output(findings=[finding()], not_checked=[{"what": "probe A", "reason": "no docker"}]))
        self.set_status("R1-F1", "escalated", "--note", "decide")
        notes = self.cli("summary")["review_notes"]
        self.assertIn("R1-F1", self.section(notes, "Escalated"))
        self.assertEqual(self.section(notes, "Not checked"), "- probe A: no docker")
        self.assertNotIn("Escalated,", notes.strip().splitlines()[-1])

    def test_summary_command_returns_review_notes(self):
        self.begin()
        self.record(output(findings=[]))
        result = self.cli("summary")
        self.assertEqual(Path(result["review_notes_path"]).read_text(), result["review_notes"])
        self.assertEqual(Path(result["review_notes_path"]).name, "review-notes.md")


if __name__ == "__main__":
    unittest.main()
