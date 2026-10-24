from pathlib import Path
import tempfile
import unittest

from helpers import PLANS, RESEARCH, SPECS, call, commit_all, docs_inventory, make_repo, run, write

SPEC = f"{SPECS}/2020-01-15-retry-design.md"
FOLDER = f"{RESEARCH}/2020-01-10-limits"


class RefsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = make_repo(Path(self._tmp.name))
        write(self.repo, SPEC, "# Retry design\n")

    def refs(self, *targets):
        return run("refs", "--repo", str(self.repo), *targets)

    def matches(self, out, target=SPEC):
        (entry,) = [t for t in out["targets"] if t["target"] == target]
        return entry["matches"]

    def test_patterns(self):
        self.assertEqual(
            docs_inventory.ref_patterns(SPEC),
            {"path": SPEC, "filename": "2020-01-15-retry-design.md", "stem": "retry-design"},
        )
        self.assertEqual(
            docs_inventory.ref_patterns("docs/notes.md"),
            {"path": "docs/notes.md", "filename": "notes.md", "stem": "notes"},
        )
        self.assertEqual(
            docs_inventory.ref_patterns(FOLDER), {"path": FOLDER, "filename": "2020-01-10-limits", "stem": "limits"}
        )
        self.assertEqual(
            docs_inventory.ref_patterns("docs/2020-01-10-x"),
            {"path": "docs/2020-01-10-x", "filename": "2020-01-10-x", "stem": "x"},
        )
        self.assertEqual(docs_inventory.ref_patterns("docs/plain"), {"path": "docs/plain", "filename": "plain"})

    def test_match_kinds(self):
        write(self.repo, "src/a.py", f"# see {SPEC}\n")
        write(self.repo, "src/b.py", "x = 1\n# see 2020-01-15-retry-design.md\n")
        write(self.repo, "docs/x.md", "[spec](../specs/retry-design)\n")
        got = {(m["file"], m["line"], m["match"]) for m in self.matches(self.refs(SPEC)) if not m["in_target"]}
        self.assertEqual(got, {("src/a.py", 1, "path"), ("src/b.py", 2, "filename"), ("docs/x.md", 1, "stem")})

    def test_one_match_per_line_with_strongest_kind(self):
        write(self.repo, "src/a.py", f"# see {SPEC} (retry-design)\n")
        found = [m for m in self.matches(self.refs(SPEC)) if m["file"] == "src/a.py"]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["match"], "path")
        self.assertEqual(found[0]["text"], f"# see {SPEC} (retry-design)")

    def test_in_target_for_files_and_folders(self):
        write(self.repo, SPEC, f"# Retry design\nThis is {SPEC}.\n")
        write(self.repo, f"{PLANS}/2020-01-16-retry.md", f"**Spec:** `{SPEC}`\n")
        write(self.repo, f"{FOLDER}/report.md", f"Feeds {SPEC}\n")
        out = self.refs(SPEC, FOLDER)
        flags = {m["file"]: m["in_target"] for m in self.matches(out)}
        self.assertEqual(flags[f"{PLANS}/2020-01-16-retry.md"], False)
        self.assertEqual(flags[f"{FOLDER}/report.md"], True)
        self.assertEqual(flags[SPEC], True)

    def test_absolute_target_reported_relative(self):
        out = self.refs(str(self.repo / SPEC))
        self.assertEqual(out["targets"][0]["target"], SPEC)

    def test_untracked_searched_and_ignored_skipped(self):
        write(self.repo, ".gitignore", "build/\n")
        commit_all(self.repo, "ignore build")
        write(self.repo, "build/out.txt", f"{SPEC}\n")
        write(self.repo, "notes.md", f"{SPEC}\n")
        files = {m["file"] for m in self.matches(self.refs(SPEC))}
        self.assertIn("notes.md", files)
        self.assertNotIn("build/out.txt", files)

    def test_text_cut_and_order(self):
        write(self.repo, "z.md", f"{SPEC}\n")
        write(self.repo, "a.md", "pad\n" + f"{SPEC} " + "x" * 400 + "\n" + f"{SPEC}\n")
        found = [(m["file"], m["line"]) for m in self.matches(self.refs(SPEC)) if not m["in_target"]]
        self.assertEqual(found, [("a.md", 2), ("a.md", 3), ("z.md", 1)])
        long = [m for m in self.matches(self.refs(SPEC)) if m["file"] == "a.md" and m["line"] == 2][0]
        self.assertEqual(len(long["text"]), 300)

    def test_missing_target_is_an_error(self):
        code, out, err = call("refs", "--repo", str(self.repo), f"{SPECS}/nope.md")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        self.assertEqual(err.strip(), f"error: no such file: {SPECS}/nope.md")

    def test_no_matches(self):
        write(self.repo, f"{SPECS}/2020-02-02-lonely.md", "# Lonely\n")
        out = self.refs(f"{SPECS}/2020-02-02-lonely.md")
        self.assertEqual(out, {"targets": [{"target": f"{SPECS}/2020-02-02-lonely.md", "matches": []}]})


if __name__ == "__main__":
    unittest.main()
