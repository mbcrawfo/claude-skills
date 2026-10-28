# agents-md-cleanup size limits and doc push-out Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach `maintenance:agents-md-cleanup` to measure rules and files, split multi-rule bullets, push documentation out of AGENTS.md into the repo's docs, offer and record size limits, and propose a repo-native test that enforces them.

**Architecture:** A new stdlib module `agents_md_limits.py` owns the definitions of a rule, a word, a normalised byte and the limits line, and `agents_md_inventory.py` imports it to report sizes, docs and references in `inventory` and to validate the new `split` and doc `move` verdicts in `check-verdicts`. The reviewer agent gains a `placement` criterion, one-rule-per-bullet under `brief`, and the limits in `POLICY`. SKILL.md adds the size question to step 2, destination reconciliation, reference re-pointing and the over-limit backstop to step 4, new approval sections and the test question to step 6, and doc writes plus the test run to step 7.

**Tech Stack:** Python 3.9+ stdlib (`argparse`, `json`, `re`, `subprocess`), `unittest`, Markdown skill and agent files, `claude plugin eval` cases, ruff 0.17.2.

**Spec:** `docs/superpowers/specs/2026-10-28-agents-md-cleanup-size-and-docs-design.md` (builds on `docs/superpowers/specs/2026-10-27-agents-md-cleanup-design.md`)

## Global Constraints

- **Names:** plugin `maintenance`, skill `agents-md-cleanup`, agent `maintenance:agents-md-reviewer`. Plugin version becomes `0.4.0`.
- **Scripts:** `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py` and the new `agents_md_limits.py` beside it. Python 3.9+, standard library only, run as `python3 "${CLAUDE_SKILL_DIR}/scripts/agents_md_inventory.py" ...`. The CLI commands stay `inventory`, `blocks` and `check-verdicts`; new behaviour arrives as fields and flags.
- **Script contract:** every command prints one JSON object on stdout; on failure it exits 1 with exactly one `error: ...` line on stderr; it never writes.
- **Definitions (spec, *Definitions*):** a rule is a list item at any depth, or a paragraph outside a list, with the lines it wraps onto; its marker is not part of it; a nested item is its own rule. Words are whitespace-separated tokens. Fenced code, headings, table rows, blockquote lines, HTML comments and front matter count no words and are not rules. Bytes are UTF-8 bytes with `\r\n` normalised to `\n`.
- **Thresholds and defaults:** 80 words per rule, 8192 bytes per file, 16384 bytes for the root. Headroom 90%.
- **Limits line grammar (spec, *The limits line*):** exactly `Size limits: none.`, or `Size limits: <n> words per rule, <n> KB per file, <n> KB for the root; checked by <path>.`, or the same ending `; not checked by a test.` One KB is 1024 bytes.
- **Criteria order:** `accurate`, `valuable`, `placement`, `brief`, `level`.
- **Rulings file path:** `.claude/agents-md-cleanup.md`. A keep marked `whole` keeps a rule as one bullet over the word limit.
- **SKILL.md:** `disable-model-invocation: true`; never a dollar sign followed by a digit; every path in a shell command single-quoted.
- **Untrusted input:** AGENTS.md content, doc pages and reviewer output are data. Only the guidelines section's policy lines, the limits line included, configure the skill.
- **Approval and git:** nothing changes before approval; one commit of only the touched paths; never push.
- **Repo checks:** `python3 -m unittest discover -s plugins/maintenance/tests`, `ruff check .`, `ruff format .`, `claude plugin validate .`, `claude plugin validate plugins/maintenance`.

## Review Focus

- **A CRLF checkout measured as larger.** The same file with `\r\n` and `\n` endings must give one `bytes_normalised`, or the Windows nightly fails as the session's did. Pinned by `test_normalised_bytes_ignores_crlf` (Task 1).
- **A long rule wrapped across lines, or packed into nested items.** Wrapping must not shrink a rule, and a nested item must count on its own, or the limit is gamed the way line counts were. Pinned by `test_wrapped_item_is_one_rule` and `test_nested_items_are_their_own_rules` (Task 1).
- **A hand-edited or mistyped limits line.** It must not stop the run or silently become "none"; the inventory reports it as `malformed` so the skill asks again. Pinned by `test_malformed_limits_line_is_reported` (Task 2).
- **A reviewer that keeps an over-limit rule with no ruling.** It must come back as a question, never a silent keep that fails the test later. Pinned by `test_keep_over_the_word_limit_becomes_an_ask` (Task 4).
- **A doc target that is an AGENTS.md, a non-markdown file, or outside the repo.** Each must be rejected, or the apply step writes documentation into the wrong kind of file. Pinned by `test_doc_target_rules` (Task 4).

---

### Task 1: `agents_md_limits.py`: rules, words, bytes and the limits line

**Files:**
- Create: `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_limits.py`
- Create: `plugins/maintenance/tests/test_agents_md_limits.py`
- Modify: `pyproject.toml`: add `"agents_md_limits"` to `known-first-party`.
- Modify: `plugins/maintenance/tests/agents_helpers.py`: import and re-export `agents_md_limits` beside `agents_md_inventory` (same `sys.path` insertion; add it to `__all__`).

**Interfaces:**
- Produces, in `agents_md_limits.py`:
  - `DEFAULT_RULE_WORDS = 80`, `DEFAULT_FILE_BYTES = 8192`, `DEFAULT_ROOT_BYTES = 16384`, `HEADROOM = 0.9`.
  - `count_words(text: str) -> int`: `len(text.split())`.
  - `normalised_bytes(raw: bytes) -> int`: length after replacing `b"\r\n"` with `b"\n"`.
  - `measure_rules(text: str) -> list[dict]`: every rule as `{"line": int, "end_line": int, "words": int, "first_words": str}`, 1-based inclusive lines, `first_words` the rule's first eight words joined by spaces. Line-based scanner, independent of `split_blocks` so the repo's test can mirror it: normalise `\r\n`; skip front matter (`---` first line through the next `---`); a line whose text after any leading whitespace starts with three or more backticks or tildes opens a fence, closed by a line of the same character at least as long, and nothing inside counts; skip blank lines, ATX headings, lines starting with `|` or `>` after whitespace, thematic breaks and HTML comment lines (from a line containing `<!--` through the line containing `-->`); a line matching `_LIST_MARKER` after any indent starts a rule whose text is the line after the marker; any other non-blank line continues the open rule when the previous line belonged to it, else starts a paragraph rule; a blank line or a skipped line closes the open rule.
  - `parse_limits_line(text: str) -> dict | None`: scans `text` (the guidelines section) for a line matching the grammar, with an optional list marker and ignoring case and surrounding whitespace. Returns `{"none": True}` for `Size limits: none.`; otherwise `{"rule_words": int, "file_bytes": int, "root_bytes": int, "test": str | None}` with KB multiplied by 1024, `test` the path after `checked by` (backticks stripped) or `None`. A line that starts with `Size limits:` (after an optional marker) but matches neither shape raises `ValueError` whose message is the line from `Size limits:` on, stripped. No such line returns `None`.
  - `format_limits_line(rule_words: int, file_bytes: int, root_bytes: int, test: str | None) -> str`: the exact line, KB as `bytes // 1024`, test path in backticks.
  - `limits_in_force(parsed: dict | None, overrides: dict) -> dict`: `{"source": "guidelines" | "arguments" | "default" | "none", "rule_words", "file_bytes", "root_bytes", "test"}`. `overrides` holds any of `rule_words`, `file_bytes`, `root_bytes`, `none: True`; an override wins with source `arguments`; a parsed line gives `guidelines`; `{"none": True}` from either gives source `none` with the default numbers (so counts can still be measured); nothing gives `default`.
  - `over_limits(rules: list[dict], size: int, is_root: bool, limits: dict) -> dict`: `{"rules": [rule dicts with words > rule_words], "file": bool, "limit": int, "headroom": bool}` where `limit` is `root_bytes` or `file_bytes`, `file` is `size > limit`, `headroom` is `size > HEADROOM * limit`. When `limits["source"] == "none"`, `rules` and `file` are empty and false, `headroom` false.

