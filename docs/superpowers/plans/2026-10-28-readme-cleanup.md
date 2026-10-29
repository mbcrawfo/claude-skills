# README Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every README short, user-facing and written in STE, and move internals, contributor notes and repo layout to where their readers look.

**Architecture:** Docs only. Each README is rewritten in place; content it no longer holds moves to `AGENTS.md`, a new `plugins/<name>/ARCHITECTURE.md`, or the script's own docstring, or is dropped as a changelog. A throwaway checker script (below) verifies sentence length, `-ing` forms, links and that no code block was lost.

**Tech Stack:** Markdown; Python 3.9+ standard library for the checker.

**Spec:** `docs/superpowers/specs/2026-10-28-readme-cleanup-design.md`

## Global Constraints

- READMEs in scope: `README.md`, `plugins/branch-review/README.md`, `plugins/implementation-manager/README.md`, `plugins/maintenance/README.md`, `docs/deferred/README.md`, `docs/tech-debt/README.md`. No other README, no `SKILL.md`, agent, eval, spec, plan, research or deferred/tech-debt item file changes.
- STE (READMEs only): procedural sentences of 20 words or fewer, descriptive sentences of 25 or fewer; active voice; instructions in the imperative, one per sentence; paragraphs of six sentences or fewer, one topic each; no "-ing" verb forms; keep articles; one term for one thing; use the STE approved word where known ("use", not "utilize"); technical names (PR, branch, commit, `gh`, option names) stay as they are.
- `ARCHITECTURE.md` files get a plain editor pass, not STE.
- No changelogs and no script internals in any README.
- Anti-goal: losing information. Every fact removed from a README moves to its new home, unless it is changelog material.
- No plugin version bumps.
- Code blocks (commands, syntax lines, file trees) are copied byte for byte; only prose is rewritten.
- `docs/deferred/README.md` and `docs/tech-debt/README.md` keep their table columns, value sets and `Next id:` line exactly; their folders' `AGENTS.md` files depend on them.

## Review Focus

- **A link into moved content breaks.** A README link or anchor (`../../docs/tech-debt/TD-0003-…`, "See 'For other plugins'") must resolve after the move. Pinned by `doccheck.py links` in every task.
- **A command loses or changes a flag during the rewrite.** Users copy these lines. Pinned by `doccheck.py blocks` in Tasks 2–4: every old fenced block appears verbatim in the new README or `ARCHITECTURE.md`.
- **A user-facing requirement drops out.** implementation-manager without the desktop app or `gh` fails at setup; the requirement must stay near the top of its README and in the root paragraph. Pinned by the grep step in Task 3 and Task 1.
- **The deferred/tech-debt table format drifts.** An agent following the folder `AGENTS.md` then edits the wrong line. Pinned by the `diff` step in Task 5.
- **What-it-authorizes text softens.** "Invoking it authorizes starting the child sessions and merging the run's PRs … It authorizes nothing else" is a consent boundary; the STE rewrite must keep both halves. Pinned by the grep step in Task 3.

## The checker

Save as `doccheck.py` outside the repo (for example in the session scratchpad); it is not committed. Every task runs it as `python3 <path>/doccheck.py …`.

