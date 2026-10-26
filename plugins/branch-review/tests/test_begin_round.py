"""begin-round's own snapshot, the per-round intent, and the stored notes and related branches."""

import unittest

from helpers import LedgerHarness, git, output


class BeginRoundTest(LedgerHarness, unittest.TestCase):
    def begin_plain(self, *extra, mode="deep"):
        return self.cli("begin-round", "--mode", mode, "--model", "m", "--base-ref", "main", *extra)

    def test_begin_round_without_snapshot_takes_one(self):
        (self.repo / "wip.txt").write_text("uncommitted\n")
        rnd = self.begin_plain()
        self.assertEqual(git(self.repo, "show", f"{rnd['snapshot']}:wip.txt"), "uncommitted")
        self.assertEqual(rnd["head"], git(self.repo, "rev-parse", "HEAD"))

    def test_explicit_snapshot_still_used(self):
        snap = self.cli("snapshot")["snapshot"]
        self.assertEqual(self.begin_plain("--snapshot", snap)["snapshot"], snap)

    def test_round_stores_intent(self):
        self.begin_plain("--intent-file", self.write("i1.md", "A"))
        self.record(output(findings=[]))
        (self.repo / "more.txt").write_text("x\n")
        self.begin_plain("--intent-file", self.write("i2.md", "B"), mode="followup")
        rounds = self.state()["rounds"]
        self.assertEqual(rounds[0]["intent"], "A")
        self.assertEqual(rounds[1]["intent"], "B")

    def test_round_without_intent_file_copies_stored(self):
        self.begin_plain("--intent-file", self.write("i1.md", "A"))
        self.record(output(findings=[]))
        (self.repo / "more.txt").write_text("x\n")
        self.begin_plain(mode="followup")
        self.assertEqual(self.state()["rounds"][1]["intent"], "A")

    def test_notes_and_related_branches_stored(self):
        rnd = self.begin_plain(
            "--notes-file", self.write("notes.md", "leads\n"), "--related-branch", "origin/x", "origin/y"
        )
        self.assertEqual(rnd["notes"], "leads")
        self.assertEqual(rnd["related_branches"], ["origin/x", "origin/y"])
        self.record(output(findings=[]))
        (self.repo / "more.txt").write_text("x\n")
        rnd = self.begin_plain(mode="followup")
        state = self.state()
        self.assertEqual(state["notes"], "leads")
        self.assertEqual(state["related_branches"], ["origin/x", "origin/y"])
        self.assertEqual(rnd["notes"], "leads")
        self.record(output(findings=[]))
        (self.repo / "more2.txt").write_text("x\n")
        rnd = self.begin_plain("--related-branch", ".", "--notes-file", self.write("empty.md", "  \n"), mode="followup")
        self.assertEqual(rnd["related_branches"], [])
        self.assertIsNone(rnd["notes"])


if __name__ == "__main__":
    unittest.main()
