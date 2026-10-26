# Branch review: fixes from the phase 8 run (v0.9)

Date: 2026-10-25
Status: draft, for the developer's review
Builds on: `2026-10-23-branch-review-recall-and-mechanics-design.md` (v0.8)

## Intent

branch-review 0.8.0 and 0.8.1 reviewed the eight child branches of SqlSource's "query generation epic phase 8" on 2026-10-24 and 25. All eight ran under the implementation-manager: 8.0 to 8.6, plus an unplanned per-column-types branch, PRs #129 to #139. That was 26 rounds:

- 10 deep, two of them second opinions;
- 1 merge-check;
- 15 follow-ups.

The rounds recorded 41 findings: 5 major and 36 minor. The orchestrators accepted all 41 and disputed none. Every PR passed CI on its first push, and no fix landed after a review converged. Precision held for the second release in a row.

The work that remains is narrower than in v0.8:

1. **Misses that a run would have caught.** Each defect that got past the review needed something to be run, and the reviewer had listed exactly that under "Not checked":
   - 8.0's spec fix said SqlClient rounds a decimal "to that scale". At scale 0 it throws "out of range" instead. 8.4's reviewer found this with a real SQL Server. 8.0's round 1 said "Not checked: the spike's answers against live containers".
   - 8.2's round 1 left a manual promise about domain parameters unchecked. The second opinion settled it with a three-minute `docker run postgres:18.6`.
   - 8.3's `AllocationTests` summed `GC.GetTotalAllocatedBytes`, which counts every thread. Test-platform telemetry made it flaky (fixed in #135). Round 1 had written "Not checked: whether the allocation margin holds on CI hardware". It had also seen a breakage the test should catch pass, and reported nothing. 8.4's twin test, built at the same time, already compared medians.
   - 8.4 added a SQL Server timeout test that later flaked on CI (#139, 542 ms, no timeout).
   - 8.1's generated methods raised CA2100 in a consumer built with analyzers. The repo's rule says "a compiler warning inside generated code is a bug". 8.3 found it.
   - 8.2 left SQLSRC232's help text saying "a later version may let a project declare a conversion", although 8.2 delivered exactly that. The doc grep looks for old statements, not for promises the change fulfils.
   - 8.2's round 1 stopped at 27 minutes with "I have enough evidence". It had never read the plan. The plan's assumption that keywords are written bare led the second opinion to R3-F1: `App.Id<System.Int64>` silently never matches its converters.
2. **Fix churn the loop caused.** In 8.2, rounds 4 to 6 (about 22 minutes) each found a smaller defect in the previous fix:
   - R3-F1's `fix` offered two options. The orchestrator chose the narrow one without escalating, which the skill forbids.
   - The fixer flagged the next gap (`System.Nullable<T>` spelled in full). Nobody acted on it, and R4 found it again.
   - The R4 fix added a guard with no test, and R5 caught that with a mutation.
3. **Timing.**
   - 8.4's owner questions were known at 16:03. They reached the owner at 16:46, after R2, R3 and a second opinion. The owner answered within 15 minutes.
   - That second opinion found nothing. It also ran before the owner's answer added the branch's largest post-review code change.
   - In 8.2, a follow-up and the second opinion ran on the same snapshot, so the follow-up was wasted.
4. **Mechanics.**
   - Follow-up reviewers on Sonnet skipped writing `REPORT_PATH` in 7 of 15 rounds, so `record-findings` failed with ENOENT each time. 8.3's R2 also left out `summary`.
   - Three sessions put `--state-dir` before the subcommand, which argparse rejects.
   - 8.1 guessed a snapshot sha.
   - 8.3's `summary.md` went stale after an extra round.
   - PR "Review notes" were hand-assembled: four or five headed "None." sections, fixed findings shown only as counts, and wrong round counts in #136 and #138.
5. **Statuses that misdescribe a decision.**
   - The owner chose to accept 8.4's R1-F5 as a known issue ("document this as a known issue rather than tech debt"), and it was recorded as `deferred`.
   - 8.7's orchestrator marked five behavior changes as intended without asking the owner. One of them turned a former error (`int4=Guid`, SQLSRC014) into accepted input.

Branch review took about 3.8 h of the epic's 24.7 h. The per-task reviewers inside each child mostly read diffs without running anything. Branch review's value was the real-database probes and the breakages run on purpose. This release makes those required, not something a reviewer does when it thinks of it.

Evidence is in the transcripts of the eight child sessions and the manager session (`~/.claude/projects/*SqlSource*`), and in the review state under `~/.claude/plugins/data/branch-review-mbcrawfo-skills/mbcrawfo__sqlsource/claude__qg-*`.

## Goals

- A deep round runs the checks a misses analysis keeps pointing at: real-system probes for driver and database claims, repeated runs of tests that measure process-wide state, a consumer build of generated output, and a check of the plan's assumptions.
- A deep round covers every checklist category and says what it did for each. Gaps are visible in the report and carried to the next deep round.
- A fix closes the whole class of its defect at once, and every guard it adds has a test.
- Choices that belong to the developer reach them as soon as triage finds them, and the script refuses to mark such a finding fixed without the developer's answer or a quote from the intent.
- The second opinion runs once, at the right time: when nothing is open, after the developer's decisions, and in place of a follow-up it would duplicate.
- The loop's mechanics do not fail on their common paths: the report file, argument order, the snapshot, the summary.
- The PR description gets a generated, ready-to-paste "Review notes" block.

## Non-goals

- Changes to the implementation-manager plugin's behavior. They belong to the session "Implementation manager skill performance review". The list for it is under "Follow-up for the implementation-manager". One exception, the developer's decision: this change updates the two implementation-manager eval fixtures that record a deep report through `record-findings` (`evals/push-gate/scaffold.sh` and `evals/push-queue/scaffold.sh`). Each report gains `coverage` and `not_checked`, so the fixtures pass A6's check. Nothing else in that plugin changes.
- Rules specific to SqlSource, such as its analyzer flags or its consumer project. The reviewer reads those from the repo's AGENTS.md. The lines SqlSource should add are listed at the end, for its owner.
- New second-opinion triggers. Size triggers stay dropped (v0.8 decision).
- Review of spec-only PRs against an epic outline. The 8.4 scope contradiction came from spec PR #128. That is planning work, not branch review.

## Anti-goals

- Noise. 41 of 41 findings were real; the stricter steps must still produce evidence, not speculation. A required step that finds nothing produces a coverage line, not a finding.
- Longer reviews for no gain. The new steps are conditional: a probe only when the change makes a driver or database claim and the repo pins an image; repeated runs only for tests that measure process-wide state; a consumer build only when the change emits code.
- Babysitting. Nothing new requires a human answer except what is already the developer's to decide.

## Constraints

- Plugin conventions from this repo's AGENTS.md:
  - Python 3.9+ standard library;
  - tests in `plugins/branch-review/tests/`;
  - ruff clean;
  - `claude plugin validate` clean;
  - `version` bumped to 0.9.0;
  - the eval case updated and run.
- Backward compatible with v0.8 state:
  - a `review.json` written by 0.8 loads, and its rounds and findings keep their meaning;
  - its `deferred` findings stay `deferred`, because there is no way to tell which were acceptances;
  - its reports, which have no `coverage` or `not_checked`, are valid.
- `branch-review:check` keeps its contract: covered only when a clean review has seen everything in the commit. The new status `accepted` does not block `covered`, just as `deferred` does not.
- The implementation-manager reads `rs covered` and copies the summary's section headings: "Escalated", "Deferred", "Invalid", "Outside the change", "Intent check". Those headings stay in `summary.md`, so the current manager keeps working. The new Review notes block is an addition.
- The reviewer keeps its tool list (Read, Grep, Glob, Bash; no Write or Edit) and never modifies tracked files.

## Approaches

1. **One release (0.9.0), with the script enforcing the rules the transcripts show orchestrators breaking. (Recommended; my call, to confirm.)** Prose-only rules were broken in this run. The alternatives rule (8.2 R3-F1) and the "your own check" rule (8.1, 8.2) are examples. So those two get a check in the script: `set-status fixed` on a finding with options needs an intent quote that the script verifies. The rest is reviewer and checklist prose, plus small script features. Cost: one larger change.
2. **Two releases: mechanics as 0.8.2 now, recall and triage as 0.9 later.** The mechanics fixes are cheap and stop recurring errors right away. Rejected as the default: the summary, status and schema changes overlap the mechanics changes (the Review notes block, `not_checked`, `accepted`), so a split means touching the same code twice. The plan may still land C1 to C4 as their own PR first.
3. **Prose only, no script changes.** Least code. Rejected: it repeats the failure that v0.8 already recorded, where written rules are followed unevenly. The escalation rule this run broke was already in the text.

## Design

### A. Reviewer recall

These go in `agents/reviewer.md` and `skills/review/reviewer-checklist.md`. Each comes from a miss in the data.

#### A1. Real-system checks are not optional

- **When the rule applies.** The change states how a driver, database, runtime or external tool behaves, in code, a comment, a doc or a spec. Examples: "SqlClient rounds to that scale", "a parameter compared with a domain column is converted", "Npgsql 9 reads cube".
- **What the reviewer must do.** When the repo pins an image or fixture for that system and Docker (or the system) is available, the reviewer probes the claim with a scratch program or a query against it.
- **Boundary values.** A claim stated for every value of a kind, from one probe, is tried at the edges: zero, the maximum, null, empty, the smallest unit (scale 0, length 0, the earliest version the repo supports).
- **Not checked.** A probe may go under `not_checked` (A6) only with a reason that rules it out, such as "Docker is not available" or "no pinned image for X". "Not run" or "out of time" is not a reason.
- **Where it goes in the checklist.** This tightens the deep step "Try each doc promise against the real system" and checklist §7's "Try doc promises". It also adds a line under `intent`: a claim the change relies on is a promise, whether or not it is user-facing.
- Evidence: 8.0 R1 → 8.4 R1-F1; 8.2 R1 → R3-F2.

#### A2. Tests that measure process-wide state

- **Where it goes.** A new subsection of checklist §4 (`tests`). It keeps the category, so the schema enum does not change.
- **Which tests.** A test that asserts on something other threads, the runtime or the machine can change: allocation counters, wall-clock time, timeouts, GC counts, process-wide caches, global counters, port or file availability.
- **What the reviewer does with each one the change adds or rewrites:**
  - names what else can move the measurement (the test platform's telemetry, driver background threads, the GC, a loaded CI runner);
  - runs the test inside the full suite once, and on its own five times;
  - reports a failure, or a spread close to the bound, as a `tests` finding;
  - reports a breakage the test should catch that still passes, as the "break it on purpose" rule already requires.
- Evidence: #135 (8.3), the #139 timeout flake (8.4), and 8.3 R1's own surviving breakage (`TypedValue` → `Value`).

#### A3. Check the plan's assumptions

- **What counts as a related record.** Deep step 3 also includes the plan or spec for this change. That is a changed file under the repo's plans or specs directory, or a document the intent names.
- **What the reviewer checks.** Each assumption the plan states about existing code: a format, a spelling, an invariant, a "this is always bare", a "callers never pass null". It checks each against that code.
- **When an assumption fails.** That is a lead to trace through the changed code, not a finding about the plan. Plans are records of intent.
- Evidence: 8.2 R3-F1, whose source was the plan's Global Constraints.

#### A4. Build what users build

- **When the rule applies.** The change emits code or artifacts that another project compiles or consumes: a source generator, templates, a code-gen tool, a published package.
- **What the reviewer does.** For each new shape of output, it builds a consumer the way a user would, with the warnings and analyzers a typical consumer turns on. It uses the repo's own consumer project when there is one; otherwise it traces through the code.
- **Findings.** A warning or error that the repo's rules forbid in output is a finding (`build-ci` or `conventions`, with the rule quoted).
- Evidence: CA2100 from 8.1, found by 8.3.

#### A5. Promises the change fulfils

- **Where it goes.** Checklist §7's grep rule gains a second search.
- **The search.** For the feature the change delivers, `git grep` the living docs and diagnostics text for forward-looking statements about it: "a later version", "not yet", "planned", "in the future", "coming", "is not supported", with the feature's key words. Read around every hit, untruncated.
- **Findings.** A statement the change makes untrue is a `docs` finding.
- Evidence: SQLSRC232 after 8.2, found by 8.6's planned docs pass.

#### A6. Coverage and Not checked, in the JSON

The finding schema's report block gains two keys. Both are required in deep mode; follow-up and merge-check reports may leave out `coverage`.

- `coverage`: one entry for each checklist category plus each required step (break tests, doc grep, fulfilled-promise grep, doc promises against the real system, related records and plan assumptions, broad mappings, measurement tests, consumer build). Each entry is `{"item": "<category or step>", "done": "<one line: what was run or read, or 'not applicable: <why>'>"}`.
- `not_checked`: a list of `{"what": "...", "reason": "..."}`. It is the JSON form of the prose's `Not checked:` line, which stays.

Script behavior:

- `record-findings` refuses a deep report whose `coverage` lacks an entry for a listed item. The error names the missing items, and the skill's re-emit step applies. The check applies only to rounds whose `plugin_version` is 0.9.0 or later, so a round begun under 0.8 and recorded under 0.9 is still accepted.
- The list of required items lives in one place, in `review_state.py`, and `finding-schema.md` documents it.
- The summary prints the latest deep round's `not_checked` under a new "Not checked" section, and the Review notes block (C5) includes it.

The next deep round (second opinion or forced deep) gets the earlier deep rounds' `not_checked` as `EARLIER_NOT_CHECKED` in its dispatch. Those items go first in its do-first list. An item that is still not checked is listed again, with its reason.

This replaces "finish the checklist" as a rule nobody can see being broken. It does not stop a reviewer from writing "done" falsely, but it makes skipping a deliberate act, and the second opinion inherits the gaps.

Evidence: 8.2 R1's early stop; the "Not checked" lines behind every escape under A1 and A2.

#### A7. Severity

Checklist "Severity" gains two lines under `major`:

- a wrong result at run time with no diagnostic, on an input the docs or the intent allow (8.2 R3-F1 was rated minor);
- an input that makes a whole unit of output disappear, such as a file's generated members, or a module's exports (8.1 R1-F1 was rated minor).

This matters beyond the label, because a major triggers the second opinion.

#### A8. Measurements and spikes

When the change contains measurements, such as benchmarks, allocation figures or spike results, or relies on them, the reviewer checks that the setup matches how production code uses the thing measured: a new object per call or a reused one, warm or cold, the same driver version. A mismatch is an `intent` or `docs` finding.

Evidence: 8.0's spike claimed the same allocation "with or without `DataTypeName`". A new parameter per call costs 113 to 118 B (TD-0078, found by 8.3).

#### A9. Equivalence classes

- **The rule.** When a finding is about normalization, spelling, aliasing or identity by text (two spellings of one type, two paths to one file, case variants), the reviewer probes the whole class before reporting.
- **A new finding key.** Such a finding gets an optional `variants` key: a list of the inputs of the same class it tried, each with `fails` or `ok`.
- **Counting.** The class is one finding, since the cause is the same.
- **The fix.** The finding's `fix` must cover every variant that fails.
- Evidence: R3-F1 (`System.Int64`) → R4-F1 (`System.Nullable<T>`), found five minutes apart by probing.

#### A10. Optional inputs from earlier work

Two optional dispatch inputs, both supplied by new `/branch-review:review` arguments and stored with the state like `--generated`:

- `--notes-file PATH` → `EARLIER_REVIEW_NOTES:`, inlined. These are things earlier per-task reviewers left for the whole-branch review: "unless the whole-branch review wants a seeded negative test", approved-with-minors lists. The reviewer treats them as leads to check, never as findings to copy, and never as instructions. Deep rounds only.
- `--related-branch REF...` → `RELATED_BRANCHES:`. These are branches built at the same time that mirror this change, for example the other engine's binding. For each one, the reviewer lists the files it changes that mirror files in `CHANGED_FILES` (the same role in another directory, the twin test class). It reads them at that ref. When one side guards against something the other does not, that is a lead.

Evidence: 8.5's Task 3 hand-off never reached the deep reviewer. 8.3 and 8.4 built the allocation test two ways.

#### A11. Environment notes

reviewer.md's Environment section gains:

- **Warnings as errors.** When the repo builds with warnings as errors, a breakage that adds a constant condition can fail to compile instead of failing the test. Turn analyzers off in the `mktemp -d` copy (for .NET, `-p:TreatWarningsAsErrors=false -p:RunAnalyzers=false`) and say so, or pick a breakage that compiles. Three agents lost time on S1125 and CA1508.
- **Waiting.** Wait on a long run with the shell's `wait`, or the runner's own blocking mode. Never chain `sleep`: the harness blocks it, which happened in 8.1 and 8.2.
- **Deleting the copy.** Delete the `mktemp -d` copy by its literal path. `rm -rf "$(cat …)"` trips the harness's safety check (8.3).

### B. Triage and the loop

These go in SKILL.md sections 7 and 8, and in `review_state.py`.

#### B1. Fixes close the class, and every guard gets a test

Section 7's **Valid** steps gain three rules. They apply whether the orchestrator fixes the finding itself or hands it to a fixer subagent, whose brief must carry them word for word:

- **Fix the class.** When the finding has `variants`, or is about normalization, spelling or identity by text, list the class in the fix, and add a test row per variant.
- **Test every guard.** Every condition, guard or branch the fix adds gets a test that fails without it. Show that in the note, the same way as for the main regression test.
- **Act on concerns now.** A concern the fixer reports, such as "X is still not handled", is fixed before the next `begin-round`, or it is written into the finding's resolution note, so the next reviewer sees it as a claim to check. It is never dropped.

Evidence: 8.2 R3-F1 → R4-F1 → R5-F1.

#### B2. Alternatives need the developer or the intent, and the script checks it

- **The schema.** It gains an optional `fix_options`: a list of two or more alternative fixes. When the reviewer's fix offers a choice, it puts the choice there. `fix` holds its recommendation.
- **The script check.** `set-status --status fixed` on a finding with `fix_options` is refused, unless either holds:
  - the finding has a developer decision recorded by `resolve --decision fix`;
  - the call passes `--intent-quote-file PATH` whose text, after whitespace is normalized, occurs in the intent stored on the round that opened the finding (`round_opened`). The note records the quote.
- **Intent per round.** For that check, `begin-round` stores the intent on the round object as `intent`, as well as at the top level. The check uses the opening round's copy, never the current one. Otherwise an orchestrator could write the option it picked into the intent and quote it. v0.8 rounds have no copy, but they also have no `fix_options`, so no fallback is needed.
- **Keeping the decision.** `resolve` stores the developer's decision in a field of its own on the finding, `decision: {decision, note, by, at}`, which `set-status` does not overwrite. Today `set-status` replaces `resolution` whole, which would erase the record the check needs.
- **The refusal message** names both ways out.
- **SKILL.md.** The Needs a decision text and the red-flags row point at this check.
- **Reviewer text.** reviewer.md and the schema tell the reviewer to use `fix_options`, not prose alternatives inside `fix`.

Evidence: 8.2 R3-F1, whose fix offered (a) canonical display or (b) keyword normalization. The orchestrator picked (b) silently.

#### B3. Raise decisions at once

The moment triage escalates a finding, the orchestrator raises the decision. It does not wait for convergence:

- If its own instructions say how to put a question to the developer (an orchestrator's request channel, say), it uses that now.
- Otherwise it prints the decision now, in the form of section 9's escalated list.

Then it carries on with the other findings and rounds. Section 9 still lists every escalation at the end.

Evidence: 8.4's 43-minute hold.

#### B4. When the second opinion runs

Two changes to `second_opinion_due` and `status`. The triggers themselves are unchanged.

- **Early.** The second opinion is due at the first `status` after which no finding is `open` or `still-open`, not only at convergence. Claimed fixes (`fixed`) are carried into it as `OPEN_FINDINGS`, and it gives them verdicts the way a deep round does for carried findings. So when round 1 has majors and every finding was fixed, round 2 is the second opinion, with no follow-up before it. `status` returns `reason: second-opinion` in that case, as it does today at convergence.
- **Held for decisions.** While any finding is `escalated`, the second opinion is not due. `status` returns `continue` per the other rules.
- **Stopping with decisions pending.** Whenever the loop would stop and any finding is `escalated`, `status` returns `continue: false` with `reason: awaiting-decisions`, in place of `converged`. `second_opinion_held` says whether a second opinion is waiting behind the decisions: `true` when a trigger fired and none has run, `false` otherwise. SKILL.md sections 8 and 9 treat `awaiting-decisions` like `converged`, with the decisions listed first.
- **After the developer answers.** `locate` also checks `second_opinion_due`. When one is due, `locate` returns `mode: deep`, `focus: second-opinion` and `reason: second-opinion` in place of a follow-up, carrying `open_findings`, so that `fixed` findings get verdicts. So a `--resolve` run whose fix leaves nothing `open` goes straight to the second opinion, with no follow-up first. `locate` reasons that force a deep round (`snapshot-missing`, `history-rewritten`, `paths-changed`) or a merge-check still win, as in v0.8 section 8. SKILL.md item 4.2 passes `--focus` from `locate` when it is present.
- **`covered`.** It already reports `escalated`, so its verdict does not change. Its output gains `second_opinion_held` (B4) and `behavior_to_confirm` (a count, B7), so that the implementation-manager can act on them through `branch-review:check` without another release. The check skill's verdict line mentions them when they are non-zero.
- **Cap.** The rule that a due second opinion may run past the cap stays as it is.

The v0.8 rule "escalated findings do not block it" is reversed, because the developer's answer can add the largest code of the branch. In 8.4 it did, and the second opinion never saw it.

Evidence: 8.2's R2 and R3 on one snapshot; 8.4's R4 before the UDT decision.

#### B5. The check behind "Checked:" is the orchestrator's own

Section 7's rule gains a sentence. The check is something the orchestrator ran or read itself, or the fail-first output of a fixer it dispatched. "The review's evidence", or a mutation the reviewer ran, is not a check.

The red-flags table gains: "The reviewer's evidence is convincing, so I'll cite it as my check." → "Run or read something yourself; the reviewer's evidence is the claim being checked."

Evidence: 8.1's F2 note ("Checked: the review's mutation evidence"); 8.2's templated notes.

#### B6. OPEN_FINDINGS as `locate` printed it

Section 5 already says OPEN_FINDINGS is the `open_findings` list. It gains a red-flags row: "OPEN_FINDINGS is long, so I'll pass a shorter version." → "Pass it whole. The trigger and evidence are what the reviewer re-runs."

Evidence: 8.7's `open2-short.json`.

#### B7. Behavior changes users already rely on

- **The rule.** An `unmentioned_behavior` item that changes behavior existing users rely on may not be folded into the intent by the orchestrator. Examples: an input that used to fail now passes, output that changes for every project, a diagnostic removed. The one exception is an intent that already says so; then the note quotes it.
- **What happens instead.** The orchestrator records it with a new command, `rs confirm-behavior --text-file <tmp>/note.md`. That adds it to a top-level `behavior_to_confirm` list.
- **Where it shows.** The summary lists the list under "Behavior changes to confirm", and the Review notes block includes it.
- **Not blocking.** It does not block `covered`. It is not a finding, and blocking would force supervision for something the PR reader can judge.
- **Other items.** Items that are intended and harmless are handled as today: into the intent or the PR description.

Evidence: 8.7 folded `int4=Guid` (formerly SQLSRC014/111) into the intent as "intended".

#### B8. `accepted`: the developer's "won't fix"

- **`resolve`.** It gains `--decision accept`, for "leave it as it is, documented or not". It sets status `accepted` and `round_closed`. `defer` keeps meaning "fix later".
- **Summary.** A new section "Accepted", after "Deferred".
- **Other commands.** `covered`, `status` and `list` treat `accepted` like `deferred`: terminal and not blocking.
- **SKILL.md.** Item 2.5 maps a note that says to keep the behavior, document it as a known issue, or not fix it, to `accept`. It asks when the direction is unclear, as it does now.

Evidence: 8.4 R1-F5.

#### B9. VALIDATED from a run the orchestrator has the output of

Section 5's rule "you ran the repo's full test suite yourself" becomes: "you have the output of a full run of the suite: your own, or a subagent's report that quotes the command, the pass result and the commit". The reviewer's check that the commit's tree equals `DIFF_TO`'s tree stays, and it is what protects the skip.

Evidence: 8.5 and 8.7 re-ran full suites that implementer subagents had just run on the same commit.

#### B10. Unmentioned behavior in the summary

The summary's "Behavior the intent does not mention" list takes items from the latest deep round on, the same as unmet claims, instead of from every round.

- A deep round re-derives the list over the whole change at its snapshot.
- Items absorbed into the intent, or no longer true, drop out.
- Items folded in by B7's rule never drop out, because they are in `behavior_to_confirm`.

Evidence: 8.4's summary listed nine items, one of them no longer true, and the PR notes were corrected by hand.

### C. Mechanics

#### C1. Every mode ends by writing the report

- **reviewer.md.** Each process section (deep, second opinion, follow-up, merge-check) ends with the same numbered last step: "Write the report to `REPORT_PATH` (see Output), then run `tail -n 3` on it, and confirm the closing fence." The Output section stays the place that says how.
- **Follow-up setup.** The follow-up section's step 1 spells out what setup is: read the schema and the checklist in full. This is because 8.3 R2 left out `summary` after reading the schema only when the error came.
- **The fallback.** SKILL.md item 6.2 stays as it is, as the fallback.

The likely cause is that the follow-up section's "Report" step never mentions the file, and the instruction sits far below, in Output. Sonnet in a one-minute round follows the nearest steps.

Evidence: 7 of 15 follow-ups: 8.0 R4, 8.2 R2, 8.3 R2 and R3, 8.4 R3, 8.5 R2, 8.6 R2.

#### C2. Arguments in any order

- **The change.** `review_state.py` accepts `--state-dir` and `--repo` before the subcommand as well as after it. In argparse, both the top-level parser and the subcommands' `common` parent declare them with `default=argparse.SUPPRESS`, and the defaults (`DEFAULT_STATE_DIR`, `.`) are applied after parsing. A default on the `common` parent silently replaces a value given before the subcommand; the builder check confirmed this with `parse_args(["--state-dir", "BEFORE", "x"])`. When both positions are given, the one after the subcommand wins.
- **`rs`.** It already detects them in any position.
- Evidence: 8.2, 8.3 and 8.1 got `invalid choice: '/Users/…/branch-review-mbcrawfo-skills'`.

#### C3. `begin-round` takes its own snapshot

- **The change.** `begin-round --snapshot` becomes optional. Without it, `begin-round` takes the snapshot itself, as `rs snapshot` does, and returns it as `snapshot`.
- **SKILL.md.** Item 4.1 is dropped, and item 4.2 reads the snapshot from `begin-round`'s output. `rs snapshot` stays for other callers.
- Evidence: 8.1's guessed sha (`snapshot not found: 87166dae…`); 8.7's hand-built `ff038573$(git rev-parse HEAD | cut -c9-)`.

#### C4. The summary is never stale

- **Rewriting.** `record-findings`, `set-status`, `resolve`, `discuss` and `confirm-behavior` rewrite `summary.md` after they save the state. `rs summary` still prints it and returns its path.
- **Header.** The summary's header gains the plugin version that wrote it.
- **`locate`.** Its output gains `plugin_version`, so an orchestrator can see which copy of the plugin it is running.
- Evidence: 8.3's summary said "2 rounds" after a third; the manager kept the 0.8.0 cache path while children ran 0.8.1.

#### C5. A generated Review notes block

`render_summary` writes a second file, `review-notes.md`. `rs summary` returns its text and path as `review_notes` and `review_notes_path`. The block:

- **Heading.** `## Review notes`.
- **Line 1.** One generated line built from review.json: the number of rounds by mode and model, whether a second opinion ran, and the latest deep round's merge risk level. Nothing in it is hand-written.
- **Sections.** Only the non-empty ones, in this order: Escalated, Behavior changes to confirm, Deferred, Accepted, Invalid, Outside the change, Intent check, Not checked.
- **Empty sections.** One line: `Nothing to report under: <names>.`
- **Fixed findings.** One line each, `id, severity, title`, under "Found and fixed".

SKILL.md section 9 tells the invoker to paste `review_notes` into the PR description as it is, and not to retype or summarize it. `summary.md` keeps its current sections and headings, which the manager depends on.

Evidence: hand-written notes in #136 and #138; "None." noise in every PR; fixed findings shown as counts in five of eight.

#### C6. Recording a decision outside the skill

The README and `skills/check/SKILL.md` gain one line: an agent may record the developer's decision with `rs resolve` directly. The decision takes effect at the next review run, which must still run before a push (a `fix` decision reopens the finding). SKILL.md section 2.5 is unchanged.

Evidence: 8.0 and 8.4 children did this, with the right outcome.

### Arguments

New arguments to `/branch-review:review`:

- `--notes-file PATH`: earlier reviewers' notes for the whole-branch review (A10). Read once, inlined into deep dispatches, and stored with the state.
- `--related-branch REF...`: branches that mirror this change (A10). Stored with the state; `--related-branch .` clears them.

All v0.8 arguments are unchanged.

### State and output changes

- **Reviewer report block:**
  - `coverage` and `not_checked` (A6);
  - per finding, optional `variants` (A9) and `fix_options` (B2).
- **Round object:** stores `coverage`, `not_checked` and `intent` (B2).
- **Finding:** gains `decision` (B2), which `set-status` keeps.
- **`locate`:** may return `focus: second-opinion` with `reason: second-opinion` (B4).
- **`covered`:** gains `second_opinion_held` and `behavior_to_confirm` (a count).
- **Top level:**
  - `behavior_to_confirm` (B7);
  - `notes` (the text of `--notes-file`);
  - `related_branches`.
- **Statuses:** gain `accepted`.
- **`resolve`:** `--decision` gains `accept`.
- **`set-status`:** gains `--intent-quote-file`.
- **New command:** `confirm-behavior --text-file PATH`.
- **`status`:** the `reason` values gain `awaiting-decisions`; the output gains `second_opinion_held`.
- **`begin-round`:** `--snapshot` is optional.
- **`locate`:** gains `plugin_version`.
- **`summary`:** gains `review_notes` and `review_notes_path`.
- **Global:** `--state-dir` and `--repo` are accepted before the subcommand.
- **Docs:** `finding-schema.md` documents all of these.

## Testing

- **Unit tests** in `plugins/branch-review/tests/`:
  - `record-findings` refuses a deep report that is missing coverage items, and accepts follow-up reports without `coverage`;
  - `variants`, `fix_options` and `not_checked` are stored;
  - `set-status fixed` on a finding with `fix_options`:
    - is refused with no decision or quote;
    - is accepted after `resolve --decision fix`;
    - is accepted with a quote found in the intent;
    - is refused with a quote that is not in the intent;
  - the second opinion:
    - is due once nothing is open (with fixed findings carried);
    - is held while anything is escalated, with `awaiting-decisions` and `second_opinion_held`;
    - becomes due after a resolve, and `locate` then returns it in place of a follow-up;
    - `awaiting-decisions` is returned with `second_opinion_held` both `true` and `false`;
  - the intent-quote check uses the opening round's intent, not a later edit;
  - `set-status` keeps the finding's `decision`;
  - `status` and `covered` still agree on whether it is due;
  - `accepted` through `resolve`, `covered`, `status`, `list` and the summary;
  - `confirm-behavior` and its summary section;
  - unmentioned behavior is taken from the latest deep round on;
  - `--state-dir` and `--repo` before the subcommand, and after it, and the after value wins;
  - `begin-round` without `--snapshot`;
  - the summary is rewritten by each state-changing command;
  - the Review notes block: the generated line, empty sections collapsed, fixed findings listed;
  - `plugin_version` in `locate`;
  - v0.8 state loads, and its reports without the new keys are valid.
- **Eval.** The `review-loop` eval gains a grader: every round's file, follow-ups included, was written by the reviewer, not by the orchestrator's fallback. If the eval budget allows, a second case covers a finding with `fix_options`, which must end escalated, or fixed with an intent quote.
- **A field check after release.** On the next SqlSource multi-branch run, count:
  - fallbacks to item 6.2;
  - findings from second opinions;
  - `not_checked` entries that name a probe or a run.

  Compare them with this run.

## Left to the builder

- **Wording** in SKILL.md, reviewer.md and the checklist, to the superpowers:writing-skills standard. Keep SKILL.md near its current length by moving detail into the checklist and the schema.
- **The coverage list:** the exact item names and their spelling in JSON.
- **Repeat counts** for A2: five runs alone and one in the suite is the default. The builder may tune it if a suite is slow.
- **Classifying a resolve note** as `accept` or `defer`: the exact wording SKILL.md uses.
- **Splitting PRs.** Whether C1 to C4 land as their own PR first (Approach 2's benefit).
- **Whitespace** normalization for `--intent-quote-file`: collapse runs of whitespace, and compare case-sensitively.

## Decided by the developer

- Scope: everything in the phase 8 analysis that belongs to branch-review, small items included.
- implementation-manager changes are out of scope. They go to the session that reported its own findings.
- The exception: this change updates the two implementation-manager eval fixtures that A6's coverage check would otherwise break.

## My calls (made without the developer)

- **Approach 1:** one release with script checks.
- **Required coverage, in the JSON** (A6), enforced by `record-findings` for deep reports.
- **`fix_options`, and the intent-quote check** that `set-status` makes (B2).
- **Second-opinion timing** (B4):
  - it runs at the first point where nothing is open, in place of a follow-up;
  - it waits for decisions. This reverses the v0.8 rule "escalated findings do not block it".
- **`accepted`** as a separate status from `deferred`. v0.8 `deferred` findings are not migrated.
- **`behavior_to_confirm`**, and the rule that such items do not block `covered`.
- **Unmentioned behavior in the summary** is taken from the latest deep round on (B10).
- **Measurement tests** go under the existing `tests` category, not a new one.
- **Two new inputs**, `--notes-file` and `--related-branch`, as arguments stored with the state.
- **`review-notes.md`** as a second generated file, keeping `summary.md`'s headings for the current manager.
- **Intent stored per round**, with the quote check reading the opening round's copy; the developer's decision kept in its own field (B2).
- **`awaiting-decisions`** whenever the loop stops with escalations, whether or not a second opinion is held (B4).
- **`locate` returns a due second opinion** in place of a follow-up (B4).
- **`covered` exposes** `second_opinion_held` and the `behavior_to_confirm` count, without changing its verdict.
- **No change to the triggers.** Making `merge-risk` follow a follow-up's later, lower rating was considered and rejected: a follow-up sees only the delta, and in 8.4 the `major-finding` trigger would have fired anyway.

## Follow-up for the implementation-manager

These came out of the same analysis. They are for the session "Implementation manager skill performance review", not for this change:

- **Review notes.** Paste `review_notes` (C5) into PR descriptions, instead of copying sections.
- **New states.**
  - Handle `second_opinion_held` (B4) from `covered`.
  - Handle `accepted` (B8), and the `behavior_to_confirm` count from `covered` in the push decision.
- **Relay early decisions.** Relay a child's early decision question (B3) to the owner as soon as it arrives.
- **`covered` calls.**
  - Pass the child's own worktree as `--repo`. In this run it pointed at unrelated worktrees.
  - Never substitute a `--state-dir` of its own.
  - Re-resolve the plugin path when `plugin_version` (C4) changes mid-run.
- **Sibling branches.** Pass `--related-branch` to children whose sub-phases are built in parallel and mirror each other, such as 8.3 and 8.4.
- **Earlier reviews.** Pass per-task reviewers' leftover notes as `--notes-file`.

## For SqlSource's AGENTS.md (its owner's call)

- Build mutants with `-p:TreatWarningsAsErrors=false -p:RunAnalyzers=false`.
- Compile generated methods in a consumer project with the .NET analyzers on, when reviewing generator output.
- The pinned database images and the command that starts them, so A1's probes start the right versions.