```python
#!/usr/bin/env python3
"""Throwaway checks for the README cleanup: STE sentence rules, links, lost code blocks."""

import re
import sys
from pathlib import Path

FENCE = re.compile(r"^```.*?^```", re.S | re.M)
ING_OK = {
    "string",
    "thing",
    "nothing",
    "something",
    "everything",
    "anything",
    "during",
    "bring",
    "pending",
    "existing",
    "missing",
    "warning",
    "setting",
    "settings",
    "remaining",
    "following",
}


def prose(text):
    text = FENCE.sub("", text)
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith(("#", "|", ">"))]
    text = "\n".join(re.sub(r"^\s*(?:[-*]|\d+\.)\s+", "", ln) for ln in lines)
    text = re.sub(r"`[^`]*`", "X", text)
    return re.sub(r"\]\([^)]*\)", "]", text)


def sentences(paths):
    bad = 0
    for p in paths:
        for s in re.split(r"(?<=[.!?:;])\s+|\n", prose(Path(p).read_text())):
            n = len(s.split())
            ing = [w for w in re.findall(r"\b[a-z]+ing\b", s.lower()) if w not in ING_OK]
            if n > 25 or ing:
                bad += 1
                print(f"{p}: {n}w {ing or ''} :: {s[:100]}")
    return bad


def links(paths):
    bad = 0
    for p in map(Path, paths):
        for target in re.findall(r"\]\(([^)\s]+)\)", FENCE.sub("", p.read_text())):
            if re.match(r"[a-z]+:", target):
                continue
            file, _, anchor = target.partition("#")
            dest = (p.parent / file).resolve() if file else p
            ok = dest.exists()
            if ok and anchor and dest.suffix == ".md":
                heads = {
                    re.sub(r"[^\w\- ]", "", h).strip().lower().replace(" ", "-")
                    for h in re.findall(r"^#+\s+(.*)$", dest.read_text(), re.M)
                }
                ok = anchor in heads
            if not ok:
                bad += 1
                print(f"{p}: broken link {target}")
    return bad


def blocks(old, news):
    have = "".join(Path(n).read_text() for n in news)
    lost = [b for b in FENCE.findall(Path(old).read_text()) if b.split("\n", 1)[1] not in have]
    for b in lost:
        print(f"lost block from {old}:\n{b}\n")
    return len(lost)


cmd, *args = sys.argv[1:]
n = {"sentences": lambda: sentences(args), "links": lambda: links(args), "blocks": lambda: blocks(args[0], args[1:])}[
    cmd
]()
print(f"{cmd}: {n} problem(s)")
sys.exit(1 if n else 0)
```

`sentences` flags descriptive sentences over 25 words and `-ing` words not on the allow list; procedural sentences (20-word limit) and passive voice are checked by reading. A flagged `-ing` word that is a noun or adjective in STE (for example a technical name) may stay; say so in the task report. `blocks` takes the old README as a file, so export it first: `git show HEAD:<path> > <scratch>/old-<name>.md`, taken before the task's first edit.

---

### Task 1: AGENTS.md documentation rules and the root README

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`

- [ ] **Step 1: Export the old README.** `git show HEAD:README.md > <scratch>/old-root.md`

- [ ] **Step 2: Edit `AGENTS.md`.**
  - Replace line 3's "See README.md for layout." with the repo layout block from the old README's Layout section (fenced block byte for byte), extended with one line: `plugins/<plugin-name>/ARCHITECTURE.md  # internals, tests and evals (contributors)`. Keep the sentence after it about `commands/`, `agents/` and `hooks/`.
  - In "Plugins and skills", after the `claude plugin validate` rule, add: "To try a plugin end to end, add your local checkout as a marketplace: `/plugin marketplace add ./path/to/claude-skills`."
  - Drop "Adding a plugin": the existing "Plugins and skills" rules already say it.
  - Add this section after "Agent instructions":

```markdown
## Documentation

- READMEs are for people who install and use the plugins. Write them in Simplified Technical English (ASD-STE100):
  - Keep procedural sentences to 20 words or fewer and descriptive sentences to 25 or fewer.
  - Use active voice. Write instructions in the imperative, one per sentence.
  - Keep paragraphs to six sentences or fewer, one topic each.
  - Do not use "-ing" verb forms. Keep articles. Use one term for one thing.
  - Use the STE approved word where you know it ("use", not "utilize"). Keep technical names as they are.
- READMEs hold no changelogs and no script internals. Document internals in the script itself (docstring, comments, `--help`), or in the plugin's `ARCHITECTURE.md` when the script cannot hold them.
- A plugin's tests and evals instructions go in its `ARCHITECTURE.md`.
```

- [ ] **Step 3: Rewrite `README.md`** in STE with these sections only: the title and one-line intro; Install (both fenced blocks byte for byte, plus the `/plugin` and update lines); Requirements, one sentence: the plugins run Python scripts and need `python3` 3.9 or later, with the standard library only; Available plugins, one short paragraph per plugin (what it does, when to use it, extra requirements), each linking its README:
  - `branch-review`: local, agent-run replacement for CodeRabbit PR reviews; reviewer agents check a branch before it becomes a PR, you fix findings, fresh reviewers re-check until clean.
  - `implementation-manager`: runs a planned multi-phase piece of work through child sessions, one PR per unit, with branch-review as the push gate. Needs the Claude desktop app, `gh` and the branch-review plugin (installed with it).
  - `maintenance`: `superpowers-cleanup` removes old superpowers specs, plans and research and fixes every reference; `agents-md-cleanup` keeps `AGENTS.md` files accurate, brief, at the right level and within size limits. Both run only when invoked.
  Nothing follows the plugin list.

- [ ] **Step 4: Check.**
  Run: `python3 <path>/doccheck.py sentences README.md && python3 <path>/doccheck.py links README.md AGENTS.md && python3 <path>/doccheck.py blocks <scratch>/old-root.md README.md AGENTS.md`
  Expected: `0 problem(s)` from each. Then `grep -n "python3\|desktop app\|gh" README.md` shows the Python line and the implementation-manager requirements.

- [ ] **Step 5: Commit.**
```bash
git add AGENTS.md README.md
git commit -m "docs: root README ends at the plugin list; STE rules and layout in AGENTS.md"
```

### Task 2: branch-review README and ARCHITECTURE.md

**Files:**
- Modify: `plugins/branch-review/README.md`
- Create: `plugins/branch-review/ARCHITECTURE.md`

- [ ] **Step 1: Export the old README.** `git show HEAD:plugins/branch-review/README.md > <scratch>/old-br.md`

- [ ] **Step 2: Write `ARCHITECTURE.md`** (plain editor pass, not STE) with, moved from the README: the round modes and when each runs (set-up rules, follow-up, merge-check, forced-deep reasons list, the 40-file / 2,000-line rule, model choice, the record-and-triage rules, second opinion, the six-round cap); the summary and `review-notes.md` contents; file-based arguments (`--note-file`, `--text-file`) and `resolve`; the `rs` wrapper and `CLAUDE_PLUGIN_DATA` paragraph; state-key naming (`__`, `local-<hash>`, `detached-<sha7>`); prune and cleanup safety rules; `check`'s coverage conditions, JSON fields, reason codes and exit codes; `record`'s output, refusals and round rules; the Tests and evals section (both fenced commands byte for byte). Open with one paragraph saying it is for contributors and pointing users to the README.

- [ ] **Step 3: Rewrite `README.md`** in STE: intro; Requirements (`python3` 3.9+; no other); Install; Review a branch (the syntax block and the option list, each option's description shortened to what a user decides; `--state-dir` moves to `ARCHITECTURE.md` since it is for test harnesses); How a review runs, five short bullets (set up, round, triage, second opinion, report) with no thresholds or reason codes, ending with a link to `ARCHITECTURE.md`; Where state lives (the path and the file tree block); Clean up (block and options); For other plugins (`check` and `record`: one short paragraph and the syntax block each); Review guidance in AGENTS.md. Remove every "What's new" section.

- [ ] **Step 4: Check.**
  Run: `python3 <path>/doccheck.py sentences plugins/branch-review/README.md && python3 <path>/doccheck.py links plugins/branch-review/README.md plugins/branch-review/ARCHITECTURE.md && python3 <path>/doccheck.py blocks <scratch>/old-br.md plugins/branch-review/README.md plugins/branch-review/ARCHITECTURE.md`
  Expected: `0 problem(s)` from each. Then `grep -n "What's new" plugins/branch-review/README.md` prints nothing.

- [ ] **Step 5: Commit**, with a body that maps each old `##`/`###` heading to README, ARCHITECTURE.md or "dropped (changelog)".
```bash
git add plugins/branch-review/README.md plugins/branch-review/ARCHITECTURE.md
git commit -m "docs(branch-review): user-facing README in STE; internals to ARCHITECTURE.md"
```

### Task 3: implementation-manager README, ARCHITECTURE.md and run.py docstring

**Files:**
- Modify: `plugins/implementation-manager/README.md`
- Create: `plugins/implementation-manager/ARCHITECTURE.md`
- Modify (only if Step 2 finds gaps): `plugins/implementation-manager/scripts/run.py` (module docstring and argparse `help=` strings only)

- [ ] **Step 1: Export the old README.** `git show HEAD:plugins/implementation-manager/README.md > <scratch>/old-im.md`

- [ ] **Step 2: Compare the "Run script" command tables with `run.py`'s docstring and `python3 plugins/implementation-manager/scripts/run.py <command> --help`.** A command-level fact the script's docs lack (a refusal, an option's effect, the unit-name character rule) goes into the docstring line or `help=` string, briefly. Protocol-level facts (why, how the pieces interact) go to `ARCHITECTURE.md`. Change no code.

- [ ] **Step 3: Write `ARCHITECTURE.md`** (plain editor pass) with, moved from the README: the push protocol between manager and children (the "Pushes go through the manager" paragraphs, syncs, Review notes, outside findings, owner questions, detached children, foreground commands); Push queue; Holds and ordering; the full Merge bar; Integration-branch mode mechanics; Run folder (with the 0.1.0 folder note dropped as changelog) and its file block; the `wait` events table and timings; `status` additions in integration mode; exit codes; Tests (the fenced command byte for byte). Point to `run.py --help` for the command list instead of repeating the tables. Open with one paragraph saying it is for contributors.

- [ ] **Step 4: Rewrite `README.md`** in STE: intro; Requirements (branch-review 0.12.1+, installed with it; the desktop app's session tools, with "does not work in a plain terminal session"; `gh`, logged in; `python3` 3.9+), first after the intro; Install; Run it (the syntax block; what invoking it authorizes and that it authorizes nothing else; a short account: one child per unit, each PR reviewed with branch-review, pushes approved by the manager, one PR in flight at a time by default, parallel only on your request); Integration-branch mode (what it is for, the two setup choices and that the manager asks both, the base-branch and merge-method limits a user must meet); When the manager merges (the merge bar in at most five user-level bullets); Known issues (both items, with the TD-0003 link). Link `ARCHITECTURE.md` once.

- [ ] **Step 5: Check.**
  Run: `python3 <path>/doccheck.py sentences plugins/implementation-manager/README.md && python3 <path>/doccheck.py links plugins/implementation-manager/README.md plugins/implementation-manager/ARCHITECTURE.md && python3 <path>/doccheck.py blocks <scratch>/old-im.md plugins/implementation-manager/README.md plugins/implementation-manager/ARCHITECTURE.md`
  Expected: `0 problem(s)` from each. Then `grep -n "authorizes\|plain terminal\|gh" plugins/implementation-manager/README.md` shows the consent sentence (both halves) and the requirements. If `run.py` changed: `ruff check . && ruff format --check . && python3 -m unittest discover -s plugins/implementation-manager/tests` all pass.

- [ ] **Step 6: Commit**, with the heading map in the body as in Task 2.
```bash
git add plugins/implementation-manager
git commit -m "docs(implementation-manager): user-facing README in STE; internals to ARCHITECTURE.md"
```

### Task 4: maintenance README

**Files:**
- Modify: `plugins/maintenance/README.md`

- [ ] **Step 1: Export the old README.** `git show HEAD:plugins/maintenance/README.md > <scratch>/old-mt.md`

- [ ] **Step 2: Rewrite in STE.** Remove the rename paragraph and its uninstall/install block (changelog). Add Requirements (`python3` 3.9+). Keep both skills' sections, every behavior bullet, the usage blocks, the `Size limits:` block, Repo policy, Folders and Assumption, shortened to STE. No `ARCHITECTURE.md` unless the rewrite finds script internals; if it does, create one as in Task 2.

- [ ] **Step 3: Check.**
  Run: `python3 <path>/doccheck.py sentences plugins/maintenance/README.md && python3 <path>/doccheck.py links plugins/maintenance/README.md && python3 <path>/doccheck.py blocks <scratch>/old-mt.md plugins/maintenance/README.md`
  Expected: `sentences` and `links` report `0 problem(s)`; `blocks` reports exactly one lost block, the `/plugin uninstall superpowers-cleanup…` block.

- [ ] **Step 4: Commit.**
```bash
git add plugins/maintenance
git commit -m "docs(maintenance): README in STE; drop the rename note"
```

### Task 5: deferred and tech-debt READMEs

**Files:**
- Modify: `docs/deferred/README.md`, `docs/tech-debt/README.md`

- [ ] **Step 1: Rewrite the intro paragraph and the Columns table's prose cells in STE.** Leave the Active items table, the column names, the status and impact values, and the `Next id:` line unchanged.

- [ ] **Step 2: Check.**
  Run: `python3 <path>/doccheck.py sentences docs/deferred/README.md docs/tech-debt/README.md && python3 <path>/doccheck.py links docs/deferred/README.md docs/tech-debt/README.md`
  Expected: `0 problem(s)` from each. Then `git diff -U0 docs/deferred/README.md docs/tech-debt/README.md | grep '^[-+]' | grep -v '^[-+][-+]'` shows no `Next id`, `## Active items` table row or column-name changes.

- [ ] **Step 3: Commit.**
```bash
git add docs/deferred/README.md docs/tech-debt/README.md
git commit -m "docs: deferred and tech-debt READMEs in STE"
```

### Task 6: Whole-branch checks

- [ ] **Step 1: Run every check together.**
  Run: `python3 <path>/doccheck.py sentences README.md plugins/*/README.md docs/deferred/README.md docs/tech-debt/README.md && python3 <path>/doccheck.py links README.md AGENTS.md plugins/*/README.md plugins/*/ARCHITECTURE.md docs/deferred/README.md docs/tech-debt/README.md`
  Expected: `0 problem(s)` from each.

- [ ] **Step 2: Validate.** `claude plugin validate .` and `claude plugin validate plugins/<name>` for each of the three plugins. Expected: all pass.

- [ ] **Step 3: Read each README once against the STE rules** for what the checker cannot see: procedural sentences over 20 words, passive voice, more than one instruction per sentence, two terms for one thing across files. Fix and commit any finds.

- [ ] **Step 4: Run `branch-review:review`** on the branch with the spec as the intent, and fix findings until it converges. Pushing waits for the owner.
