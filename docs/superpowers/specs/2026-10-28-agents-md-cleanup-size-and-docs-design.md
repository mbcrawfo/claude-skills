# agents-md-cleanup: rule size, documentation push-out and limits

Builds on [the agents-md-cleanup design](2026-10-27-agents-md-cleanup-design.md). Everything that design settles stays as it is unless this document says otherwise.

## Intent

The first real run of `agents-md-cleanup`, on the SqlSource repo (36 AGENTS.md files, 458 blocks), did its job: nearly every fact checked out, 115 rules moved to the right level, one approval message, one commit. What it could not do was shorten anything that mattered. The repo's old guideline measured files in lines, so agents had packed up to a dozen rules, their reasons, file lists and test names into single 460-word bullets. The reviewers judged each bullet as one statement and returned `keep` or `move`; the files kept the same bulk in new places. An agent working deep in the tree still loaded about 68 KB of AGENTS.md before it started.

A second, hand-run pass fixed that: limits of 80 words per rule and 8 KB per file (16 KB for the root), explanation moved into `docs/architecture/` pages and test READMEs, one rule per bullet, and a test that enforces the limits. The loaded context fell to about 30 KB. This design folds what that pass learned into the skill, so the next repo gets it from the normal review.

## Goals

- **One rule per bullet**, always. A bullet that holds several independent statements is split, limits or not.
- **Documentation leaves AGENTS.md.** A statement that describes the project rather than directs the agent, or that an agent needs only for some tasks, moves to the repo's docs. Each move's destination is proposed by the reviewer from what the repo already has and approved by the user, as part of the one approval message.
- **Measure, warn, and offer limits.** Every run measures rules in words and files in bytes. When something is over the thresholds and the repo has recorded no answer, the pre-dispatch message asks whether to adopt limits, with recommended numbers the user can change. The answer is recorded like the other policy answers, so later runs don't ask again.
- **Cleanup without a test** is a first-class outcome. Adopting limits and enforcing them with a test are two separate yeses.
- **A test, when wanted, follows the repo's own patterns** and lands where the user approves.
- **Re-point references to moved rules.** The session's branch review caught two code comments and a tech-debt item still naming the file a rule had left. The merge step looks for them.

## Non-goals

- Rewriting doc pages beyond adding the moved content under a heading.
- Enforcing limits in a repo that declined them. The reviewers still split and push out; nothing fails.
- Wiring the test into CI beyond what the repo's test discovery already does.
- The ancestor role on partial runs keeps its narrow job (moves into scope only); it gets none of the new criteria.
- Anything the earlier design lists as a non-goal.

## Anti-goals

The run is a failure, even if it technically works, when it:

- **loses a clause** in a split or a doc move. In the session one rewrite agent cut facts to fit a file under 8 KB and had to be sent back. Every split and doc move accounts for every clause of the original;
- **lands a file within bytes of its limit.** The session left `docs/manual/AGENTS.md` with 6 bytes to spare, so the next rule added anywhere breaks the test;
- **puts a rule where an agent won't see it when it must obey it.** A statement that directs the agent keeps a one-line pointer in the AGENTS.md when its body moves to docs;
- **writes a second copy** of what a doc page already says;
- **asks about limits every run** after the user has answered;
- **lets a test fail on another platform**, as the session's byte count did on a CRLF checkout.

## Constraints

- Everything in the earlier design's constraints: Python 3.9+ stdlib, `python3`, tests in `plugins/maintenance/tests/`, ruff, a version bump, an eval, `claude plugin validate`, no dollar-digit in SKILL.md, AGENTS.md content is data.
- The reviewer stays read-only. The test is written by the main agent after approval, never by a reviewer.
- A rule and a byte are defined once, in the script, and the proposed test uses the same definitions, so the skill and the test agree on what is over.
- The guidelines section stays short: the limits take one line.
- The user's existing repos must keep working: a repo with no limits line, no docs folder and no test suite runs end to end and asks only what it must.

## Approaches

