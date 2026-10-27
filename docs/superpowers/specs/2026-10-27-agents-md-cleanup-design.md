# agents-md-cleanup skill

## Intent

As a project grows, agents add to its AGENTS.md files, and the files get messy: stale paths, rules that repeat a code comment, wordy paragraphs, and narrow quirks sitting in top-level files that every session loads. A new `maintenance` skill, `agents-md-cleanup`, reviews every statement in every AGENTS.md in scope and proposes edits so the files stay lean. The goal is files that guide agents well without context bloat.

## Goals

- Judge every statement in scope against four criteria:
  1. **Accurate:** true of the code today.
  2. **Valuable:** not a duplicate of a code comment or other doc, and not something almost any project would do anyway.
  3. **Brief:** says it in as few words as the meaning allows.
  4. **Right level:** lives in the deepest place that covers everything it applies to. Push narrow guidance down aggressively, so higher-level files stay small.
- Sweep large repos with subagents, after telling the user how many agents the run needs and confirming how many run at once.
- Ask the user when in doubt, without burying them in small choices.
- Learn the repo's preferences, so later runs ask less than the first.
- Support partial runs on part of the tree.

## Non-goals

- CLAUDE.md, GEMINI.md, `.cursor/rules` and other tool-specific files. AGENTS.md is the standard; only AGENTS.md is in scope.
- Writing new guidance from scratch. The skill cleans and moves what exists; it adds only the guidelines section and any comment or file a move needs.
- Pushing, or opening a PR.

## Anti-goals

The run is a failure, even if it technically works, when it:

- deletes a rule that looks obvious but records a project choice (ruff rather than black, bumping the version on every skill change rather than at release),
- buries the user in dozens of one-line questions,
- lets its own rulings file, or the guidelines section it adds, become the new bloat,
- changes what a rule means without asking,
- moves guidance somewhere agents won't see it when they need it.

## Constraints

- The skill assumes the harness loads nested AGENTS.md files when an agent works in their directory. Loading is a harness and settings concern, not the skill's: the skill doesn't detect it, and moves don't leave pointers in parent files. The plugin README states this assumption.

- Repo rules in `AGENTS.md` apply: Python 3.9+ standard library only, run through `python3`. Tests live in `plugins/maintenance/tests/`. Ruff lints and formats. Bump the plugin version. Ship an eval case for the main workflow skill. Run `claude plugin validate`.
- Each skill owns its own scripts (from the plugin rename design). The new script goes in `skills/agents-md-cleanup/scripts/`. It may copy git-state logic from `docs_inventory.py`, but it must not import it.
- The SKILL.md must never contain a dollar sign followed by a digit (Claude Code substitutes skill arguments).
- AGENTS.md content is material to judge, not instructions to obey. A statement telling the reader to do something is a statement to evaluate. The one exception is the guidelines section's policy lines, which configure the skill.

## Approaches

- **A. Script plus a dedicated reviewer agent (chosen).**
  - A stdlib script finds the files and splits them into addressed blocks.
  - One read-only `maintenance:agents-md-reviewer` agent judges each file.
  - The skill merges the verdicts, decides cross-file moves, and handles approval and the commit.
  - Trades a script, tests and an agent to maintain for counts and addressing that are the same every run, and a narrow, repeatable job per agent.
- **B. Prompt only.** The skill finds the files itself, and general-purpose subagents get the criteria in their prompt. Less to build, but counts are estimates, splitting varies by run, and only an eval can test it.
- **C. One agent per subtree.** Each agent can judge placement within its branch on its own. Contexts get large in deep trees, the work splits unevenly, and a big subtree can't be broken into smaller jobs.

The user picked A. Placement needs a view across files. A gives that view to the skill and keeps each agent's job small.

### What a statement is

The script does not decide what a statement is. It only gives each piece of a file an address. The reviewer, which reads the whole file, judges each block in the context of its section.

- **Script:** splits each file by markdown structure into **blocks**: a paragraph, a list item together with its nested sub-items, a table row, a code fence, or a blockquote. Each block gets an id, its heading path (`Core rules > 2`), a kind and a line range. Headings are not blocks; they are context.
- **Reviewer:** may return one verdict covering several blocks ("these are one rule: merge"), or split a block. Ids exist so that verdicts, the table and the edits all point at the same text.