- [ ] **Step 1: Write the failing tests**

```python
# plugins/maintenance/tests/test_agents_md_limits.py
import unittest

from agents_helpers import agents_md_limits as lim


class RuleScannerTest(unittest.TestCase):
    def rules(self, text):
        return [(r["line"], r["words"]) for r in lim.measure_rules(text)]

    def test_wrapped_item_is_one_rule(self):
        self.assertEqual(self.rules("- one two\n  three four\n"), [(1, 4)])

    def test_nested_items_are_their_own_rules(self):
        self.assertEqual(self.rules("- a b\n  - c\n    - d e f\n"), [(1, 2), (2, 1), (3, 3)])

    def test_marker_is_not_a_word(self):
        self.assertEqual(self.rules("1. one\n10) two three\n* x\n"), [(1, 1), (2, 2), (3, 1)])

    def test_paragraph_outside_a_list_is_a_rule(self):
        self.assertEqual(self.rules("Intro line one\nline two\n\n- item\n"), [(1, 5), (4, 1)])

    def test_blank_line_ends_a_rule(self):
        self.assertEqual(self.rules("- one\n\n  two three\n"), [(1, 1), (3, 2)])

    def test_fences_headings_tables_quotes_comments_count_nothing(self):
        text = "# H\n\n- one\n\n```\nnot counted\n```\n\n| a | b |\n|---|---|\n| c | d |\n\n> quoted\n\n<!-- a\ncomment -->\n\n~~~md\n- not a rule\n~~~\n"
        self.assertEqual(self.rules(text), [(3, 1)])

    def test_fence_inside_an_item_counts_nothing(self):
        self.assertEqual(self.rules("- one\n\n  ```\n  x y z\n  ```\n"), [(1, 1)])

    def test_front_matter_is_skipped(self):
        self.assertEqual(self.rules("---\ntitle: x\n---\n\n- one\n"), [(5, 1)])

    def test_first_words_and_end_line(self):
        (rule,) = lim.measure_rules("- a b c d e f g h i j\n  k\n")
        self.assertEqual(rule["first_words"], "a b c d e f g h")
        self.assertEqual(rule["end_line"], 2)

    def test_crlf_text_measures_like_lf(self):
        self.assertEqual(self.rules("- one two\r\n  three\r\n"), [(1, 3)])


class BytesTest(unittest.TestCase):
    def test_normalised_bytes_ignores_crlf(self):
        self.assertEqual(lim.normalised_bytes(b"- a\r\n- b\r\n"), lim.normalised_bytes(b"- a\n- b\n"))
        self.assertEqual(lim.normalised_bytes("- é\n".encode()), 5)


class LimitsLineTest(unittest.TestCase):
    def test_none(self):
        self.assertEqual(lim.parse_limits_line("- Push-down: aggressive.\n- Size limits: none.\n"), {"none": True})

    def test_checked_by_a_test(self):
        got = lim.parse_limits_line("13. Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; checked by `tests/docs/test_agents_md.py`.")
        self.assertEqual(got, {"rule_words": 80, "file_bytes": 8192, "root_bytes": 16384, "test": "tests/docs/test_agents_md.py"})

    def test_not_checked(self):
        got = lim.parse_limits_line("Size limits: 100 words per rule, 10 KB per file, 20 KB for the root; not checked by a test.")
        self.assertEqual(got["test"], None)
        self.assertEqual((got["rule_words"], got["file_bytes"], got["root_bytes"]), (100, 10240, 20480))

    def test_absent(self):
        self.assertIsNone(lim.parse_limits_line("- Push-down: aggressive.\n"))

    def test_malformed_raises(self):
        with self.assertRaises(ValueError):
            lim.parse_limits_line("- Size limits: 80 words, 8 KB.\n")

    def test_format_round_trips(self):
        line = lim.format_limits_line(80, 8192, 16384, "t/test_a.py")
        self.assertEqual(line, "Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; checked by `t/test_a.py`.")
        self.assertEqual(lim.parse_limits_line(line)["test"], "t/test_a.py")
        self.assertEqual(lim.format_limits_line(80, 8192, 16384, None).split("; ")[1], "not checked by a test.")


class InForceTest(unittest.TestCase):
    def test_sources(self):
        self.assertEqual(lim.limits_in_force(None, {})["source"], "default")
        self.assertEqual(lim.limits_in_force({"none": True}, {})["source"], "none")
        self.assertEqual(lim.limits_in_force(None, {"none": True})["source"], "none")
        parsed = {"rule_words": 100, "file_bytes": 10240, "root_bytes": 20480, "test": None}
        self.assertEqual(lim.limits_in_force(parsed, {})["rule_words"], 100)
        self.assertEqual(lim.limits_in_force(parsed, {})["source"], "guidelines")
        got = lim.limits_in_force(parsed, {"rule_words": 60})
        self.assertEqual((got["source"], got["rule_words"], got["file_bytes"]), ("arguments", 60, 10240))

    def test_over_limits(self):
        limits = lim.limits_in_force(None, {})
        rules = [{"line": 1, "end_line": 1, "words": 81, "first_words": "x"}, {"line": 2, "end_line": 2, "words": 80, "first_words": "y"}]
        got = lim.over_limits(rules, 8193, False, limits)
        self.assertEqual([r["line"] for r in got["rules"]], [1])
        self.assertEqual((got["file"], got["limit"], got["headroom"]), (True, 8192, True))
        self.assertEqual(lim.over_limits(rules, 7000, False, limits)["headroom"], False)
        self.assertEqual(lim.over_limits(rules, 16384, True, limits)["file"], False)
        none = lim.over_limits(rules, 99999, False, lim.limits_in_force({"none": True}, {}))
        self.assertEqual((none["rules"], none["file"], none["headroom"]), ([], False, False))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest plugins.maintenance.tests.test_agents_md_limits 2>&1 | tail -3` from the repo root, or `cd plugins/maintenance/tests && python3 -m unittest test_agents_md_limits -v`
Expected: FAIL with `ImportError` (cannot import `agents_md_limits`).

- [ ] **Step 3: Implement `agents_md_limits.py` with the interfaces above**

The scanner is one pass over lines with `fence`, `comment` and `open_rule` state. Reuse the regexes from `agents_md_inventory.py` by copying them (the limits module must not import the inventory module, which will import it).

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_limits.py' -v`
Expected: all PASS.

- [ ] **Step 5: Lint, format and commit**

```bash
ruff check . && ruff format .
git add plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_limits.py plugins/maintenance/tests/test_agents_md_limits.py plugins/maintenance/tests/agents_helpers.py pyproject.toml
git commit -m "feat(maintenance): define AGENTS.md rules, bytes and the size limits line"
```

---

### Task 2: Sizes and limits in `inventory`

**Files:**
- Modify: `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py` (`_file_entry`, `cmd_inventory`, `_parser`; module docstring)
- Test: `plugins/maintenance/tests/test_agents_md_inventory.py` (new class `SizeTest(RepoTestCase)`)

**Interfaces:**
- Consumes: Task 1's `measure_rules`, `normalised_bytes`, `parse_limits_line`, `limits_in_force`, `over_limits`.
- Produces, in `inventory` output:
  - each file entry gains `bytes_normalised: int`, `rules: list[dict]` (from `measure_rules`), `over: dict` (from `over_limits`, with `is_root` true for an entry whose `_dir_of(path) == "."`).
  - `limits`: the `limits_in_force` dict, plus `malformed: str | null` holding a `Size limits:` line that failed to parse (then `source` is `default`).
  - `totals` gains `rules`, `rules_over` (sum of `len(over["rules"])`), `files_over` (count of `over["file"]`).
  - CLI flags on `inventory`: `--rule-words N`, `--file-bytes N`, `--root-bytes N` (positive ints) and `--no-size-limits`; they fill `overrides`. `--no-size-limits` with a number is a usage error.
- Produces, as a helper later tasks use: `_file_entry(..., limits: dict)` carries the limits; `_entries(root, all_paths, scopes, limits)` passes them on.

- [ ] **Step 1: Write the failing tests**