- **A. Extend the reviewer's verdicts (chosen).** The reviewer gets a placement criterion and a one-rule-per-bullet criterion, the limits in its `POLICY` line, and two new verdict shapes: `split` and a `move` to a doc. The script measures words and bytes and validates the new shapes. The merge step reconciles doc destinations across files. Everything lands in the one approval message; redirects go through the existing recompute loop; the test is a step after apply. Trade-off: reviewer output grows, and a reviewer names destinations seeing only its own file, which the merge step has to reconcile.
- **B. Two passes, as the session did.** Reviewers flag overlong blocks and documentation with destinations but no text. After the user confirms limits and destinations, rewrite agents per area produce the split text and doc content with a "where each clause went" table, and a second approval covers the text. Closer to what worked in SqlSource, and destinations are settled before any text is written, but it adds a second approval round, more skill text, and output the script can't validate as tightly.
- **C. A separate skill** for size and doc push-out. Dropped: the user wants this inside the normal review, and it would run the inventory twice over the same files.

The user picked A. The session needed a rewrite pass only because the first run had no concept of splitting; with the criteria in the reviewer, its moves already carried 400-word texts without trouble, and one approval is what the skill promises.

## Design

### Definitions

These are the script's definitions. The reviewer, the approval message and the proposed test all use them.