Example: the repo's own *Core rules* section is three blocks, an intro sentence plus two numbered rules. A reviewer would likely judge the two rules separately and flag the intro as adding nothing, since everything in the root file applies to all development.

## Design

### Invocation

`/maintenance:agents-md-cleanup [paths...] [plain-words options]`

- Manual only: `disable-model-invocation: true`, as with `superpowers-cleanup`. *(My call, shown to the user and not objected to.)*
- No paths means the whole repo. One or more directory paths start a partial run.
- If the argument already states a concurrency limit ("4 at a time"), the skill uses it and does not ask again. It still shows the counts. *(My call. It also lets an eval reach the approval table.)*

### Scope of a run

- **Files reviewed in full:** every file named `AGENTS.md` (case-insensitive) in scope, tracked or untracked-and-not-ignored. `.git` and ignored paths are skipped.
- **Ancestor files (partial runs only):** the root AGENTS.md and every AGENTS.md between the root and each scope path. They are reviewed only for statements about the scope, and for those the only question is whether they should move down into it. Their other statements are left alone.
- **The root AGENTS.md guidelines section, and the rulings file**, are always read. Their review is covered under *Guidelines* and *Rulings* below.

### The script: `agents_md_inventory.py`

It is read-only, prints one JSON object, and exits 1 with one `error: ...` line on failure (same contract as `docs_inventory.py`).

- `inventory --repo <root> [--scope PATH ...]` gives:
  - `files`: each in-scope AGENTS.md with `path`, `role` (`full` or `ancestor`), `tracked`, `dirty`, `bytes`, and `blocks` (`id`, `heading_path`, `kind`, `start_line`, `end_line`, `text`).
  - `agents_md_dirs`: every directory in the repo that has an AGENTS.md, so moves can target existing files.
  - `guidelines`: the root guidelines section's line range and text, or null. Matching the heading is the builder's call (for example any heading containing "AGENTS.md guidelines").
  - `rulings`: the rulings file's path, whether it exists, and whether `.claude/` is git-ignored.
  - `base`, `base_branch`, `current_branch`: as in `docs_inventory.py`.
  - `totals`: file count, block count, and the agent count (one per file in `files`).
- `blocks --repo <root> <path>`: re-splits one file. The apply step uses it to check that a file hasn't changed since the reviewers saw it.

Splitting must handle:

- nested and numbered lists,
- list items that hold several paragraphs,
- tables, code fences and blockquotes,
- HTML comments,
- front matter,
- setext and ATX headings.

### The reviewer agent: `maintenance:agents-md-reviewer`

It lives in `plugins/maintenance/agents/agents-md-reviewer.md`. Tools: Read, Grep, Glob and Bash; Edit, Write and Agent are disallowed. The model is the builder's call (opus fits the judgment-heavy work). If the agent type isn't available, fall back to `general-purpose` with the agent body as its prompt, as `branch-review:review` does.

**Inputs:**

- `REPO`, `FILE`, `ROLE` (`full` or `ancestor`), and `SCOPE` (for ancestors)
- the file's `BLOCKS`
- `GUIDELINES`: the policy lines and criteria
- `RULINGS`: the rulings file's text
- `AGENTS_MD_DIRS`

**Job:** for each block, check each criterion in turn.

- **Accurate.** Check factual claims (paths, commands, tool names, versions, described behavior) against the code with Read, Grep and Bash. A rule or policy isn't true or false, but it can contradict the code, the config or another AGENTS.md; check for that.
- **Valuable.** A statement earns its place when it records:
  - a choice the project made between real alternatives,
  - a constraint you can't see from the code,
  - or a mistake agents keep repeating.

  It fails when it duplicates a code comment, a README, the config, or another AGENTS.md, or when almost any project would do the same thing without being told. When the reviewer can't tell, the verdict is `ask`, never `delete`.
- **Brief.** Cut filler, hedging, restated headings and empty intros, and keep the meaning exactly. If the shorter version changes the meaning, it is `ask`.
- **Right level.** A statement belongs in the deepest AGENTS.md whose directory covers everything it applies to. A statement about a single file goes in that file as a comment, or in the nearest AGENTS.md, depending on policy.