```python
class SizeTest(RepoTestCase):
    LONG = "- " + " ".join(["w"] * 81) + "\n"

    def test_file_entry_measures_rules_and_bytes(self):
        write(self.repo, "AGENTS.md", "- a b\n  - c\n\n```\nx\n```\n" + self.LONG)
        commit_all(self.repo, "add")
        out = self.inventory()
        (f,) = out["files"]
        self.assertEqual([(r["line"], r["words"]) for r in f["rules"]], [(1, 2), (2, 1), (7, 81)])
        self.assertEqual(f["bytes_normalised"], f["bytes"])
        self.assertEqual([r["line"] for r in f["over"]["rules"]], [7])
        self.assertEqual(f["over"]["file"], False)
        self.assertEqual(out["totals"]["rules"], 3)
        self.assertEqual(out["totals"]["rules_over"], 1)
        self.assertEqual(out["totals"]["files_over"], 0)
        self.assertEqual(out["limits"], {"source": "default", "rule_words": 80, "file_bytes": 8192, "root_bytes": 16384, "test": None, "malformed": None})

    def test_crlf_file_normalised(self):
        write(self.repo, "AGENTS.md", b"- a\r\n- b\r\n")
        commit_all(self.repo, "add")
        (f,) = self.inventory()["files"]
        self.assertEqual((f["bytes"], f["bytes_normalised"]), (10, 8))

    def test_root_gets_the_root_limit(self):
        write(self.repo, "AGENTS.md", "- a\n" * 2300)  # 9200 bytes
        write(self.repo, "src/AGENTS.md", "- a\n" * 2300)
        commit_all(self.repo, "add")
        out = self.inventory()
        root, src = out["files"]
        self.assertEqual((root["over"]["file"], root["over"]["limit"]), (False, 16384))
        self.assertEqual((src["over"]["file"], src["over"]["limit"]), (True, 8192))
        self.assertEqual(out["totals"]["files_over"], 1)

    def test_limits_from_the_guidelines_line(self):
        write(self.repo, "AGENTS.md", "## AGENTS.md guidelines\n\n- Size limits: 100 words per rule, 1 KB per file, 2 KB for the root; not checked by a test.\n" + "- a\n" * 600)
        commit_all(self.repo, "add")
        out = self.inventory()
        self.assertEqual(out["limits"]["source"], "guidelines")
        self.assertEqual(out["limits"]["root_bytes"], 2048)
        self.assertTrue(out["files"][0]["over"]["file"])

    def test_limits_none_measures_but_marks_nothing(self):
        write(self.repo, "AGENTS.md", "## AGENTS.md guidelines\n\n- Size limits: none.\n\n" + self.LONG)
        commit_all(self.repo, "add")
        out = self.inventory()
        self.assertEqual(out["limits"]["source"], "none")
        self.assertEqual(out["files"][0]["rules"][-1]["words"], 81)
        self.assertEqual(out["totals"]["rules_over"], 0)

    def test_malformed_limits_line_is_reported(self):
        write(self.repo, "AGENTS.md", "## AGENTS.md guidelines\n\n- Size limits: 80 words.\n")
        commit_all(self.repo, "add")
        out = self.inventory()
        self.assertEqual(out["limits"]["source"], "default")
        self.assertEqual(out["limits"]["malformed"], "Size limits: 80 words.")

    def test_argument_overrides(self):
        write(self.repo, "AGENTS.md", "## AGENTS.md guidelines\n\n- Size limits: none.\n\n" + self.LONG)
        commit_all(self.repo, "add")
        out = self.inventory("--rule-words", "60")
        self.assertEqual((out["limits"]["source"], out["limits"]["rule_words"]), ("arguments", 60))
        self.assertEqual(out["totals"]["rules_over"], 1)
        self.assertEqual(self.inventory("--no-size-limits")["limits"]["source"], "none")
        code, _, err = call("inventory", "--repo", str(self.repo), "--no-size-limits", "--file-bytes", "10")
        self.assertEqual(code, 1)
        self.assertIn("error: ", err)
        code, _, err = call("inventory", "--repo", str(self.repo), "--rule-words", "0")
        self.assertEqual(code, 1)
```

Add `call` to the test module's imports from `agents_helpers`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_inventory.py' -k SizeTest -v`
Expected: FAIL with `KeyError: 'rules'` and the like.

- [ ] **Step 3: Implement the fields and flags**

`cmd_inventory` builds `overrides` from the flags, reads the guidelines text through `_guidelines`, calls `parse_limits_line` in a `try` that records `malformed`, then `limits_in_force`, and passes `limits` into `_entries` and `_file_entry`. Positive-int flags use an `argparse` type that raises for `< 1`. Update the module docstring's `inventory` line.

- [ ] **Step 4: Run the whole suite to verify it passes**

Run: `python3 -m unittest discover -s plugins/maintenance/tests`
Expected: OK (existing inventory tests still pass with the new fields present).

- [ ] **Step 5: Lint, format and commit**

```bash
ruff check . && ruff format .
git add plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py plugins/maintenance/tests/test_agents_md_inventory.py
git commit -m "feat(maintenance): inventory measures AGENTS.md rules and files against size limits"
```

---

### Task 3: `docs` and `references` in `inventory`

**Files:**
- Modify: `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py` (new `_docs`, `_references`; `cmd_inventory`, `_file_entry`)
- Test: `plugins/maintenance/tests/test_agents_md_inventory.py` (new class `DocsAndReferencesTest(RepoTestCase)`)

**Interfaces:**
- Produces, in `inventory` output:
  - `docs`: `{"folder": str | null, "pages": [{"path": str, "headings": [str]}], "readmes": [str]}`. `folder` is the first of `docs`, `doc`, `documentation` that exists as a directory at the root, matched case-insensitively and spelled as on disk. `pages` are the `.md` files under it (tracked, or untracked and not ignored, found the way `discover` finds AGENTS.md files), sorted, capped at `DOCS_PAGES_SHOWN = 200`, each with the titles of its headings from `_parse`. `readmes` are the `README.md` files (any case) in each `agents_md_dirs` directory, sorted.
  - each file entry gains `references`: `{"hits": [{"path": str, "line": int, "text": str}], "truncated": bool}`. Hits come from `git grep -n -I -F` with one `-e` per path in `[path, *aliases]`, over the whole repo, dropping hits in files whose basename is `agents.md` in any case. `text` is the matched line, stripped, cut to 200 characters. Capped at `REFERENCES_SHOWN = 50` with `truncated` true when more were found.

- [ ] **Step 1: Write the failing tests**

```python
class DocsAndReferencesTest(RepoTestCase):
    def test_docs_folder_pages_and_readmes(self):
        write(self.repo, "AGENTS.md", "- a\n")
        write(self.repo, "src/AGENTS.md", "- b\n")
        write(self.repo, "src/README.md", "# src\n")
        write(self.repo, "docs/architecture.md", "# Architecture\n\n## Retry\n\ntext\n\nSetext\n------\n")
        write(self.repo, "docs/notes.txt", "not a page\n")
        commit_all(self.repo, "add")
        write(self.repo, "docs/untracked.md", "# New\n")
        docs = self.inventory()["docs"]
        self.assertEqual(docs["folder"], "docs")
        self.assertEqual([p["path"] for p in docs["pages"]], ["docs/architecture.md", "docs/untracked.md"])
        self.assertEqual(docs["pages"][0]["headings"], ["Architecture", "Retry", "Setext"])
        self.assertEqual(docs["readmes"], ["README.md", "src/README.md"])

    def test_docs_folder_preference_and_absence(self):
        write(self.repo, "AGENTS.md", "- a\n")
        write(self.repo, "documentation/x.md", "# x\n")
        write(self.repo, "Doc/y.md", "# y\n")
        commit_all(self.repo, "add")
        self.assertEqual(self.inventory()["docs"]["folder"], "Doc")
        (self.repo / "Doc" / "y.md").unlink()
        (self.repo / "Doc").rmdir()
        self.assertEqual(self.inventory()["docs"]["folder"], "documentation")
        write(self.repo, "docs/.keep", "")
        self.assertEqual(self.inventory()["docs"]["folder"], "docs")

    def test_no_docs_folder(self):
        write(self.repo, "AGENTS.md", "- a\n")
        commit_all(self.repo, "add")
        self.assertEqual(self.inventory()["docs"], {"folder": None, "pages": [], "readmes": ["README.md"]})

    def test_references_find_mentions_outside_agents_files(self):
        write(self.repo, "AGENTS.md", "- a\n")
        write(self.repo, "src/AGENTS.md", "- b\n")
        write(self.repo, "src/x.py", "# see src/AGENTS.md for the rule\n")
        write(self.repo, "docs/td.md", "`src/AGENTS.md` says so\n")
        write(self.repo, "nested/AGENTS.md", "- src/AGENTS.md is elsewhere\n")
        commit_all(self.repo, "add")
        out = self.inventory()
        src = by_path(out, "src/AGENTS.md")
        self.assertEqual([(h["path"], h["line"]) for h in src["references"]["hits"]], [("docs/td.md", 1), ("src/x.py", 1)])
        self.assertEqual(src["references"]["hits"][1]["text"], "# see src/AGENTS.md for the rule")
        self.assertFalse(src["references"]["truncated"])

    def test_references_are_capped_and_flagged(self):
        write(self.repo, "AGENTS.md", "- a\n")
        write(self.repo, "notes.txt", "AGENTS.md\n" * 60)
        commit_all(self.repo, "add")
        refs = by_path(self.inventory(), "AGENTS.md")["references"]
        self.assertEqual(len(refs["hits"]), 50)
        self.assertTrue(refs["truncated"])

    def test_references_include_aliases(self):
        write(self.repo, "AGENTS.md", "- a\n")
        (self.repo / "docs").mkdir()
        (self.repo / "docs" / "AGENTS.md").symlink_to("../AGENTS.md")
        write(self.repo, "notes.txt", "read docs/AGENTS.md\n")
        commit_all(self.repo, "add")
        entry = by_path(self.inventory(), "AGENTS.md")
        self.assertEqual(entry["aliases"], ["docs/AGENTS.md"])
        self.assertIn(("notes.txt", 1), [(h["path"], h["line"]) for h in entry["references"]["hits"]])
