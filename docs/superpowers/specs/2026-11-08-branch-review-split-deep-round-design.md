# branch-review: split deep round and token efficiency: design

Full design (project). Covers the `branch-review` plugin only.

## Why

Two weeks of local sessions (2026-10-25 to 2026-11-08: 485 reviewer runs in 202 review runs, nearly all on mbcrawfo/SqlSource and this repo) cost about $2,070 at list prices (Opus $5/$25, Sonnet $3/$15 per million input/output tokens). Deep rounds on Opus are 85% of that:

| Round kind | Rounds | Cost | Median | New findings |
| --- | --- | --- | --- | --- |
| Deep (first, or forced restart) | 144 | $1,279 | $8.06 | 84 major, 438 minor |
| Deep, second opinion | 51 | $481 | $9.23 | 23 major, 116 minor |
| Merge-check (Opus) | 41 | $91 | $1.84 | 12 minor |
| Follow-up (Opus, decided-fix or large delta) | 37 | $89 | $1.76 | 2 major, 27 minor |
| Follow-up (Sonnet) | 204 | $84 | $0.29 | 2 major, 45 minor |

Quality is high and must stay so: of 781 findings, 1 was invalid and 761 were fixed and verified. The second opinion is valuable: 47 of 52 found something, and its 23 majors are about one in five of all majors, so a single first deep pass misses a real share.

Where a deep round's cost goes:

- **Re-read context: $1,126 of $1,760.** A median deep round takes 64 turns and its context grows to about 240k tokens. Every turn re-reads the whole context from cache, so cost grows with turns times context size.
- **One tool call per turn.** The reviewer averages 1.15 tool calls per turn. A median round spends about 21 turns on greps and 17 on reading SqlSource's nested `AGENTS.md` files one at a time.
- **Fixed instructions.** `agents/reviewer.md` (30 KB), `finding-schema.md` (26 KB) and `reviewer-checklist.md` (17 KB) sit in every turn's context: about 25% of the re-read cost. Most of `finding-schema.md` documents `review.json` and the state commands, which the reviewer does not need.
- **Double reads.** A quarter of changed files in deep rounds are new files, which the reviewer reads once in the diff and again in full.
- **Duplicate report.** The reviewer writes its report to `REPORT_PATH`, then repeats it word for word as its final message (about $22 of output in two weeks).
- **Size does not set the cost.** Deep rounds on 5 changed files or fewer had a median cost of $9.61, about as much as large ones: the required steps run whatever the size.
- Cache misses ($33) and the review skill's own text in the orchestrator's context (about $31) are negligible.

The analysis scripts that produced these numbers become the usage report below.

## Goals

- Cut the cost of deep rounds, the bulk of review spend, while keeping recall of majors at least where it is.
- Split a deep round's work across parallel reviewers that each carry a smaller context and fewer turns.
- Make every reviewer cheaper per turn: fewer turns through grouped tool calls, and less fixed instruction text.
- Check each change cheaply before it reaches real branches, and decide on it from real use.
- Use real use to decide whether the second opinion is still needed.

## Non-goals

- Changing follow-up rounds or merge-check rounds, beyond the shared instruction-file and tool-grouping changes. They are 13% of spend.
- Changing which model a round runs on. Moving merge-checks to Sonnet (12 minors in 41 rounds) was considered and left for later.
- Changing the orchestrator's triage, fix and dispute rules.
- Changing the second-opinion round itself in this project. It stays a single reviewer doing the whole deep process; real use only decides whether its trigger can be narrowed (see Phase 3).
- A statistically firm benchmark. The pre-release check is a smoke test for clear regressions; the decisions rest on real use.

## Anti-goals

- An evaluation too expensive to run. The owner rejected a $500–800 replay plan: each check must cost tens of dollars, not hundreds.
- Fewer majors found. A cheaper review that misses majors the current one finds is a failure, whatever it saves.
- Findings that are not real. Precision is near perfect today; more reviewers must not mean more noise for the orchestrator to dispute.
- The same defect reported two or three times and fixed or argued about more than once.
- Any material from mbcrawfo/stashman (private) in this public repo, including replay sets, results and examples.

## Constraints