- **A rule** is a list item at any depth, or a paragraph outside a list, together with the lines it wraps onto. Its list marker is not part of it. A nested item is its own rule, not part of its parent's.
- **Words** are whitespace-separated tokens of a rule's text. Fenced code, headings, table rows, blockquote lines, HTML comments and front matter count no words and are not rules.
- **Bytes** are the UTF-8 bytes of the file with `\r\n` normalised to `\n`, so a CRLF checkout measures the same as an LF one.
- **Thresholds** are the numbers the skill warns at and recommends as limits: 80 words per rule, 8 KB (8192 bytes) per file, 16 KB for the root AGENTS.md. They are constants in the script, overridable per run from the arguments.
- **Headroom:** when limits are in force, a reviewer brings each file to at most 90% of its byte limit, so the next addition has room. *(My call; the number is the builder's to tune, not whether there is one.)*

### The limits line

One line in the guidelines section records the answer, in one of these exact shapes:

```
Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; checked by tests/docs/test_agents_md.py.
Size limits: 80 words per rule, 8 KB per file, 16 KB for the root; not checked by a test.
Size limits: none.
```

The numbers are the user's. The line's grammar is fixed so the script can parse it: `Size limits:` then either `none` or `<n> words per rule, <n> KB per file, <n> KB for the root; ` followed by `checked by <path>` or `not checked by a test`. A run is a *first size run* when the section has no `Size limits:` line. The arguments can override for one run in plain words ("100 words", "10 KB", "no size limits"); an override is used for the run and offered as the new line in the approval message.

### Script changes (`agents_md_inventory.py`)

- `inventory` gains, on every file entry, `bytes_normalised` and `rules`: a list of every rule with its `block` id, `line` and `words`. The entry also gets `over`: the rules over the rule limit and whether the file is over its byte limit, judged against the limits in force. `totals` gains `rules`, `rules_over`, `files_over` and the `limits` used: `{"source": "guidelines" | "arguments" | "default" | "none", "rule_words", "file_bytes", "root_bytes", "test"}`. With `source: "none"` the counts are still measured against the defaults, so the skill can report them in one line, but nothing is marked `over`.
- `inventory` gains `docs`: the repo's documentation folder (`docs/`, `doc/` or `documentation/` at the root, in that order of preference, or null) and the markdown files under it with their headings, plus every `README.md` beside an AGENTS.md. Reviewers use it to find candidate destinations without crawling.
- `inventory` gains, per file entry, `references`: the `path:line` hits of a literal `git grep` for the file's path and aliases outside AGENTS.md files and `.git`, capped at a count the builder picks, with `truncated: true` when the cap was hit. The root's path, `AGENTS.md`, matches every mention of any AGENTS.md, so its list is expected to be truncated and is a seed only; the merge step also greps for each moved rule's backticked names (see step 4). *(My call, from the builder check.)*
- `inventory` reads the `Size limits:` line from the guidelines section and takes `--rule-words`, `--file-bytes` and `--root-bytes` overrides.
- `check-verdicts` validates the new action and target kind below, and, when limits are in force, rejects any `new_text`, part text or pointer text that holds a rule over the word limit, with a one-line error the reviewer can act on. A `keep` on a rule over the word limit with no `whole` ruling is turned into an `ask` ("keep it whole, with an entry in the test's exception list, or split it?"), the way the project-choice guard turns a deletion into an `ask` today. It cannot check clause preservation; that is the reviewer's accounting and the user's approval.

### Reviewer changes (`agents-md-reviewer.md`)

**Criteria order** becomes `accurate`, `valuable`, `placement`, `brief`, `level`. Placement sits before brief because explanation is moved, not trimmed.

**Placement: documentation, not instruction.** A statement goes to the repo's documentation when it describes the project rather than directs the agent, or when an agent needs it only for some tasks. How a reader treats a missing key, the seven steps of a describe, the brand colour of a primary button: documentation. "Use `uv`, never `pip`", "run `scripts/test.sh` before a PR", "never put user text into a statement the tool sends": rules, kept. The reviewer:

1. looks for a destination that fits the content: a page the statement already links to, a page under `docs` whose headings cover the topic, or the README beside the code it concerns;
2. reads the candidate first. When the page already says it, the verdict is the existing duplicate handling (`delete`, or `trim` to a pointer line when the statement directs the agent), never a second copy;
3. otherwise proposes a `move` with `target_kind: doc`, `target` the page (existing or new), `target_heading` where it goes, `new_text` the doc text, and `pointer`: the one-line rule that stays in the AGENTS.md, such as "Button colours follow `docs/branding.md`.", or absent when the statement was pure description;
4. when nothing fits, names a new file under the docs folder with a name drawn from the content (`docs/branding.md`, `docs/ui-standards.md`) and says in the evidence why that name. With no docs folder it proposes `docs/<name>.md` and the skill says the folder would be created.

A doc move's `evidence` accounts for every clause of the original: where it went, or that it was dropped because `path:line` already states it.

**Brief: one rule per bullet.** A block that holds several independent statements gets a `split`: `parts`, an ordered list, each with `text` (one rule, marker included) and, when it leaves the file, `target`, `target_kind` (`agents_md`, `comment` or `doc`) and `target_heading`, with `pointer` allowed on a doc part. A part with no target stays in place. Explanation beyond one sentence of reason inside a rule is a doc part. The split's `evidence` carries the same clause accounting as a doc move. Two statements that need different actions, which today return `ask`, become a `split` when the actions are among keep, move and doc move; anything else still asks.

**Limits in `POLICY`.** The dispatch prompt's `POLICY` line gains `size limits: 80 words per rule, 8 KB per file (root 16 KB)` or `size limits: none`, and the reviewer gets the file's `bytes_normalised` and its `over` list. With limits, the reviewer must bring every rule under the word limit and the file under 90% of its byte limit, in this order of preference: doc moves, comment moves, moves to nested AGENTS.md files (policy permitting), splits, trims. It never drops a clause to fit; when it cannot get under the limit without dropping one, it returns `ask` with the clause and its best proposal. Without limits, the reviewer still splits and pushes out, and reports the file's size only when it is over the thresholds.

**Rulings** gain one case: a keep whose quote the user marked `whole` means the rule is kept as one bullet even over the word limit. The reviewer keeps it, with `ruling` set, and the skill lists it in the test's exception list. A `whole` keep records the file, the quote and the reason like any keep, plus the word `whole`, so the exception list can always be regenerated from the rulings file.

### Skill changes (`SKILL.md`)

**Step 1, inventory.** The arguments may also carry size overrides. The limits in force are reported with the counts.

**Step 2, confirm before dispatch.** On a first size run where `rules_over` or `files_over` is not zero, the message adds one size question after the policy questions: the counts, the five worst rules (file, first words, word count) and the files over with their sizes, and the question "Adopt size limits? Recommended: 80 words per rule, 8 KB per file, 16 KB for the root. Change the numbers, or answer none." It recommends adopting. On a repeat run with a limits line, no question; one line states the counts over the limits in force, or, with `none`, over the defaults. On a first size run with nothing over the thresholds, no question and no line, and no limits line is written: the next run that finds something over asks.

**Step 3, dispatch.** `POLICY` carries the limits; the prompt carries `DOCS` (the inventory's docs summary), `BYTES` and `OVER` for the file.

**Step 4, merge.** Three additions:

- **Reconcile destinations.** Doc moves and doc parts are grouped by `target`. Two reviewers proposing new files for one topic under different names (`docs/branding.md`, `docs/ui-standards.md`) become one question offering one file. Moves into an existing page are grouped by page and heading; the page goes through `blocks` for its `sha256` and `resolved` path like any other edited file.
- **Re-point references.** For each rule that leaves a file (any move, any doc part), the file's `references` are read, and the repo is grepped for the rule's backticked names (a type, a script, a path) in files that also mention AGENTS.md. A reference that names that rule's content gets a bulk row updating it to the new place. A reference the skill can't judge becomes a question.
- **Size accounting.** With limits, the merged edits are re-measured per file (the script's definitions, applied to the edited text). A target file over its byte limit, which happens when several sources move rules into it and no single reviewer could see the total, gets one more reviewer round: the file's reviewer is re-dispatched on the merged text, told the limit and what came in, and its verdicts are merged like any other. At most one such round per file, before the approval message. If the file is still over, the approval message asks: accept it for now, with a file-level entry in the test's exception list and its reason, or leave those moves out so the rules stay where they were. A file under its limit but over the headroom is reported, not asked about. *(The user chose this over always asking.)*

**Step 5, sort.** Splits whose parts all stay in place or go to existing files are bulk. A split or doc move that creates a new file is bulk when the new-file policy allows, otherwise a question. The approval message shows each doc destination; the user can redirect any of them, and a redirect is recomputed and shown again before anything is written, as a changed row is today.

**Step 6, approval message.** New sections:

- **Documentation moves**, grouped by destination, new files marked and the name's reason given; each row shows the doc text and the pointer that stays.
- **Splits**, before → after, with the clause accounting.
- **Size**, per file over the thresholds: before → after bytes, longest rule before → after.
- **The limits line** to be written, when this run set or changed it.
- **The test question**, when limits were adopted or changed this run: "Enforce the limits with a test? Proposed: `<path>`, `<framework>`, modelled on `<existing doc test>`; it uses the same rule and byte definitions, normalises CRLF, has boundary cases at each limit, and an exception list holding `<rules kept whole>`. Recommended: yes." When the repo has no test suite, the offer is a script check at a path following the repo's conventions, or none. The answer goes into the limits line (`checked by <path>` or `not checked by a test`). Limits without a test is a complete, ordinary outcome.
- **Test maintenance**, on a later run whose limits line names a test: when this run changes the limits (an override offered as the new line) or adds a `whole` keep or a file-level exception, the test's constants or exception list need the same change. The skill reads the test, and the approval message carries rows for those edits, before → after. The test file goes through `blocks` for its `sha256` and `resolved` path like any other edited file. *(My call, from the builder check.)*

**Step 7, apply.** Doc text goes under the approved heading, at the end of that section, or into a new file with an `# <title>` heading; a new docs folder is created when the message said so. Pointer lines replace the moved statement in place. Reference updates are ordinary edits. When the test was approved, or an existing one has approved edits, the main agent writes it after the AGENTS.md edits, following the proposed shape, then runs that test alone with the repo's test command and its filter idiom (`dotnet test --filter`, a `pytest` path, a `jest` path), never the whole suite. Three outcomes:

- It passes: the run goes on to the commit.
- It runs and fails: the run stops with the report and nothing is committed. The script measured the edited tree in step 4, so a failure means the test and the script disagree, which is a bug to show the user, not something to paper over.
- It can't run (no toolchain in the sandbox, a build failure outside the test, no recognisable test command): the approved AGENTS.md, doc and reference edits are committed as usual, the limits line is written as approved, and the test file is left in the tree uncommitted. The report says the test needs a run and a commit by the user. *(The user chose this over stopping to ask, which would be a second interaction after the one approval.)*

**Step 8, commit.** The doc files, reference updates and the test are part of the one commit, except a test that could not be run, which stays uncommitted.

**Step 9, nothing to change.** Also reports the size counts in one line when something is over.

### The proposed test

The skill proposes, and after approval writes, a test shaped by the repo:

- It lives beside the repo's existing documentation tests when there are any, else in the main test project, named by the repo's convention. *(The session's `AgentsFileTests.cs` beside `UserDocsLinksTests.cs` is the model.)*
- It finds every tracked AGENTS.md from the repo root, skipping `.git` and build output.
- It applies the script's definitions of rule, word and byte, with CRLF normalised.
- It holds the limits as constants and has boundary cases for each: a rule at the limit passes and one word over fails, a file at the byte limit passes and one byte over fails, the root limit the same. The session's second-opinion review found the first version lacked these; a loosened comparison passed silently.
- It has an exception list of `(file, first words of the rule, reason)` for rules kept whole, and `(file, reason)` for a file accepted over its limit, empty when there are none.
- It passes on the tree the run leaves behind.
- Later runs keep it current (see *Test maintenance* in step 6).

The builder writes the skill's instructions for proposing this shape; the skill never ships test code, since the framework is the repo's.

### Files to change

- `plugins/maintenance/skills/agents-md-cleanup/SKILL.md`: steps 1 to 9 as above.
- `plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py`: rule measurement, limits line parsing and overrides, `docs`, `references`, `split` and `doc` validation, word-limit checks on new text.
- `plugins/maintenance/agents/agents-md-reviewer.md`: the placement criterion, one rule per bullet, the `split` and doc `move` shapes, limits handling, the `whole` ruling, and an extended example.
- `plugins/maintenance/tests/`:
  - script tests: rule and word counting across nested lists, wrapped items, fences, tables, quotes and comments; CRLF byte normalisation; the limits line in each shape and the overrides; `docs` and `references` discovery; `check-verdicts` on `split` and doc `move`, including the word-limit rejection and every field rule;
  - SKILL.md and agent text tests in the style of `test_agents_md_skill.py`: the size question, the limits line grammar in both files, the test question, the criteria order, the clause-accounting requirement, the no-second-copy rule;
  - the eval fixture test, for the new scaffold.
- `plugins/maintenance/evals/agents-md-cleanup-size/`: a scaffold like the review case's, with a guidelines section holding the three policy lines and no limits line, a `docs/architecture.md` page with headings, and in the root AGENTS.md one 250-word bullet holding four rules plus a paragraph of how-it-works explanation, and a second short bullet that is pure description already covered by the page. Prompt: `/maintenance:agents-md-cleanup 2 at a time`. Graders: the pre-dispatch message reports the counts and asks the size question with the recommended numbers; after an answer in the prompt or a second case, the approval message splits the long bullet into one rule each, moves the explanation under the page's heading with a pointer, deletes the covered description, and proposes the limits line; nothing is written before approval. The existing two cases keep passing.
- `plugin.json`: version `0.4.0`; the description mentions size limits. `marketplace.json` matches.
- `plugins/maintenance/README.md`: the `agents-md-cleanup` section gains size limits, documentation moves, the test offer, and the limits line.
- `docs/tech-debt/TD-0008-...`: updated if any of its items change; new items for whatever this work knowingly leaves coarse (for example the heading-based choice of a doc page).

### Left to the builder

- Field names beyond those given, the exact grammar regex for the limits line, and the cap on `references`.
- The headroom percentage, and how the approval message shows a file that is under its limit but over the headroom.
- How many worst rules the size question lists, and how doc moves are collapsed when there are dozens.
- How `docs` picks among several candidate folders, and what counts as a heading match.
- The exact wording the skill uses to propose the test's shape per framework it recognises (xunit, pytest, jest, go test) and the fallback for an unknown one.
- Whether `references` hits inside `docs/superpowers` are skipped, as the session skipped them by hand.
- The test commands and filter idioms the skill recognises, and how it tells "can't run" from "ran and failed" for each.
- The prompt for the one extra reviewer round on an over-limit target: what it carries beyond the normal template.