```

Import `by_path` from `helpers` in the test module if it is not already imported.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_inventory.py' -k DocsAndReferencesTest -v`
Expected: FAIL with `KeyError: 'docs'`.

- [ ] **Step 3: Implement `_docs(root, all_paths) -> dict` and `_references(root, paths: list[str]) -> dict`**

`_references` runs `_git(root, "grep", "-n", "-I", "-F", *("-e", p for p in paths), "--", ".", check=False)` with `paths = [entry["path"], *entry["aliases"]]`; exit 1 means no hits. Parse `path:line:text`. Skip `.git` and AGENTS.md basenames. Walk for `.md` pages with the same tracked-or-unignored rule `discover` uses, under the docs folder only.

- [ ] **Step 4: Run the suite to verify it passes**

Run: `python3 -m unittest discover -s plugins/maintenance/tests`
Expected: OK.

- [ ] **Step 5: Lint, format and commit**

```bash
ruff check . && ruff format .
git add plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py plugins/maintenance/tests/test_agents_md_inventory.py
git commit -m "feat(maintenance): inventory lists doc pages and references to each AGENTS.md"
```

---

### Task 4: `split`, doc moves and the word-limit guard in `check-verdicts`

**Files:**
- Modify: `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py` (`ACTIONS`, `CRITERIA`, `TARGET_KINDS`, `ACTION_KEYS`, `_check_move`, `_check_verdict`, `_check_ancestor`, `validate_verdicts`, `cmd_check_verdicts`, `_parser`)
- Test: `plugins/maintenance/tests/test_agents_md_verdicts.py` (new class `SplitAndDocTest(VerdictsTestCase)`; update `test_every_action_is_accepted`, `test_every_field_an_action_takes_is_accepted`, `test_fields_an_action_does_not_take_are_rejected` so the enumerations cover the new action and fields)

**Interfaces:**
- Produces, in the verdict schema:
  - `ACTIONS` gains `split`; `CRITERIA` gains `placement` (order: `accurate`, `valuable`, `placement`, `brief`, `level`); `TARGET_KINDS` gains `doc`.
  - `move` takes `pointer` (string) only with `target_kind: doc`; a doc `move` requires `new_text`; a `doc` target must end in `.md` (any case), must not be named `AGENTS.md`, must pass `_can_hold_file` and `_check_stageable`; the other `_check_move` rules apply.
  - `split` takes `parts`: a list of at least two objects, each `{"text": str}` plus optional `target`, `target_kind`, `target_heading`, `pointer`; a part with any target field needs `target` and `target_kind` and is checked by `_check_move` on `{"target", "target_kind", "target_heading"}`; `pointer` only on a doc part; no other keys. `split` takes no `new_text`.
  - `ask.proposed` may be a `split` or a doc `move`, checked the same way.
  - `check-verdicts` takes `--rule-words N`. With it: every rule in any `new_text`, part `text` or `pointer` (measured with `measure_rules`) over `N` words is rejected with `error: verdict <n>: <field> holds a rule of <w> words, over the limit of <N>`; a `keep` on a block holding a rule over `N` words with no `ruling` becomes `{"action": "ask", "criterion": "brief", "evidence": ["<file>:<line>: <w> words"], "question": OVER_LIMIT_QUESTION, "recommendation": "Split it into one rule per bullet.", "proposed": ...}` with no `proposed` (the user or reviewer supplies the split), where `OVER_LIMIT_QUESTION = "Keep this rule whole, over the word limit, with an entry in the test's exception list? Or split it?"`.
  - Ancestor role allows `move` and `ask` only, as today; `split` is rejected there.

- [ ] **Step 1: Write the failing tests**