- The state script is Python 3.9+, standard library only, run through `python3`, with tests under `plugins/branch-review/tests/` (repo `AGENTS.md`). The replay and usage report follow the same rules.
- State is per branch in `review.json` under the plugin data directory, and a round today records exactly one report (`record-findings --file`, refused when the round already has findings).
- A deep report from plugin 0.9.0 on must list every one of the 16 `COVERAGE_ITEMS` (`review_state.py`), and `check-report` applies the same checks as `record-findings`.
- The second-opinion trigger reads each deep round's `merge_risk` and the severities of findings opened since the cycle start.
- implementation-manager drives the review only through `/branch-review:review`, `check` and `record`, with no arguments that pick the round shape. A setting that turns the split on must work without new arguments.
- The plugin already has a `userConfig` option (`retention_days`); Claude Code substitutes a saved value into skill text and keeps the literal `${user_config.<name>}` when none is saved (ARCHITECTURE.md, Pruning and cleanup).
- Snapshots of all 144 past first deep rounds still exist in the local SqlSource and claude-skills repos. Each one's transcript gives what that round cost, and `review.json` gives what it found: a free baseline.
- Replay and report output is local data, gitignored like `evals/results/`.

## Approaches

Three ways to cut cost were considered.

1. **Mechanical changes only.** Group tool calls, shorten the instruction files, skip double reads, drop the duplicate final report, Sonnet for merge-checks. About 20–25% saved; checks nothing less. Caps the savings and leaves the first-pass recall gap alone.
2. **Mechanical changes plus less work in the costly steps.** Cap test-breaking, combine the doc greps, lower the reviewer's effort. About 30–40%. Trades recall for cost step by step.
3. **Split the deep round into parallel focused reviewers.** Each reviewer covers part of the checklist with a smaller context. Largest possible saving, since cost grows with turns times context; narrower focus may also close the gap the second opinion fills today. The largest and least certain change.

**The owner chose approach 3,** with the mechanical changes of approach 1 that help every reviewer (tool grouping, shorter instruction files, no double reads, no duplicate report) included, so the split is measured at its real cost.

Three ways to check quality were considered.

1. **Full replay.** About 10 past branches, today's reviewer re-run as a baseline, each new version run twice. Firm numbers, but $500–800 for the project. Rejected by the owner as too expensive to use.
2. **Small replay.** Only the new reviewer, once, on 3–4 small past branches, compared with what those rounds cost and found at the time. About $20–40 per version. A rough signal.
3. **Real use only.** Ship behind an opt-in setting and read cost and findings from live sessions. No extra spend, but the signal arrives after a week or two, on different branches.

**The owner chose small replay plus real use:** the small replay guards each version before it is turned on, and real use makes the decisions.

**Quality bar, chosen by the owner: two gates, in order.**

1. **Ship gate.** The split becomes the default when, in real use, it finds at least what the single deep reviewer found, at lower cost. The second opinion keeps running.
2. **Second-opinion gate.** The second opinion's trigger is narrowed or removed only if the split round also finds what the first deep round and the second opinion found together; in real use, that shows as second opinions after split rounds finding little.

## Design

### Phase 1: shared efficiency changes, the small replay and the usage report

**Tool grouping.** `agents/reviewer.md` tells the reviewer to send independent reads, greps and `git` commands in one message as parallel tool calls: all the rule files at once, each batch of greps at once, the diff of several unrelated files at once. Steps that depend on an earlier result stay sequential.

**Reviewer-facing schema.** `finding-schema.md` is split. The reviewer reads only a short reviewer-facing file: the output block, the field rules, the coverage rule and the error format. The `review.json`, statuses, `status`, mode selection and command reference move to a contributor file (or `ARCHITECTURE.md`), which tests and the orchestrator still use. `CHECKLIST_PATH` and `SCHEMA_PATH` in the dispatch prompt point at the files the reviewer needs.

**Shorter reviewer definition.** `agents/reviewer.md` drops text the reviewer does not act on in the mode it runs. The JSON block's shape stays inlined (ARCHITECTURE.md explains why) and stays in step with the script through the existing test.

**No double reads.** For a file the change adds, the diff already holds the whole file; the reviewer reads it in full only when it needs line numbers the diff does not give.

**Short final message.** After `REPORT_PATH` is written and `CHECK_COMMAND` passes, the reviewer's final message is one line naming the file, not the report. The full report goes in the final message only when the reviewer could not write the file. Section 6.2 of the review skill keeps its fallback for that case.

**The usage report.** A contributor script under `plugins/branch-review/evals/usage/`, built from this analysis. It reads local Claude Code transcripts (reviewer subagents, found by their `agentType`) and the plugin's `review.json` files for a date range, and prints, per round kind and plugin version (and per deep-round shape once the split exists):