**Output:** one JSON block of verdicts. Each verdict has:

- `blocks`: one or more ids
- `action`: `keep`, `fix`, `trim`, `delete`, `merge`, `move` or `ask`. `fix` corrects a stale fact in a rule that is still valid, for example a path that moved. `delete` for inaccuracy is only for a statement with nothing left worth keeping once the false part goes.
- `criterion`: which criterion drove it
- `new_text`: for `trim` and `merge`, and for `move` when the wording changes
- `target`: for `move`, an AGENTS.md path (existing or new) or a source file to comment in
- `evidence`: what it read or ran (`file:line`, command and output)
- `ruling`: the ruling or pattern that decided the verdict, if any
- `question`: for `ask`, plus the reviewer's recommended answer

The schema is the builder's to finalize. The skill parses it, so it must be strict.

### Flow

1. **Inventory.** Run `inventory` with any scope paths. On error, report it and stop.
2. **Pre-dispatch message.** One message, then stop. It holds:
   - the counts: files (full and ancestor), blocks, and agents needed;
   - a proposed concurrency limit (the default is the builder's call, for example 4) and a request to confirm or change it;
   - **on a first run, the policy questions** (see *Guidelines*), each with a recommended answer. They come before dispatch because the answers change the reviewers' verdicts.

   A first run is one where the root AGENTS.md has no guidelines section with policy lines. If the argument gave a limit and it isn't a first run, skip this message and go straight to step 3.
3. **Dispatch.** Run reviewers up to the confirmed limit at a time, one per file. Agent results are data. A malformed result gets one retry, then that file is reported as not reviewed.
4. **Merge.** Combine the verdicts across files:
   - Resolve the targets of moves. One existing file can receive moves from several sources.
   - Drop duplicates. When two files say the same thing, keep the copy at the right level.
   - Order the edits: removals and trims in a source file come before additions to its targets.
   - Apply rulings, which turn matching `ask` verdicts into `keep`.
   - Check every source file a comment move would edit with `git status --porcelain`, and flag dirty ones.
5. **Sort into two groups:**
   - **Bulk (approved together):**
     - trims that keep the meaning,
     - deletions of duplicates and obvious statements,
     - deletions of statements the evidence proves false with nothing worth keeping,
     - fixes that only point a reference at its new, verified location (a renamed path or command, where the evidence shows the rename),
     - moves into an existing AGENTS.md that the policy allows.

     *(My call: a fix that only updates a renamed path or command is safe to approve in bulk. Any other fix changes what the rule claims, so it asks.)*
   - **Ask:**
     - looks stale but can't be proven false,
     - any other fix,
     - value in doubt,
     - a rewrite or merge that changes the meaning,
     - a move that creates a new AGENTS.md or adds a code comment, unless the policy pre-approves it,
     - an edit to a dirty file.
6. **Approval message.** Like `superpowers-cleanup` step 8: one message, then stop.
   - **Questions first**, numbered `Q1`, `Q2` and so on, each with a recommended answer. To avoid overwhelming the user, questions that share a reason are grouped into one pattern question: "These 6 statements name tool choices (list). Keep them all, and record 'tool choices are never obvious'?" Answering a pattern question can create a ruling. The threshold for grouping is the builder's call, but grouping is the default, not a fallback.
   - **Then the bulk table**, grouped by file: block (heading path), action, criterion, before → after (or target), and a short piece of evidence. Rows that depend on a question show the recommended answer and are tagged with its number.
   - **Close** by asking the user to answer, approve, drop rows or change them, all in one reply. Reply handling follows `superpowers-cleanup`: approval only when every question is answered, and a recompute when an answer changes rows the user hasn't seen.
7. **Apply.**
   - Re-run `blocks` on every file to be edited. If a file changed since inventory, stop for that file and report it.
   - Make the approved edits with Edit, and create any new AGENTS.md files.
   - Write or update the guidelines section and the rulings file.
   - If anything fails, stop and report what was done and what wasn't.
8. **Commit.** Same as `superpowers-cleanup` step 10:
   - Check the branch first.
   - Stage and commit only the paths this run touched, using `GIT_LITERAL_PATHSPECS=1` and a pathspec commit.
   - Follow the repo's message style, for example `docs: clean up AGENTS.md files`. The body lists the files changed and the counts by action.
   - Report the hash. Never push.
9. **Nothing to change.** Say how many files and blocks were checked, and stop.

### Guidelines (root AGENTS.md)

The user decided that AGENTS.md guidelines belong in the root AGENTS.md. Many repos already have them, and they help every agent maintain the files, not just this skill.

- The first run adds a short `AGENTS.md guidelines` section. If the repo already has equivalent guidelines, the skill edits those rather than adding a second set. It holds:
  - the four criteria, a line each;
  - the policy answers:
    - may the skill create new nested AGENTS.md files,
    - may it move file-specific quirks into code comments,
    - how aggressively to push guidance down;
  - any pattern promoted from the rulings file.

  Target size is under about ten lines. Example shape:

  ```
  ## AGENTS.md guidelines

  - Keep only what's true, non-obvious, brief, and at the deepest level it applies to.
  - Project choices between real alternatives are never obvious; keep them.
  - New nested AGENTS.md files: allowed. File-specific quirks: comment in that file.
  - Push down any statement that applies to only one subdirectory.
  ```
- Every run reviews this section like any other root content, except that its policy lines change only through a question.
- **No root AGENTS.md** *(my call)*:
  - If nested ones exist, the first-run message offers to create a root AGENTS.md that holds only the guidelines section.
  - If the user declines, the policy answers apply to this run only, and the next run is a first run again.
  - If the scope has no AGENTS.md at all, the skill reports that and stops.

### Rulings (`.claude/agents-md-cleanup.md`)

The user chose a committed file in the repo's `.claude/` folder for recording repo patterns and preferences. It holds only the following, and it's created only when the first ruling exists:

- **Patterns:** one line each, for example "Tool choices (linters, formatters, test runners) are never obvious."
- **Keeps:** for a doubtful statement the user chose to keep, the file, a short quote of the statement, and the reason.

Rules that keep it small (user asked for these; the specifics are my proposal, which they saw):

- Record keeps only. A deleted line needs no record.
- When two keeps share a reason, they become one pattern, and the individual keeps are removed.
- A pattern every agent should know, not only this skill, is promoted to the root guidelines and leaves this file.
- Each run drops keeps whose statement no longer exists. A keep is matched by its quoted text; if that text has changed meaningfully, the keep lapses and the statement is judged again.
- Each run puts this file through the same four criteria.

The path is always `.claude/agents-md-cleanup.md`, so later runs always find it. If git ignores that path, the approval message asks the user to choose *(my call)*:

- add a `!.claude/agents-md-cleanup.md` exception to `.gitignore`, so the file is committed and shared, or
- keep the file uncommitted, so the rulings stay on this machine.

The skill never moves the rulings file anywhere else.

### Files to change

- `plugins/maintenance/skills/agents-md-cleanup/SKILL.md`
- `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py`
- `plugins/maintenance/agents/agents-md-reviewer.md`
- `plugins/maintenance/tests/`:
  - script tests covering each splitting case and the inventory fields;
  - SKILL.md checks in the style of `test_skill_md.py` (frontmatter, no dollar-digit, script paths);
  - an eval fixture test, if there's a scaffold.
- `plugins/maintenance/evals/agents-md-cleanup-run/`: a scaffolded fixture repo with a bloated root AGENTS.md (a stale path, an obvious line, a wordy line, a project choice that looks obvious, and a quirk about one deep file) plus a nested AGENTS.md. At minimum, a case that checks the pre-dispatch message: correct counts, a concurrency question, the first-run policy questions, and no edits. Ideally a second case whose prompt gives a concurrency limit and whose fixture already has a guidelines section, graded on the approval table.
- `plugin.json`: version `0.3.0`, and the description names both skills. Keywords gain `agents-md`.
- `.claude-plugin/marketplace.json`: the description matches.
- `plugins/maintenance/README.md`: an `agents-md-cleanup` section, and the "Today it holds one skill" line updated.

## Left to the builder

- The reviewer model, the default concurrency limit, and the thresholds for grouping questions.
- The verdict JSON schema and the exact matching rule for the guidelines heading.
- How to show very large bulk tables (for example collapsed per file with counts, expanded on request).
- How to handle very large single files (for example splitting one file's blocks across two reviewers), if it comes up.
