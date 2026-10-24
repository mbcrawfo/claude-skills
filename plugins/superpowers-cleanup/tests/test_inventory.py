from pathlib import Path
import tempfile
import unittest

from helpers import PLANS, RESEARCH, SPECS, by_path, docs_inventory, make_repo, run, write

SPEC_A = f"{SPECS}/2026-10-22-a-design.md"


class InventoryFilesTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name))

    def inventory(self, *extra):
        return run("inventory", "--repo", str(self.repo), *extra)

    def test_pairs_plan_with_spec_from_header(self):
        write(self.repo, SPEC_A, "# A design\n")
        write(self.repo, f"{PLANS}/2026-10-22-b.md", f"# B plan\n\n**Spec:** `{SPEC_A}`\n")
        plan = by_path(self.inventory(), f"{PLANS}/2026-10-22-b.md")
        self.assertEqual(plan["kind"], "plan")
        self.assertEqual(plan["spec"], SPEC_A)
        self.assertTrue(plan["spec_exists"])

    def test_spec_link_markdown_and_relative_forms(self):
        write(self.repo, SPEC_A, "# A design\n")
        write(self.repo, f"{PLANS}/2026-10-22-md.md", "**Spec:** [design](../specs/2026-10-22-a-design.md)\n")
        write(self.repo, f"{PLANS}/2026-10-22-dot.md", f"**Spec:** ./{SPEC_A}\n")
        out = self.inventory()
        for name in ("2026-10-22-md.md", "2026-10-22-dot.md"):
            plan = by_path(out, f"{PLANS}/{name}")
            self.assertEqual(plan["spec"], SPEC_A, name)
            self.assertTrue(plan["spec_exists"], name)

    def test_missing_or_broken_spec_line(self):
        write(self.repo, f"{PLANS}/2026-10-22-none.md", "# No spec line\n")
        write(self.repo, f"{PLANS}/2026-10-22-gone.md", f"**Spec:** `{SPECS}/gone.md`\n")
        out = self.inventory()
        none = by_path(out, f"{PLANS}/2026-10-22-none.md")
        self.assertIsNone(none["spec"])
        self.assertFalse(none["spec_exists"])
        gone = by_path(out, f"{PLANS}/2026-10-22-gone.md")
        self.assertEqual(gone["spec"], f"{SPECS}/gone.md")
        self.assertFalse(gone["spec_exists"])

    def test_filename_dates(self):
        self.assertEqual(docs_inventory.filename_date("2026-10-22-a.md"), "2026-10-22")
        self.assertIsNone(docs_inventory.filename_date("notes.md"))
        self.assertIsNone(docs_inventory.filename_date("2026-13-45-x.md"))

    def test_filename_date_in_output(self):
        write(self.repo, SPEC_A, "# A\n")
        write(self.repo, f"{SPECS}/notes.md", "# Notes\n")
        write(self.repo, f"{SPECS}/2026-13-45-x.md", "# X\n")
        out = self.inventory()
        self.assertEqual(by_path(out, SPEC_A)["filename_date"], "2026-10-22")
        self.assertIsNone(by_path(out, f"{SPECS}/notes.md")["filename_date"])
        self.assertIsNone(by_path(out, f"{SPECS}/2026-13-45-x.md")["filename_date"])

    def test_research_citations_by_path_and_name(self):
        write(self.repo, f"{RESEARCH}/2020-01-10-limits/report.md", "# Limits\n")
        write(self.repo, f"{RESEARCH}/2020-01-10-limits-v2/report.md", "# Limits v2\n")
        write(self.repo, f"{SPECS}/2020-01-15-a.md", f"See {RESEARCH}/2020-01-10-limits/report.md.\n")
        write(self.repo, f"{SPECS}/2020-01-15-b.md", "Based on 2020-01-10-limits-v2.\n")
        out = self.inventory()
        self.assertEqual(by_path(out, f"{SPECS}/2020-01-15-a.md")["research"], [f"{RESEARCH}/2020-01-10-limits"])
        self.assertEqual(by_path(out, f"{SPECS}/2020-01-15-b.md")["research"], [f"{RESEARCH}/2020-01-10-limits-v2"])

    def test_research_file_entry_cited_by_full_name(self):
        write(self.repo, f"{RESEARCH}/2020-01-10-notes.md", "# Notes\n")
        write(self.repo, f"{SPECS}/2020-01-15-a.md", "Read 2020-01-10-notes.md first.\n")
        write(self.repo, f"{SPECS}/2020-01-15-b.md", "Read 2020-01-10-notes first.\n")
        out = self.inventory()
        self.assertEqual(by_path(out, f"{SPECS}/2020-01-15-a.md")["research"], [f"{RESEARCH}/2020-01-10-notes.md"])
        self.assertEqual(by_path(out, f"{SPECS}/2020-01-15-b.md")["research"], [])

    def test_titles_and_research_folder_title(self):
        write(self.repo, f"{SPECS}/2020-01-15-retry.md", "Intro line\n\n# Retry design\n\n## Later\n")
        write(self.repo, f"{RESEARCH}/2020-01-10-limits/README.md", "# Limits\n")
        write(self.repo, f"{RESEARCH}/2020-01-11-raw/data.md", "# Data\n")
        out = self.inventory()
        self.assertEqual(by_path(out, f"{SPECS}/2020-01-15-retry.md")["title"], "Retry design")
        limits = by_path(out, f"{RESEARCH}/2020-01-10-limits")
        self.assertEqual(limits["kind"], "research")
        self.assertEqual(limits["title"], "Limits")
        self.assertIsNone(by_path(out, f"{RESEARCH}/2020-01-11-raw")["title"])

    def test_kinds_entries_and_sorting(self):
        write(self.repo, f"{SPECS}/x.md", "# X\n")
        write(self.repo, f"{SPECS}/sub/deep.md", "# Deep\n")
        write(self.repo, f"{SPECS}/image.png", "png")
        write(self.repo, f"{RESEARCH}/.DS_Store", "junk")
        write(self.repo, f"{RESEARCH}/file.md", "# File\n")
        write(self.repo, f"{PLANS}/2020-01-01-p.md", "# P\n")
        out = self.inventory()
        paths = [f["path"] for f in out["files"]]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(set(paths), {f"{SPECS}/x.md", f"{RESEARCH}/file.md", f"{PLANS}/2020-01-01-p.md"})
        self.assertEqual(by_path(out, f"{RESEARCH}/file.md")["kind"], "research")

    def test_folder_overrides_and_missing_dirs(self):
        write(self.repo, "design/specs/2020-01-15-a.md", "# A\n")
        write(self.repo, "design/plans/2020-01-16-a.md", "**Spec:** `design/specs/2020-01-15-a.md`\n")
        out = self.inventory("--specs", "design/specs", "--plans", "design/plans")
        self.assertEqual(out["missing_dirs"], [RESEARCH])
        self.assertEqual(out["dirs"], {"specs": "design/specs", "plans": "design/plans", "research": RESEARCH})
        plan = by_path(out, "design/plans/2020-01-16-a.md")
        self.assertEqual(plan["spec"], "design/specs/2020-01-15-a.md")
        self.assertTrue(plan["spec_exists"])

    def test_unreadable_file_reported(self):
        write(self.repo, f"{SPECS}/2026-10-22-bad.md", b"\xff\xfe\x00")
        out = self.inventory()
        self.assertEqual(out["unreadable"][0]["path"], f"{SPECS}/2026-10-22-bad.md")
        self.assertTrue(out["unreadable"][0]["reason"])
        self.assertNotIn(f"{SPECS}/2026-10-22-bad.md", [f["path"] for f in out["files"]])


if __name__ == "__main__":
    unittest.main()