- rounds, cost, median and 90th-percentile cost, turns, peak context, tool calls per turn;
- findings opened by severity, invalid findings, duplicates;
- for second opinions, the findings they opened and the shape of the deep round before them.

Cost is read the same way as this analysis: tokens by kind per assistant message, deduplicated by message id, priced at list prices kept in one table in the script. It runs on local data only, writes nothing to the repo, and costs nothing to run. The two-week numbers in Why are its first output and the historical baseline.

**The small replay.** A contributor tool under `plugins/branch-review/evals/replay/`, run before a version is turned on:

- **Replay set.** 3 or 4 branches whose first deep round covered 5 to 15 changed files (median $4.49 then) and was followed by a second opinion, from SqlSource and this repo, reviewed by plugin 0.12 or later. The set is a local file listing, per branch, the repo, the round-1 `DIFF_FROM`/`DIFF_TO`, the base ref, the intent, the review state file and the round-1 transcript. It is not committed when it names local paths.
- **Run.** For each branch, create a scratch worktree at `DIFF_TO` and dispatch the reviewer version under test (from a given plugin checkout) once, with the inputs round 1 had, no open findings and no notes.
- **Baseline.** No re-run. The round-1 transcript gives the old cost, and `review.json` gives the known findings: round 1's, plus the second opinion's whose code existed at the round-1 snapshot (file and lines checked against `DIFF_TO`).
- **Matching.** A Sonnet judge labels each replay finding `matches <id>`, `new` or `duplicate of <replay finding>`, and for each `new` one says whether it holds and what it checked. The owner spot-checks the `new` verdicts.
- **Pass.** No known major missed that round 1 found, no judged-not-real finding, and lower cost than round 1 on at least all but one branch. A failure blocks the version; a pass only lets it into real use.
- **Spend.** About $20–40 a run; the tool prints its spend at the end.

**Phase 1 rollout.** Phase 1 changes no checks, so it ships as the default once its small replay passes. The usage report then compares its deep rounds with the two-week baseline; a drop in majors per deep round is investigated before Phase 2 is built on it.

### Phase 2: the split deep round

A deep round without the second-opinion focus dispatches three reviewers in parallel, each with a part of the checklist. Each one does the setup (checklist, its schema, baseline, rule files), reads the intent and the diff, and then hunts only its own part, in depth:

| Reviewer | Checklist categories | Required steps |
| --- | --- | --- |
| `code` | `logic`, `sibling-path`, `security`, `build-ci` | `broad-mappings`, `consumer-build` |
| `tests` | `tests` | `break-tests`, `measurement-tests` |
| `docs-intent` | `intent`, `conventions`, `docs` | `doc-grep`, `fulfilled-promises`, `related-records`, `real-system` |

Each report lists `coverage` for its own items; together the three cover all 16. A reviewer may still report a defect outside its part when it finds one; matching and deduplication handle the overlap. Carried `OPEN_FINDINGS` (a forced deep restart) are verified by the `code` reviewer only; the other two parts get the same list as already reported (like `PRIOR_FINDINGS` in a second opinion) and never report those defects again.

The second-opinion round, follow-ups and merge-checks keep one reviewer.

**Turning it on.** A new `userConfig` option, `deep_round`, `single` (default) or `split`, chooses the shape. The review skill passes it to `begin-round`, which stores the shape on the round; an unsaved option (the literal `${user_config.deep_round}`) means `single`. implementation-manager needs no change. `--full` and the other arguments keep their meaning in both shapes.

**Working trees.** Each part reviews its own detached worktree of `DIFF_TO`, created before dispatch and removed once the round is recorded; `REPO` is that worktree. Parallel parts then never see each other's scratch files, build output (`obj/`, `bin/`) or test runs, and each part's baseline and cleanup rules work as today. Containers a part starts carry the part's name, so two parts never clash on a name.

**Recording.** `record-findings` and `check-report` accept a round of several reports. Each report is checked on its own, against its own part's coverage items, and the round is recorded once all its parts are in. The round stores each part's summary, coverage and `not_checked`, and keeps these for the existing readers:

- `merge_risk`: the highest level among the parts, with that part's reason. This keeps the second-opinion trigger's meaning (any part rating moderate or high triggers it).
- `intent_check`: the union of the parts' lists.
- `coverage` and `not_checked`: the union, so the summary and `EARLIER_NOT_CHECKED` work as today.

Finding ids stay `R<round>-F<k>`, numbered across the parts in a fixed part order.