```python
class SplitAndDocTest(VerdictsTestCase):
    def setUp(self):
        super().setUp()
        write(self.repo, "docs/architecture.md", "# Arch\n\n## Retry\n")
        commit_all(self.repo, "docs")

    def doc_move(self, **extra):
        return {"blocks": ["b1"], "action": "move", "criterion": "placement", "evidence": ["AGENTS.md:1 explains"],
                "target": "docs/architecture.md", "target_kind": "doc", "target_heading": "Retry",
                "new_text": "The retry loop backs off twice.", **extra}

    def test_doc_move_with_pointer_is_accepted(self):
        code, out, err = self.check(self.keep_rest(self.doc_move(pointer="- Retry behaviour: see `docs/architecture.md#retry`.")))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["counts"], {"move": 1, "keep": 3})

    def test_doc_move_to_a_new_page_is_accepted(self):
        v = self.doc_move(target="docs/branding.md")
        del v["target_heading"]
        self.assertEqual(self.check(self.keep_rest(v))[0], 0)

    def test_doc_move_requires_new_text(self):
        v = self.doc_move()
        del v["new_text"]
        self.assertRejected(self.check(self.keep_rest(v)), "new_text")

    def test_pointer_only_on_doc_moves(self):
        v = {**self.doc_move(pointer="- see docs"), "target": "src/AGENTS.md", "target_kind": "agents_md"}
        self.assertRejected(self.check(self.keep_rest(v)), "pointer")

    def test_doc_target_rules(self):
        for target, expected in (
            ("docs/AGENTS.md", "AGENTS.md"),
            ("src/a.py", ".md"),
            ("../out.md", ".."),
            ("README.md/x.md", "not a file path"),
        ):
            self.assertRejected(self.check(self.keep_rest(self.doc_move(target=target))), expected)

    def test_placement_is_a_criterion(self):
        v = {"blocks": ["b1"], "action": "delete", "criterion": "placement", "evidence": ["docs/architecture.md:3 already says it"]}
        self.assertEqual(self.check(self.keep_rest(v))[0], 0)

    def split(self, parts):
        return {"blocks": ["b1"], "action": "split", "criterion": "brief", "evidence": ["AGENTS.md:1 holds two rules"], "parts": parts}

    def test_split_is_accepted_with_staying_and_leaving_parts(self):
        v = self.split([
            {"text": "- First rule."},
            {"text": "- Second rule: see `docs/architecture.md#retry`.", "target": "docs/architecture.md", "target_kind": "doc",
             "target_heading": "Retry", "pointer": "- Retry: see `docs/architecture.md#retry`."},
            {"text": "Keep MAX at 3.", "target": "src/a.py", "target_kind": "comment"},
        ])
        code, out, err = self.check(self.keep_rest(v))
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["counts"]["split"], 1)

    def test_split_needs_two_parts_with_text(self):
        self.assertRejected(self.check(self.keep_rest(self.split([{"text": "- one"}]))), "parts")
        self.assertRejected(self.check(self.keep_rest(self.split([{"text": "- one"}, {"text": ""}]))), "text")
        self.assertRejected(self.check(self.keep_rest(self.split([{"text": "- one"}, {"text": "- two", "extra": 1}]))), "extra")

    def test_split_part_targets_are_checked_like_moves(self):
        bad = self.split([{"text": "- one"}, {"text": "- two", "target": "docs/AGENTS.md", "target_kind": "doc"}])
        self.assertRejected(self.check(self.keep_rest(bad)), "AGENTS.md")
        missing = self.split([{"text": "- one"}, {"text": "- two", "target": "docs/architecture.md"}])
        self.assertRejected(self.check(self.keep_rest(missing)), "target_kind")
        pointer = self.split([{"text": "- one"}, {"text": "- two", "target": "src/a.py", "target_kind": "comment", "pointer": "x"}])
        self.assertRejected(self.check(self.keep_rest(pointer)), "pointer")

    def test_split_takes_no_new_text(self):
        self.assertRejected(self.check(self.keep_rest({**self.split([{"text": "- a"}, {"text": "- b"}]), "new_text": "x"})), "new_text")

    def test_split_is_not_allowed_on_an_ancestor(self):
        write(self.repo, "src/AGENTS.md", "- s\n")
        commit_all(self.repo, "src")
        self.assertRejected(self.check([self.split([{"text": "- a"}, {"text": "- b"}])], role="ancestor", scope=["src"]), "not allowed")

    def test_proposed_may_be_a_split_or_a_doc_move(self):
        ask = {"blocks": ["b1"], "action": "ask", "criterion": "brief", "evidence": ["e"], "question": "q", "recommendation": "r",
               "proposed": {"action": "split", "parts": [{"text": "- a"}, {"text": "- b"}]}}
        self.assertEqual(self.check(self.keep_rest(ask))[0], 0)
        ask["proposed"] = {"action": "move", "target": "docs/architecture.md", "target_kind": "doc", "new_text": "t"}
        self.assertEqual(self.check(self.keep_rest(ask))[0], 0)


class WordLimitGuardTest(VerdictsTestCase):
    LONG = "- " + " ".join(["w"] * 81)

    def check_limited(self, verdicts, **kw):
        path = self.tmp / "verdicts.txt"
        path.write_text("Report\n```json\n" + json.dumps({"file": "AGENTS.md", "verdicts": verdicts}) + "\n```\n", encoding="utf-8")
        return call("check-verdicts", "--repo", str(self.repo), "--file", "AGENTS.md", "--role", "full", "--input", str(path), "--rule-words", "80")

    def test_keep_over_the_word_limit_becomes_an_ask(self):
        write(self.repo, "AGENTS.md", self.LONG + "\n\n- two\n\n- three\n\n- four\n")
        commit_all(self.repo, "long")
        code, out, err = self.check_limited(self.keep_rest())
        self.assertEqual(code, 0, err)
        first = json.loads(out)["verdicts"][0]
        self.assertEqual((first["action"], first["criterion"]), ("ask", "brief"))
        self.assertIn("81 words", first["evidence"][0])
        self.assertIn("exception list", first["question"])
        self.assertNotIn("proposed", first)

    def test_keep_with_a_ruling_stays_a_keep(self):
        write(self.repo, "AGENTS.md", self.LONG + "\n\n- two\n\n- three\n\n- four\n")
        commit_all(self.repo, "long")
        verdicts = self.keep_rest({"blocks": ["b1"], "action": "keep", "ruling": "whole: the security rule"})
        self.assertEqual(json.loads(self.check_limited(verdicts)[1])["verdicts"][0]["action"], "keep")

    def test_new_text_over_the_limit_is_rejected(self):
        long = "- " + " ".join(["x"] * 81)
        trim = {"blocks": ["b1"], "action": "trim", "criterion": "brief", "evidence": ["e"], "new_text": long}
        self.assertRejected(self.check_limited(self.keep_rest(trim)), "81 words, over the limit of 80")
        split = {"blocks": ["b1"], "action": "split", "criterion": "brief", "evidence": ["e"], "parts": [{"text": "- a"}, {"text": long}]}
        self.assertRejected(self.check_limited(self.keep_rest(split)), "parts")
        exact = {"blocks": ["b1"], "action": "trim", "criterion": "brief", "evidence": ["e"], "new_text": "- " + " ".join(["x"] * 80)}
        self.assertEqual(self.check_limited(self.keep_rest(exact))[0], 0)

    def test_without_the_flag_nothing_is_guarded(self):
        write(self.repo, "AGENTS.md", self.LONG + "\n\n- two\n\n- three\n\n- four\n")
        commit_all(self.repo, "long")
        self.assertEqual(json.loads(self.check(self.keep_rest())[1])["verdicts"][0]["action"], "keep")
```

Also extend the existing enumeration tests: `test_every_action_is_accepted` adds a valid `split`; the field tables in `test_every_field_an_action_takes_is_accepted` and `test_fields_an_action_does_not_take_are_rejected` add `pointer` (move) and `parts` (split).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_verdicts.py' -k 'SplitAndDocTest or WordLimitGuardTest' -v`
Expected: FAIL with `unknown action 'split'` and `target_kind must be one of agents_md, comment`.

- [ ] **Step 3: Implement the schema changes**

Add `_check_part(part, file, root)` and `_check_doc_target(root, target)`; extend `_check_move` for `doc` and `pointer`; add `split` to `ACTION_KEYS` as `("parts",)` and `pointer` to `move`'s keys; add `_guard_length(verdict, blocks, file, rule_words)` applied in `validate_verdicts` after `_guard_choice`, and `_check_text_limits(verdict, rule_words)` in `_check_verdict` when `rule_words` is set (thread it through `validate_verdicts(..., rule_words: int | None = None)`). Report `counts` as today. Update the module docstring's `check-verdicts` line.

- [ ] **Step 4: Run the suite to verify it passes**

Run: `python3 -m unittest discover -s plugins/maintenance/tests`
Expected: OK.

- [ ] **Step 5: Lint, format and commit**

```bash
ruff check . && ruff format .
git add plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py plugins/maintenance/tests/test_agents_md_verdicts.py
git commit -m "feat(maintenance): check-verdicts accepts split and doc moves and guards the word limit"
```

---

### Task 5: Reviewer agent: placement, one rule per bullet, limits

**Files:**
- Modify: `plugins/maintenance/agents/agents-md-reviewer.md` (Inputs, Judging a block, Rulings, Ancestor role, Evidence, Output table, Example)
- Test: `plugins/maintenance/tests/test_agents_md_skill.py` (`ReviewerAgentTest`: extend `test_names_every_action_and_criterion`, `test_names_every_input`, `test_names_every_policy_answer`, `test_example_json_matches_schema`, `test_schema_table_lists_the_fields_the_validator_allows`, `test_example_passes_the_validator`; add the tests below)

**Interfaces:**
- Consumes: Task 4's schema (`split` with `parts`, doc `move` with `pointer`, `placement`).
- Produces: the agent text the skill's dispatch template (Task 6) must match: new inputs `DOCS` (the inventory's `docs` object as JSON), `BYTES` (the file's `bytes_normalised`) and `OVER` (the file's `over` object as JSON); `POLICY` gains `size limits: <n> words per rule, <n> KB per file (root <n> KB)` or `size limits: none`.

Content to write, each as its own section or paragraph:

- **Inputs:** the three new lines and the `POLICY` extension.
- **Judging a block:** the order `accurate`, `valuable`, `placement`, `brief`, `level`.
- **`### Placement: documentation, not instruction`**, after *Valuable*: the criterion and the examples from the spec word for word ("How a reader treats a missing key, the seven steps of a describe, the brand colour of a primary button: documentation. 'Use `uv`, never `pip`', 'run `scripts/test.sh` before a PR', 'never put user text into a statement the tool sends': rules, kept."); the four-step destination search (a page the statement links to; a `DOCS` page whose headings cover the topic; the README beside the code; else a new file under `DOCS.folder`, or `docs/<name>.md` when `folder` is null, with the name's reason in the evidence); read the candidate first, and a statement the page already states is the duplicate handling (`delete`, or `trim` to a pointer when it directs the agent), never a second copy; the verdict shape (`move`, `target_kind: doc`, `target`, `target_heading`, `new_text` the doc text, `pointer` the one-line rule that stays, absent for pure description); the clause accounting rule: evidence lists every clause of the original and where it went, or `dropped: <path:line> already states it`.
- **Brief gains `split`:** a block holding several independent statements becomes a `split` with `parts`, one rule each, in order, each part staying or leaving (`agents_md`, `comment`, `doc`); explanation beyond one sentence of reason inside a rule is a doc part; the same clause accounting; two statements needing different actions among keep, move and doc move are a `split`, anything else still `ask`.
- **Limits:** with limits in `POLICY`, every rule under the word limit and the file under 90% of its byte limit; the order of preference doc moves, comment moves, moves to nested AGENTS.md files (policy permitting), splits, trims; never drop a clause to fit; when it can't get under without dropping one, `ask` with the clause and the best proposal. Without limits, still split and push out; mention the size only when `OVER` is not empty.
- **Rulings:** a keep whose quote is marked `whole` is kept as one bullet over the limit, `ruling` set.
- **Ancestor role:** unchanged: `move` and `ask` only; no placement or split verdicts.
- **Output table:** rows for `parts`, `pointer`, `target_kind` with `doc`, `criterion` with `placement`.
- **Example:** add to the example file a 90-word bullet "The retry loop..." holding a rule plus explanation, with its `split` verdict (one staying part, one doc part with `pointer`, clause accounting in `evidence`), and `DOCS` in the example inputs. The example JSON must pass `check-verdicts` against the example file (the existing `test_example_passes_the_validator` builds the fixture from the agent's markdown fence; extend its fixture to include `docs/architecture.md`).

- [ ] **Step 1: Write the failing tests**

```python
# in ReviewerAgentTest
def test_placement_criterion_and_order(self):
    text = AGENT.read_text(encoding="utf-8")
    order = [text.index(h) for h in ("### Accurate", "### Valuable", "### Placement", "### Brief", "### Level")]
    self.assertEqual(order, sorted(order))
    placement = text[text.index("### Placement") : text.index("### Brief")]
    for phrase in ("describes the project rather than directs the agent", "only for some tasks", "`target_kind: doc`", "`pointer`",
                   "never a second copy", "`docs/<name>.md`", "every clause"):
        self.assertIn(phrase, placement)

def test_split_and_limits_are_described(self):
    text = AGENT.read_text(encoding="utf-8")
    brief = text[text.index("### Brief") : text.index("### Level")]
    for phrase in ("`split`", "`parts`", "one rule each"):
        self.assertIn(phrase, brief)
    for phrase in ("size limits:", "90%", "never drop a clause", "`whole`", "`BYTES`", "`OVER`", "`DOCS`"):
        self.assertIn(phrase, text)
```

Extend the enumerations: `test_names_every_action_and_criterion` adds `` `split` `` and `` `placement` ``; `test_names_every_input` adds `DOCS`, `BYTES`, `OVER`; `test_schema_table_lists_the_fields_the_validator_allows` reads `ACTION_KEYS`, so it picks up `parts` and `pointer` on its own. `test_example_json_matches_schema` requires the example to use every action, so the example gains a `split`; make its `target_kind` check also walk `parts`. `test_example_passes_the_validator` writes a placeholder for each comment target; make it also create a `# Placeholder\n` page for each doc target in a verdict or a part, so `docs/architecture.md` exists in the fixture.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_skill.py' -k ReviewerAgentTest -v`
Expected: FAIL on the new phrases and the schema table.

- [ ] **Step 3: Edit `agents-md-reviewer.md` as listed above**

Keep every existing rule; add, don't rewrite. Keep the agent's frontmatter unchanged.

- [ ] **Step 4: Run the suite to verify it passes**

Run: `python3 -m unittest discover -s plugins/maintenance/tests`
Expected: OK, `test_example_passes_the_validator` included.

- [ ] **Step 5: Commit**

```bash
git add plugins/maintenance/agents/agents-md-reviewer.md plugins/maintenance/tests/test_agents_md_skill.py
git commit -m "feat(maintenance): reviewer judges placement, splits bullets and works to size limits"
```

---

### Task 6: SKILL.md: size question, destinations, references, backstop, test

**Files:**
- Modify: `plugins/maintenance/skills/agents-md-cleanup/SKILL.md` (frontmatter `argument-hint`; *The script*; steps 1 to 9)
- Test: `plugins/maintenance/tests/test_agents_md_skill.py` (`SkillMdTest`: extend `test_script_calls` to accept the new flags; add the tests below)

**Interfaces:**
- Consumes: Task 2's `limits`, `rules`, `over`, `totals.rules_over`, `totals.files_over`, flags `--rule-words`, `--file-bytes`, `--root-bytes`, `--no-size-limits`; Task 3's `docs` and `references`; Task 4's `--rule-words` on `check-verdicts`, `split`, doc `move`, `pointer`, `OVER_LIMIT_QUESTION`; Task 5's `DOCS`, `BYTES`, `OVER` inputs and `POLICY` form.

Content to write:

- **Frontmatter:** `argument-hint` adds size words: `"[directories] [plain-words options, e.g. 4 at a time, 100 words, 10 KB, no size limits]"`.
- **The script:** the `inventory` line gains `[--rule-words N] [--file-bytes N] [--root-bytes N] [--no-size-limits]`; `check-verdicts` gains `[--rule-words N]`.
- **Step 1:** the options also include size overrides ("100 words", "10 KB", "20 KB root", "no size limits") mapped to the flags; state the limits in force with the counts; a `limits.malformed` line is reported and the run is a first size run.
- **Step 2:** the skip condition adds "and no size question is due". The size question is due on a first size run (`limits.source` is `default`) with `rules_over` or `files_over` not zero: show the counts, the five worst rules (`file`, `first_words`, `words`) and each file over with `bytes_normalised` and its limit, then "Adopt size limits? Recommended: 80 words per rule, 8 KB per file, 16 KB for the root. Change the numbers, or answer none." Recommend adopting. With a limits line, no question; one line with the counts over the limits in force, or over the defaults when `source` is `none`. Nothing over on a first size run: no question, no line, no limits line written.
- **Step 3:** `POLICY` gains the size limits; the template gains `DOCS:`, `BYTES:` and `OVER:` lines; `check-verdicts` gets `--rule-words` when `limits.source` is not `none`; the re-emit message covers the new error.
- **Step 4:** three new items, each its own bullet: **Reconcile destinations.** (group doc moves and doc parts by `target`; two new files for one topic become one question offering one file; existing pages go through `blocks` for `sha256` and `resolved`); **Re-point references.** (for each rule that leaves a file, read `references.hits`, and run `git grep -n -F -e '<name>'` for each backticked name in the rule over files that also mention `AGENTS.md`; a hit that names the rule gets a bulk row; an unjudgeable one a question; a `truncated` root list is a seed only); **Size accounting.** (with limits, re-measure each edited file's text with the definitions in *The script*; a target over its byte limit gets one re-dispatch of its reviewer on the merged text with `BYTES`, `OVER` and the list of incoming rules, at most once per file; still over: a question offering a file-level exception with its reason or leaving those moves out; over headroom: a line in the message).
- **Step 5:** splits whose parts stay or go to existing files are bulk; a split or doc move that creates a new file follows the new-file policy; redirects of a destination are recomputed and shown again.
- **Step 6:** new numbered sections after the bulk table: **Documentation moves** (grouped by destination, new files marked with the name's reason, each row the doc text and the pointer that stays), **Splits** (before → after with the clause accounting), **Size** (per file over the thresholds: before → after bytes, longest rule before → after), **The limits line** (exact new text when set or changed, and the three shapes written out: `Size limits: none.`, `Size limits: <n> words per rule, <n> KB per file, <n> KB for the root; checked by <path>.` and the same ending `; not checked by a test.`), **The test question** (the spec's wording: proposed path, framework, modelled on an existing doc test, same definitions, CRLF normalised, boundary cases at each limit, exception list of rules kept whole and files accepted over; a script check when there is no suite; or none; recommended yes; answer recorded as `checked by <path>` or `not checked by a test`), **Test maintenance** (rows for the test's constants or exception list when this run changes them; the test file goes through `blocks`).
- **Step 7:** doc text under the approved heading at the end of that section, or a new file with `# <title>`; create the docs folder when the message said so; pointer lines replace the moved statement; `split` writes its staying parts in place of the block, in order, and sends each leaving part to its target like a move; after the AGENTS.md edits, write the approved test and run it alone with the repo's filter idiom (`dotnet test --filter`, a `pytest` path, a `jest` path, `go test -run`), never the whole suite; three outcomes word for word from the spec (passes → commit; runs and fails → stop, nothing committed, the test and the script disagree; can't run → commit the approved edits, write the limits line as approved, leave the test file uncommitted and say so).
- **Step 8:** the doc files, reference updates and the test join the commit; a test that could not be run stays out.
- **Step 9:** also one line with the size counts when something is over.

- [ ] **Step 1: Write the failing tests**

```python
# in SkillMdTest
def _section(self, start, end=None):
    text = SKILL.read_text(encoding="utf-8")
    return text[text.index(start) : text.index(end)] if end else text[text.index(start) :]

def test_size_question_in_confirm_step(self):
    confirm = self._section("## 2. Confirm before dispatch", "## 3. Dispatch")
    for phrase in ("no size question is due", "`limits.source`", "`rules_over`", "`files_over`", "Adopt size limits?",
                   "80 words per rule, 8 KB per file, 16 KB for the root", "answer none"):
        self.assertIn(phrase, confirm)

def test_limits_line_grammar_in_skill(self):
    text = SKILL.read_text(encoding="utf-8")
    for line in ("Size limits: none.", "; checked by ", "; not checked by a test."):
        self.assertIn(line, text)

def test_dispatch_carries_docs_bytes_over_and_limits(self):
    dispatch = self._section("## 3. Dispatch", "## 4. Merge")
    for phrase in ("DOCS:", "BYTES:", "OVER:", "size limits:", "--rule-words"):
        self.assertIn(phrase, dispatch)

def test_merge_reconciles_repoints_and_backstops(self):
    merge = self._section("## 4. Merge", "## 5. Sort")
    for bullet in ("- **Reconcile destinations.**", "- **Re-point references.**", "- **Size accounting.**"):
        self.assertIn(bullet, merge)
    (size,) = [line for line in merge.splitlines() if line.startswith("- **Size accounting.**")]
    for phrase in ("at most once per file", "file-level", "exception list", "leaving those moves out"):
        self.assertIn(phrase, size)
    (refs,) = [line for line in merge.splitlines() if line.startswith("- **Re-point references.**")]
    for phrase in ("`references`", "backticked", "`truncated`"):
        self.assertIn(phrase, refs)

def test_approval_message_sections(self):
    approval = self._section("## 6. Approval message", "## 7. Apply")
    for phrase in ("**Documentation moves", "**Splits", "**Size", "**The limits line", "**The test question", "**Test maintenance",
                   "boundary cases", "exception list", "not checked by a test"):
        self.assertIn(phrase, approval)

def test_apply_writes_docs_and_runs_only_the_new_test(self):
    apply = self._section("## 7. Apply", "## 8. Commit")
    for phrase in ("`# <title>`", "`pointer`", "`parts`", "never the whole suite", "`dotnet test --filter`",
                   "nothing is committed", "uncommitted"):
        self.assertIn(phrase, apply)
    self.assertIn("could not be run", self._section("## 8. Commit"))

def test_script_calls_accept_size_flags(self):
    text = SKILL.read_text(encoding="utf-8")
    for flag in ("--rule-words", "--file-bytes", "--root-bytes", "--no-size-limits"):
        self.assertIn(flag, text[text.index("## The script") : text.index("## 1. Inventory")])
```

Extend `test_script_calls`: its command check already accepts only `inventory`, `blocks` and `check-verdicts`; no change unless it rejects flags.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_skill.py' -k SkillMdTest -v`
Expected: FAIL on each new phrase.

- [ ] **Step 3: Edit SKILL.md as listed above**

Write every path in a shell command single-quoted; never a dollar sign followed by a digit; keep the existing step numbering and bullet names the tests already pin (`- **Rulings in git.**`, `- **Other files.**`, `- **Dirty files.**`, `1. **Check for changes.**`).

- [ ] **Step 4: Run the suite to verify it passes**

Run: `python3 -m unittest discover -s plugins/maintenance/tests`
Expected: OK.

- [ ] **Step 5: Commit**

```bash
git add plugins/maintenance/skills/agents-md-cleanup/SKILL.md plugins/maintenance/tests/test_agents_md_skill.py
git commit -m "feat(maintenance): agents-md-cleanup asks about size limits, moves docs out and offers a test"
```

---

### Task 7: Eval cases for the size question and the split-and-doc review

**Files:**
- Create: `plugins/maintenance/evals/agents-md-cleanup-size-ask/{case.yaml,prompt.md,scaffold.sh,graders/skill-ran.md,graders/no-dispatch.md,graders/nothing-changed.md,graders/size-question.md}`
- Create: `plugins/maintenance/evals/agents-md-cleanup-size-review/{case.yaml,prompt.md,scaffold.sh,graders/skill-ran.md,graders/nothing-changed.md,graders/no-repo-write.md,graders/table.md}`
- Test: `plugins/maintenance/tests/test_agents_md_eval.py` (new constants `SIZE_ASK`, `SIZE_REVIEW`; new tests)

**Interfaces:**
- Consumes: the review case's `scaffold.sh` as the model (copy its git isolation, overwrite refusal and branch handling); the first-run case's `no-dispatch.md` and the review case's `no-repo-write.md`, `nothing-changed.md`, `skill-ran.md` graders, copied as they are.
- Produces: one shared fixture, built by both scaffolds (identical scripts): the review fixture's files plus
  - `docs/architecture.md` with `# Architecture`, `## Retry` (two sentences on the retry loop), `## Dates` (one sentence: `parse_date()` accepts ISO 8601 dates only);
  - in root `AGENTS.md`, under `## Code notes`, statement 7: one bullet of about 250 words holding four rules about `src/payments/retry.py` (keep `MAX_ATTEMPTS` at 3; log every retry through `log_retry`; never retry a `ValueError`; add a test in `tests/test_retry.py` for a new retry reason) interleaved with explanation of how the retry loop backs off and why the processor bans clients, and statement 8: `- The retry loop sleeps between attempts, doubling the wait each time.` which `docs/architecture.md#retry` already says word for word;
  - `src/payments/retry.py` gains `def log_retry(attempt): ...` and a loop that doubles a wait, and `tests/test_retry.py` exists with one test, so the four rules check out;
  - the guidelines section holds the three policy lines and no `Size limits:` line.
- `agents-md-cleanup-size-ask`: prompt `/maintenance:agents-md-cleanup 2 at a time`; `case.yaml` like the review case's. `size-question.md` (llm, last_message) passes only if the message states 1 rule over 80 words and 0 files over, names the retry bullet as the worst, asks whether to adopt size limits with 80 words, 8 KB and 16 KB recommended, offers changing the numbers or none, dispatches nothing and edits nothing.
- `agents-md-cleanup-size-review`: prompt `/maintenance:agents-md-cleanup 2 at a time, no size limits`. `table.md` (llm, last_message) passes only if: statement 7 is split into one rule per bullet (four rules, each naming its subject), its explanation goes to `docs/architecture.md` under `Retry` with a pointer line left behind; statement 8 is deleted as a duplicate of `docs/architecture.md`; the message proposes the line `Size limits: none.` for the guidelines section; no test is proposed; statements 1 to 6 keep the review case's expected verdicts; nothing is reported as applied.

- [ ] **Step 1: Write the failing fixture tests**

```python
SIZE_ASK = EVALS / "agents-md-cleanup-size-ask"
SIZE_REVIEW = EVALS / "agents-md-cleanup-size-review"

# in FixtureTest
def test_size_fixtures_are_identical_and_measure_one_long_rule(self):
    ask = self.scaffold_ok(SIZE_ASK, "ask")
    review = self.scaffold_ok(SIZE_REVIEW, "review")
    self.assertEqual((SIZE_ASK / "scaffold.sh").read_bytes(), (SIZE_REVIEW / "scaffold.sh").read_bytes())
    out = run("inventory", "--repo", str(ask))
    self.assertEqual(out["limits"]["source"], "default")
    self.assertEqual((out["totals"]["rules_over"], out["totals"]["files_over"]), (1, 0))
    root = next(f for f in out["files"] if f["path"] == "AGENTS.md")
    (long,) = root["over"]["rules"]
    self.assertGreater(long["words"], 200)
    self.assertIn("docs/architecture.md", [p["path"] for p in out["docs"]["pages"]])
    self.assertIn("Retry", next(p for p in out["docs"]["pages"] if p["path"] == "docs/architecture.md")["headings"])
    self.assertIn("doubling the wait each time", (ask / "docs" / "architecture.md").read_text())
    self.assertTrue((ask / "tests" / "test_retry.py").is_file())
    self.assertIn("def log_retry", (ask / "src" / "payments" / "retry.py").read_text())
    self.assertEqual(git(review, "status", "--porcelain"), "")

# in GraderTest
def test_size_case_files(self):
    for case, graders in ((SIZE_ASK, {"skill-ran", "no-dispatch", "nothing-changed", "size-question"}),
                          (SIZE_REVIEW, {"skill-ran", "nothing-changed", "no-repo-write", "table"})):
        self.assertEqual({p.stem for p in (case / "graders").iterdir()}, graders)
        self.assertIn("scaffold_script: scaffold.sh", (case / "case.yaml").read_text())
    self.assertIn("no size limits", (SIZE_REVIEW / "prompt.md").read_text())
    self.assertNotIn("size", (SIZE_ASK / "prompt.md").read_text().lower())
    for name in ("no-dispatch", "nothing-changed", "skill-ran"):
        self.assertEqual((SIZE_ASK / "graders" / f"{name}.md").read_bytes(), ((FIRST if name == "no-dispatch" else REVIEW) / "graders" / f"{name}.md").read_bytes())
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_eval.py' -v`
Expected: FAIL: the scaffolds don't exist.

- [ ] **Step 3: Write the scaffolds, prompts, case files and graders**

Start from `plugins/maintenance/evals/agents-md-cleanup-review/scaffold.sh`. Keep the planted statements and their comment block, add statements 7 and 8 and the new files, and update the header comment. The `table.md` grader restates the review grader's items 1 to 9 and adds items 10 to 13 for statements 7 and 8, the `Size limits: none.` line and no test offer.

- [ ] **Step 4: Run the fixture tests, then both eval cases**

Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_eval.py' -v`
Expected: OK.

Run (through a login shell so the token in `~/.zprofile` is present), once per case, for the two new cases and the two existing ones:

```bash
zsh -lc 'claude plugin eval plugins/maintenance --case agents-md-cleanup-size-ask --scaffold --allow-tools Bash Write Edit SendMessage ToolSearch Agent --ablation none --runs 1 --json "$TMPDIR/size-ask.json"'
```
Expected: every grader passes. A grader that fails on wording, not substance, is loosened; a grader that fails on substance means the skill or agent text needs the fix, in this task.

- [ ] **Step 5: Commit**

```bash
git add plugins/maintenance/evals/agents-md-cleanup-size-ask plugins/maintenance/evals/agents-md-cleanup-size-review plugins/maintenance/tests/test_agents_md_eval.py
git commit -m "test(maintenance): eval cases for the size question and the split-and-doc review"
```

---

### Task 8: README, manifests, tech debt and release checks

**Files:**
- Modify: `plugins/maintenance/.claude-plugin/plugin.json` (version `0.4.0`; description ends `... accurate, brief, at the right level and within size limits.`)
- Modify: `.claude-plugin/marketplace.json` (the `maintenance` description matches `plugin.json` exactly)
- Modify: `plugins/maintenance/README.md` (`agents-md-cleanup` section)
- Modify or create: `docs/tech-debt/README.md` and `docs/tech-debt/TD-0009-agents-md-cleanup-doc-destinations.md`
- Test: `plugins/maintenance/tests/test_agents_md_skill.py::test_manifest_descriptions_match` (already pins the match and the version format)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Update the README section**

Add bullets under *What it does*, in the section's existing style: **One rule per bullet** (a bullet holding several statements is split); **Documentation moves out** (what counts as documentation, the destination proposed from the repo's docs, a new file under the docs folder when nothing fits, a one-line pointer left behind, every destination approved); **Measures and offers limits** (words per rule and bytes per file, the warning thresholds 80 words, 8 KB and 16 KB for the root, the question on the first run that is over, the recorded `Size limits:` line in its three shapes, plain-words overrides); **A test only if you want one** (proposed in the repo's own test style, boundary cases, exception list, run alone before the commit, left uncommitted when it can't run). Add the limits line's three shapes as a fenced example.

- [ ] **Step 2: Bump the version and descriptions**

Edit `plugin.json` and `marketplace.json`. Run: `python3 -m unittest discover -s plugins/maintenance/tests -p 'test_agents_md_skill.py' -k test_manifest_descriptions_match`
Expected: OK.

- [ ] **Step 3: Record tech debt**

Create `TD-0009-agents-md-cleanup-doc-destinations.md` with the five sections the folder's AGENTS.md requires: the doc page choice rests on heading titles and the reviewer's reading, with no check that a chosen heading is the best one; `references` for the root file is a seed only; the test's "can't run" detection is per recognised framework. Add its row to `docs/tech-debt/README.md` and bump `Next id` to `TD-0010`. Update `TD-0008` only if one of its items changed.

- [ ] **Step 4: Run every check**

```bash
python3 -m unittest discover -s plugins/maintenance/tests
ruff check . && ruff format --check .
claude plugin validate . && claude plugin validate plugins/maintenance
```
Expected: all OK; `ruff format --check` reports nothing to change.

- [ ] **Step 5: Commit**

```bash
git add plugins/maintenance/.claude-plugin/plugin.json .claude-plugin/marketplace.json plugins/maintenance/README.md docs/tech-debt
git commit -m "chore(maintenance): release 0.4.0 with size limits and doc push-out"
```

---

## Self-review notes

- **Spec coverage:** definitions, limits line, script fields, `docs`, `references`, `check-verdicts` changes, reviewer criteria and shapes, limits in `POLICY`, `whole` rulings, each SKILL.md step, the test's shape and maintenance, the three test outcomes, eval cases, README, version, tech debt: each has a task. The headroom check on edited text (step 4, *Size accounting*) is done by the skill with the script's definitions; the plan gives no CLI command for it because `blocks` plus `measure_rules` on the edited text is enough, and `inventory` re-run after apply reports it anyway.
- **Type consistency:** `limits` keys (`source`, `rule_words`, `file_bytes`, `root_bytes`, `test`, `malformed`), `over` keys (`rules`, `file`, `limit`, `headroom`), `references` keys (`hits`, `truncated`), `docs` keys (`folder`, `pages`, `readmes`), verdict fields (`parts`, `pointer`, `target_kind: doc`, `placement`) are spelled the same in Tasks 1 to 7.
- **Proportion:** the plan carries tests and signatures; the only bodies given are the scanner's rules, which the signature does not determine.