**A failed part.** A part that writes no usable report (after the existing write-from-message and re-emit steps) is dispatched again once, alone, in the same round. If it fails again, the round stays unrecorded, as a failed single-reviewer round does today. A split round is never recorded with fewer than 16 coverage items.

**Duplicates.** The orchestrator marks a finding that repeats another of the same round as a duplicate during triage, after checking both point at the same defect. The original is the one with the higher severity (the earlier id when they tie), so the `major-finding` trigger sees the higher severity. A duplicate leaves the fix loop without counting as invalid, and the summary lists it under the finding it repeats. From then on the duplicate takes no action of its own: its outcome is always its original's (verified, invalid after a withdrawal, escalated, deferred, accepted). Disputes and questions go only to the original's reviewer; when the duplicate's reviewer saw something the original's did not (another variant, another trigger), the orchestrator adds it to the original's resolution note so the next round checks it.

**Dispatch and disputes.** The review skill's section 5 dispatches the three reviewers in one message and keeps each agent id, so a dispute or question goes to the reviewer that reported the finding.

**Phase 2 rollout.** The split must pass its small replay. Then the owner sets `deep_round: split`, and it runs on real branches with the second opinion unchanged.

**Ship gate, in real use.** After at least 15 split first deep rounds, the usage report compares them with single first deep rounds (the two-week baseline and any Phase 1 rounds since), by change-size bucket where there are enough rounds:

- majors opened per first deep round at least the single rounds' rate (0.58 in the baseline);
- second opinions after split rounds opening majors at no more than the rate after single rounds (0.45 per second opinion in the baseline);
- invalid findings still rare (no more than one across the split rounds);
- median cost of a split first deep round at most 70% of a single one in the same size bucket.

When it passes, `split` becomes the default (the option stays, so `single` can be chosen). When it fails, the owner turns the option back to `single` and the result goes in `docs/tech-debt` with the report's numbers.

### Phase 3: the second-opinion decision

Once split is the default, the usage report keeps tracking second opinions after split rounds. After at least 15 of them:

- **They open majors at no more than a third of the baseline rate (0.15 per second opinion or less):** narrow the automatic trigger (for example to `high` merge risk only, or `--second-opinion` only). The exact change is designed then, from the report's numbers.
- **Otherwise:** the second opinion stays as it is, and the gap is recorded in `docs/tech-debt` with the numbers.

## Decided by the owner

- Approach 3, split deep round, with the shared mechanical changes included.
- Quality check: a small replay before each version is turned on, and real use for the decisions. No full replay; each check costs tens of dollars at most.
- Two gates in order: make the split the default only against the single deep reviewer; change the second opinion only against deep plus second opinion.

## My calls (owner to check)

- **The three-way split and its assignment of categories and steps** (table above). Grouped by what each reviewer must read and run: code paths, test runs in a repo copy, docs and real systems.
- **Second-opinion rounds stay single.** The second opinion's job is a whole-change look; splitting it is a later question.
- **Duplicates as their own outcome,** not `invalid`, so precision numbers stay honest. A duplicate follows its original's outcome, and the higher-severity finding is the original.
- **`merge_risk` as the maximum of the parts.** More parts can mean more moderate ratings and more second opinions; the usage report shows how often that happens.
- **Small replay set and pass rule:** 3–4 branches of 5–15 files, one run each, Sonnet judge, pass as above.
- **Real-use thresholds:** at least 15 rounds before each decision; the ship-gate rates above; a third of the baseline rate for narrowing the second opinion.
- **A `deep_round` user option** turns the split on, so implementation-manager runs it with no change.
- **Phase 1 ships as the default** after its small replay, before the split is built.
- **One worktree per part,** created before dispatch and removed after recording.
- **Carried findings go to all three parts,** with verdicts from `code` only.
- **A failed part is re-dispatched once, alone;** a split round is never recorded partly.

## Left to the builder

- The reviewer-facing schema file's name and how the dispatch prompt selects each part's checklist and coverage items (one checklist with a part filter, or per-part files).
- How `begin-round` and `record-findings` represent a multi-part round in `review.json`, and the migration for existing state (single-report rounds stay valid).
- How the replay dispatches reviewers (headless `claude -p` with the plugin loaded, or another harness), provided the reviewers run with the plugin's own agent definition and the same inputs a real round gets.
- The judge's prompt and output format.
- Whether small change sets skip the split. If the usage report shows the split costing more than one reviewer below some size, use one reviewer there and record the threshold.
- Version bumps, README and ARCHITECTURE updates, and tests for the multi-part recording and the usage report.
