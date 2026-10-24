# Branch review: recall and mechanics (v0.8)

Date: 2026-10-23
Status: approved 2026-10-23
Builds on: `2026-10-22-branch-review-skill-design.md`

## Intent

branch-review v0.7 ran on 15 tech-debt branches of mbcrawfo/SqlSource on 2026-10-23, all under the implementation-manager. It reported 45 findings (7 major, 36 minor, 2 outside the change), and every one of them was real. The orchestrators accepted all 45; nothing was disputed or found invalid. CI was green, the PRs got no human comments, and no fix commits followed convergence. Precision is not the problem.

Recall is. A second deep round ran only by accident, when a merge with main conflicted. In 3 of the 5 sessions where that happened, it found problems that already existed in round 1's snapshot:

- TD-0049: a major. Every SQLSTATE 08P01 was read as "pooler statement mode". Round 1 found one major and stopped hunting.
- TD-0051: six minors. Round 1 never broke the new tests on purpose, never read the tech-debt item that described the risk, and never tried the manual's GROUP BY promise on a server.
- TD-0032: two doc findings. The reviewer printed 128 KB of AGENTS.md files at once, the output went to a file, and nobody read it.

Branches without such an accident probably shipped with misses of the same kind.

The loop's mechanics also tripped the orchestrators, in roughly these numbers:

- zsh did not split a `$RS` command prefix (about 10 of 15 sessions).
- Orchestrators retyped or condensed reviewer reports instead of saving them word for word (most sessions). Placeholder files then broke the Write tool and `record-findings`.
- `status` kept the loop going when every new finding was escalated.
- The six-round cap counted forced restarts after merges.
- Outside findings showed up as "Deferred", which alarmed a manager whose repo forbids deferring.
- Full deep rounds ran over 44 and 101 files to check one-file merge conflicts, and found nothing.

Any agent can invoke this skill: an interactive session with a person waiting, a plain coding agent, or an orchestrator such as the implementation-manager. The defaults must work with nobody supervising, and must keep the cost reasonable for someone waiting on the loop.

Evidence for everything above is in the session analysis of 2026-10-23 (transcripts of the TD-0021 to TD-0075 sessions, review state under `~/.claude/plugins/data/branch-review-mbcrawfo-skills/mbcrawfo__sqlsource/`).

## Goals

- A deep review finds what a second deep look would have found: fewer misses in round 1, and a deliberate second deep review when the change is risky or complex.
- The second opinion is decided by the state script, from risk and complexity signals, or requested by the invoker. It behaves the same for every invoker.
- The loop runs without the orchestrator improvising: no shell-variable prefixes, no retyped reports, no empty rounds just to reach the summary, no cap exhausted by merge restarts.
- A merge conflict with the base is checked at the size of the conflict, not with a full deep round.
- The summary carries everything the PR description needs, including the intent check.
- Triage is honest: each finding is checked, decisions that belong to the developer are escalated, and regression tests prove they catch the bug.

## Non-goals

- Changes to the implementation-manager plugin. One follow-up line there is noted under Constraints.
- More than one automatic second opinion per branch.
- A persistent learnings store (unchanged from v0.7).
- Posting to GitHub (unchanged).
- Automatic splitting of oversized change sets (unchanged).

## Anti-goals

- Noise. More findings from a stricter reviewer must still be real. The v0.7 precision (45 of 45 real) is the bar: required steps produce evidence, not speculation.
- A second opinion on every change. Small, low-risk changes stay at one deep round.
- Silent scope creep through outside findings. They stay out of the fix loop unless trivial in a changed file, or unless the repo's rules say they must go to the developer.
- Forcing an orchestrator to babysit. Nothing new may require a human answer that v0.7 did not, except where the repo's own rules already require the developer's answer (outside findings in a repo that forbids deferring, B6).

## Constraints

- Plugin conventions from this repo's AGENTS.md:
  - Python 3.9+ standard library, invoked through `python3`.
  - Tests in `plugins/branch-review/tests/`; `ruff check` and `ruff format` clean.
  - `claude plugin validate` clean, and `version` bumped to 0.8.0.
  - The eval case updated and run with `claude plugin eval`.
- Backward compatible with v0.7 state. A `review.json` written by v0.7 loads, follow-ups continue, and its outside findings (status `deferred`, scope `outside`) count as `outside`.
- `branch-review:check` (`cmd_covered`) keeps its contract: covered only when a clean review has seen everything in the commit. New statuses and the pending second opinion fit that contract (see below).
- The implementation-manager copies the summary's "Escalated, Deferred and Invalid" sections into PR descriptions (its SKILL.md line 54). Those headings stay, so it keeps working. A follow-up in that plugin should also copy the new "Outside the change" and "Intent check" sections. That follow-up is not part of this change.
- The orchestrator runs on macOS and Linux under zsh or bash, and the reviewer runs the same commands.
- The reviewer keeps its tool list (Read, Grep, Glob, Bash; no Write or Edit) and its rule never to modify tracked files.

## Approaches

1. **One release, with the script deciding when a second opinion runs. (Chosen.)** `rs status` works out from recorded data whether a second opinion is due and returns its reasons. Everything ships as v0.8. The trigger can be unit-tested and behaves the same for every invoker, and the new pieces are shared by both halves. The cost is one larger change to review.
2. **The same design in two releases**, mechanics first and then recall. This would show whether the recall changes alone move the numbers. Rejected: the trigger needs the `status` changes anyway, so the split is not clean, and it is slower.
3. **The orchestrator decides, from rules in SKILL.md.** Less code. Rejected: the transcripts show orchestrators improvising around written rules (`$RS`, condensed round files, OPEN_FINDINGS passed as a path), so a judgment call written as prose would fire unevenly.

The developer chose 1.

## Design

### A. Recall

#### A1. Second opinion

A second opinion is one extra deep round, run once the loop first converges. It reviews the whole change set at the converged snapshot, so it also sees what the fixes changed. Running it alongside round 1 was considered: most misses existed at round 1, so either timing would catch those. But the TD-0051 R6-F1 and TD-0049 R3-F2 misses came from fixes and from a finding that was half-handled, and only a later pass sees those. A later pass can also use what round 1 recorded.

**Triggers.** At first convergence the second opinion is due when either of these holds, counting only deep rounds since the last forced deep round (`snapshot-missing`, `history-rewritten`, `paths-changed`) or since the state began:

- a deep round's `merge_risk.level` is `moderate` or `high`;
- any finding of the change (scope `change`) recorded since then has severity `critical` or `major`.

Both are read from recorded state alone, so `status` and `covered` work them out the same way with no run arguments (see Decision record).

Calibration on the 16 SqlSource branch states:

- **Merge risk moderate or higher:** fires on TD-0032, TD-0049, TD-0051 and TD-0071. Three of those four are where a later deep pass found existing misses.
- **A critical or major finding:** adds only TD-0021. That makes 5 of 16 in all.
- **Size and security-sensitive path triggers were considered and dropped.** Size fired on large refactors (TD-0073, TD-0074, TD-0039 part 3), whose extra deep passes found nothing in already-reviewed code. Path patterns matched almost every file in a repo named SqlSource. The developer chose the narrower rule; `--second-opinion` covers anything else.

**Rules.**

- **At most one automatic second opinion per branch state.** Once a recorded round has `focus: second-opinion`, no trigger fires again, even after more commits. A later forced deep round (history rewritten) is not a second opinion and does not use it up.
- **The invoker can ask for one** with `--second-opinion`, whatever the triggers say. That includes branches that already had one. The invoker can also suppress it with `--no-second-opinion`.
- **`status` reports it.** At what would otherwise be `converged`, `status` returns `continue: true`, `reason: second-opinion`, `next_mode: deep`, `next_focus: second-opinion` and `second_opinion_reasons` (`merge-risk`, `major-finding`, `requested`). Escalated findings do not block it: escalated means waiting for the developer, not a broken loop.
- **What happens after it.** If it finds anything, the normal fix and follow-up loop takes over. When that loop converges again, no second opinion is due. If it finds nothing, the loop converges at once.
- **`covered` respects it.** While a second opinion is due, `branch-review:check` reports not covered with the reason `second-opinion-pending`.
- **v0.7 state is exempt.** A branch whose recorded rounds all predate v0.8 (no round carries `plugin_version`) gets no automatic second opinion, and `covered` does not report it pending. Once a round is recorded under v0.8, the triggers apply over the deep rounds above. This keeps branches that v0.7 called covered from being held back the moment the plugin updates.
- **The cap does not block it.** A due second opinion may run even when the round cap is reached (B4), as one extra round. If findings from it then reach the cap, `max-rounds` handling applies as in v0.7. A recorded second-opinion round clears `second-opinion-pending` either way.

**Decision record.** `begin-round` stores the invoker's choice on the branch state:

- `--second-opinion` sets `second_opinion.requested` to the number of the next round;
- `--no-second-opinion` sets `second_opinion.skipped: true`.

A skip stays in force for the branch until a later run passes `--second-opinion`. One shared function computes "due" from the state (triggers, request, skip, any recorded second-opinion round, the v0.7 exemption). `status` and `covered` both call it, so they always agree.

**Model and focus.** The second opinion uses `opus`, or `--model` if given. Its dispatch says `FOCUS: second-opinion`. In that focus the reviewer does the whole deep process, and gives first priority to the required steps the first pass most often skipped (A2): breaking each new test on purpose, trying doc promises against the real system, reading related tech-debt and known-issue docs, and working every checklist category to the end. Open and earlier findings are given to it as context (ids, titles, status) so it does not report them again. Finding something already reported is not a new finding.

#### A2. Stricter deep rounds (every deep round, first pass included)

These go in `agents/reviewer.md` and `reviewer-checklist.md`. Each one comes from a miss in the data.

- **Break new tests on purpose.** For each test the change adds or rewrites for a behavior, change the code under test in a scratch copy and run the test; report which breakages survive. When that is impractical, name the wrong implementation the test would miss and say why it could not be run. *(TD-0051 R4-F1, TD-0050 R2-F1, TD-0075 R1-F1.)*
- **Hunt stale docs without truncation.** For each behavior the change alters, `git grep` the living docs for the old statement and read around every hit. Never pipe that search through `head`. *(TD-0036 R2-F1.)*
- **Read the related tech-debt and known-issue docs.** That means the tech-debt items, known-issue sections and design records that name a changed component or file. *(TD-0051 R4-F2 / TD-0077.)*
- **Try doc promises.** When the change adds or changes a user-facing promise ("never null when …", "waits until …"), try it against the real system when one can be run locally (a container, the CLI). Otherwise trace it through the code. *(TD-0051 R4-F3, TD-0049 R3-F2.)*
- **Check broad mappings.** When code maps an error code, a prefix, a type or a message to one diagnosis, list what else produces it and check that each of those gets the right answer. Add this to the checklist under logic. *(TD-0049 R3-F1.)*
- **Finish the checklist.** Work every category even after a critical or major is found. *(TD-0049 R1.)*
- **Check the intent in both directions, including the non-goals.** Check every stated claim, the reverse of each claim (what else the code does under the same condition), and the "deliberately not done" list against the repo's tech-debt records. *(TD-0049 R3, TD-0051 R4-F4.)*
- **One defect per finding.** Never put a second defect in another finding's `fix` or `trigger` field. *(TD-0049 R3-F2, which R1 had put in R1-F1's fix text.)*
- **Report it or drop it.** No "noted but not reported" text; prose observations that are not findings go in `intent_check.unmentioned_behavior` or nowhere. *(TD-0021 R5/R6.)*
- **Check your own fix.** Before reporting, trace each suggested fix against the finding's trigger, and look for a new way in that the fix itself opens. *(TD-0051 R4-F2, which came from R1's own fix; TD-0075, where the suggested bound raced the mutation that was meant to break it.)*
- **A pre-existing claim the change repeats or relies on is part of the change.** Report it as a finding on the change, not as outside. *(TD-0048 R1-F2.)*
- **No process items.** Branch freshness, CI state and push readiness are not findings. *(TD-0021 R4-F2.)*

#### A3. Follow-up rigor

- **Same setup as a deep round.** Read `SCHEMA_PATH`, the checklist and the AGENTS.md files on the path to each changed file before reviewing. *(TD-0039 P3 R2's broken JSON; several follow-ups skipped this.)*
- **Check each fix in the docs.** For each fix that changed a behavior or a statement, `git grep` the old wording across living docs.
- **Run tests yourself.** Run the tests that cover the delta. Do not accept "the orchestrator reports a green run" (but see `VALIDATED` in B9).
- **Say what wasn't checked.** End the prose with what was not checked, instead of asserting that something matches. *(TD-0051 R2.)*
- **A large delta goes deep with Opus.** When a follow-up delta has 40 or more non-generated changed files, or 2,000 or more changed lines in them, `begin-round` returns `mode: deep` with `mode_reason: large-delta`. The skill then dispatches a deep round with Opus over the whole change set. *(TD-0074 R4, where the orchestrator had to override this by hand.)*
- **Generated files.** A file counts as generated when `git check-attr` reports `linguist-generated` or `-diff` set for it, or when it matches the run's `--generated` globs, which are stored with the state like `--paths`. Generated files are excluded from the large-delta threshold and from the oversized warning. They stay in `changed_files`, so the reviewer still sees them. *(TD-0071's 105 files included about 47 recorded fixtures.)*

#### A4. Reading AGENTS.md

The reviewer lists the AGENTS.md and CLAUDE.md files that apply, with their sizes. It reads the root file and each file on the path to a changed file in full, one at a time, with the Read tool. It reads others only when a rule points to them. It never prints several at once, and never sends rule output to a file it does not then read. *(TD-0032 R4-F2, TD-0034.)*

#### A5. Environment notes

reviewer.md gets a short section of environment notes:

- macOS has no `timeout` command; use the test runner's own timeout, or `perl -e 'alarm …'`.
- BSD `sed -i` needs an argument.
- Quote globs (`--include='*.cs'`) for zsh.
- Containers the reviewer starts and tools it installs fall under the cleanup rule like scratch files: remove them, or list them in the report.
- Keep containers on the image versions the repo pins.

### B. Mechanics

#### B1. `rs` wrapper

`plugins/branch-review/scripts/rs` is an executable POSIX `sh` script. It runs `python3 "<its dir>/review_state.py" "$@"`, adding `--state-dir "$CLAUDE_PLUGIN_DATA"` when no `--state-dir` was given and the variable is set, and `--repo "$(git rev-parse --show-toplevel)"` when no `--repo` was given. SKILL.md calls it by its full path (`"${CLAUDE_SKILL_DIR}/../../scripts/rs" <command> …`) and says never to put a command in a shell variable. `review_state.py` keeps its current arguments and refusals. The `--state-dir` rule (an invoker-given absolute path, never one the agent chose) is unchanged.

#### B2. Reports the orchestrator never retypes

- The dispatch prompt gains `REPORT_PATH: <tmp>/round-<n>.md`.
- The reviewer writes its complete final report (prose plus the JSON block) to that path with a Bash heredoc whose delimiter cannot appear in the report, then returns the same report as its final message.
- The skill runs `rs record-findings --file <REPORT_PATH> --report`.
- If the file is missing or has no JSON block, the skill falls back to saving the agent's final message with the Write tool (creating the file; never a placeholder first), then retries.
- The re-emit exchange for bad JSON stays as in v0.7: the reply is appended to the file.
- SKILL.md says never to create the round file before the reviewer reports.
- The report path is outside the repo, so the reviewer's tree-cleanliness rule is unaffected.

#### B3. `status` and convergence

- `new-findings` keeps the loop going only when at least one finding from the latest round is `open`. Findings the orchestrator already moved to `escalated`, `invalid` or `outside` do not count.
- The reason values become: `open-findings`, `unverified-fixes`, `new-findings`, `second-opinion`, `converged`, `max-rounds`.
- The output gains `next_mode` and `next_focus` when `continue` is true.

#### B4. Round cap

- The cap of six counts rounds numbered above the later of `--since-round` and the last round whose mode was forced deep by `snapshot-missing`, `history-rewritten` or `paths-changed`.
- Merge-check rounds (B5) and the second-opinion round count toward the cap but do not reset it. A due second opinion may still run at the cap (A1).
- At the cap, when the only unsettled findings are `fixed` (claimed but unverified), `status` allows one more round as a follow-up whose only job is to verify them (`reason: final-verification`).
- After that, `max-rounds` behaves as in v0.7.

#### B5. Merge-check rounds

When the base was merged in with conflicts (`base-moved` in v0.7), `locate` returns `mode: merge-check` and lists the conflicted paths in `conflicts`.

- **How `locate` finds them.** It runs `git merge-tree --write-tree --name-only --merge-base=<anchor merge base> <anchor snapshot> <new merge base>` and reads the conflicted paths.
- **What the dispatch carries.**
  - `CONFLICTS`: the conflicted paths.
  - `UPSTREAM_OVERLAP`: the files the base changed since the anchor's merge base that are also in the change set.
  - The open findings.
  - The usual `DIFF_FROM` (the anchor snapshot) and `DIFF_TO` (the new snapshot).
  - `CHANGED_FILES`: the files in that delta that are also in the change set (merge base to new snapshot), plus the conflicted files. Files only the base changed drop out. The branch's own work since the anchor (fixes made alongside the merge, say) is always in scope, so nothing new reaches `covered` unreviewed.
- **What the reviewer checks.** It does what a follow-up does for the branch's own changes in `CHANGED_FILES`. In addition:
  - each conflict resolution keeps both sides' intent;
  - the change still fits the base's changes in the overlapping files (renamed APIs, moved tests, new rules in AGENTS.md);
  - the build and the tests that cover the overlap pass;
  - each carried finding still holds, or still verifies.
- **What it does not do.** It does not review the unchanged rest of the change again. New findings are reported normally.
- **Model.** Opus by default: conflict resolutions are subtle, and the round is small.
- **Fallback.** When git is older than 2.40 or merge-tree fails, the round is deep, as in v0.7.
- **Other forced deep reasons.** `snapshot-missing`, `history-rewritten` and `paths-changed` still force a deep round.

`covered` treats a recorded merge-check round as a normal anchor. A conflicted base update that was never reviewed still reports `base-conflict`.

#### B6. Outside findings

- A new terminal status, `outside`, replaces `deferred` for findings from `outside_change`. `deferred` again means only that the developer chose to defer.
- Loading v0.7 state maps `deferred` findings with scope `outside` and resolution note `outside the change set` to `outside`.
- The summary gets a section "Outside the change", listed after "Invalid" and before "Fixed and verified".
- **Triage rule:** if the repo's AGENTS.md forbids deferring review findings, or requires them to be raised with the owner, the orchestrator escalates each outside finding (`set-status escalated`, allowed from `outside`). The note is "outside the change; the repo does not allow deferring it without the developer", plus a recommendation. Escalated findings block `covered` as in v0.7. The developer accepted that block: such a repo's own rule says the owner decides, and in the SqlSource run the manager blocked a push over exactly this.
- The trivial-fix exception stays (the fix is trivial and touches only files already in the change). It is extended so the orchestrator may also fix an outside finding the developer told it to fix.

#### B7. Summary and intent check

- `render_summary` adds a section "Intent check" after "Open and unverified". It lists:
  - the unmet claims of the latest deep or second-opinion round, plus those of later rounds;
  - the union of `unmentioned_behavior` across rounds, de-duplicated by exact text.
- SKILL.md section 9 tells the invoker to copy Escalated, Deferred, Invalid, Outside the change and Intent check into the PR description under "Review notes".
- **Triage of unmentioned behavior.** After each round, the orchestrator reads `unmentioned_behavior`. An item that contradicts the intent is handled like a finding with no id: fix the code, update the intent (the next `begin-round` stores it), or escalate it to the developer by asking in its report. Items that are intended and simply unstated go into the intent or the PR description.

#### B8. Triage rules

These are added to SKILL.md section 7.

- **Decisions that belong to the developer.** A finding whose `fix` offers alternatives, says "developer decision", or asks the owner to choose is **Needs a decision**: escalate it, unless the intent already settles the choice; then quote the intent's line in the note. *(TD-0032 R1-F1.)*
- **Regression tests must fail without the fix.** The note for `set-status fixed` says how that was shown (the test run against the unfixed code, or a revert in a scratch copy), or why it could not be. A test the orchestrator knows passes either way is not a fix. *(TD-0050.)*
- **One recorded check per finding.** Before a finding is marked Valid or Wrong, the orchestrator runs or reads something that confirms or refutes it, and the `set-status` note starts with `Checked: <what>`. Handing the check to a fixer subagent is allowed when the fixer must first show the test failing; the note then says so. *(TD-0071, TD-0072, TD-0051 marked findings valid with no visible check.)*

#### B9. Dispatch additions

- `VALIDATED: <command> @ <sha>` (optional): the orchestrator's last full validation run and the commit or snapshot it ran on. When it matches `DIFF_TO`'s tree, the reviewer may skip running that same full suite. It still runs the targeted tests and breaks tests on purpose (A2). *(Several deep reviewers re-ran full suites the orchestrator had just run, once at the same time.)*
- `FOCUS` (`second-opinion` or absent), `REPORT_PATH`, and, for merge-check rounds, `CONFLICTS` and `UPSTREAM_OVERLAP`.
- The template gains a line saying `INTENT`, `OPEN_FINDINGS` and `CHANGED_FILES` are inlined text, never file paths. *(Several dispatches passed paths.)*

#### B10. Re-runs

SKILL.md says that every new run, including one after a merge with the base or a redo request from an orchestrator, starts at section 2, and ends with `rs summary`. *(TD-0074's later rounds skipped prune and summary, leaving a stale `summary.md`.)*

### Arguments

New arguments to `/branch-review:review`:

- `--second-opinion`: run a second-opinion deep round at convergence, whatever the triggers say.
- `--no-second-opinion`: do not run an automatic one for this branch; stored with the state, so `covered` does not wait for it, until a later run passes `--second-opinion`.
- `--generated GLOB...`: extra globs for generated files (A3), stored with the state like `--paths`.

`--full`, `--model`, `--base`, `--intent`/`--intent-file`, `--paths`, `--resolve` and `--state-dir` are unchanged.

### State changes

- Round object gains:
  - `focus` (`second-opinion` or absent);
  - `mode` may be `merge-check`;
  - `mode_reason` (from `locate` or `begin-round`);
  - `conflicts` (merge-check rounds);
  - `plugin_version` (the plugin version that recorded it).
- Top level gains:
  - `second_opinion`: `{requested, skipped}`;
  - `generated`: the stored globs.
- Statuses gain `outside`.
- `status` output gains `next_mode`, `next_focus` and `second_opinion_reasons`.
- `locate` output gains `conflicts`.
- `begin-round` accepts `--mode merge-check` and `--focus second-opinion`, and may return `mode: deep` with `mode_reason: large-delta` for a follow-up request.
- `covered` gains the reason `second-opinion-pending`.
- `finding-schema.md` documents all of these.

## Testing

- **Unit tests** in `plugins/branch-review/tests/` for:
  - each second-opinion trigger and its once-per-branch rule;
  - `--second-opinion` and `--no-second-opinion`, and the skip persisting;
  - the v0.7 exemption;
  - a due second opinion at the cap;
  - `status` and `covered` agreeing on "due";
  - merge-check `CHANGED_FILES` including branch work and excluding base-only files;
  - `status` with escalated-only new findings;
  - the cap reset and the final verification round;
  - merge-check detection and the listing of conflicted files (including the git < 2.40 fallback);
  - large-delta promotion;
  - generated-file exclusion;
  - `outside` mapping from v0.7 state;
  - escalation from `outside`;
  - the Intent check section of the summary;
  - `covered` with `second-opinion-pending`;
  - the `rs` wrapper's defaults under `sh` and `zsh`.
- **The `review-loop` eval** gains:
  - a grader that the recorded round file is the reviewer's report word for word (compare with the reviewer's final message);
  - a grader that no `$`-variable command prefix was used.
- **A second eval case**, if it fits in the budget: a fixture whose first round finds a major, so that a second-opinion round must follow. The case passes when that round runs and the loop converges.

## Left to the builder

- Exact wording in SKILL.md, reviewer.md and the checklist, to the superpowers:writing-skills standard. Keep SKILL.md's length close to v0.7's by moving detail into the checklist and schema where it fits.
- How `begin-round` counts changed lines for generated files, and whether `--generated` is stored with the state for later runs (default: stored, like `--paths`).
- The heredoc delimiter scheme for REPORT_PATH.
- Whether `rs` needs a Windows story (default: no; the repo targets macOS and Linux).
- Whether to tune the 40-file and 2,000-line large-delta threshold against the SqlSource numbers before release.

## Decided by the developer

- A second opinion runs automatically from risk signals, or on the invoker's request.
- It runs at first convergence, not in parallel with round 1.
- The triggers are only merge risk moderate or higher, or a critical or major finding. There are no size or path triggers.
- Outside findings in a repo that forbids deferring are escalated, and block `covered` until the developer answers.
- Approach 1: one release, with the script deciding.

## My calls (made without the developer)

- At most one automatic second opinion per branch state, and a skip that stays in force.
- `--no-second-opinion` exists and keeps `covered` from waiting.
- v0.7 state is exempt from the automatic second opinion until a v0.8 round is recorded.
- A due second opinion may run past the round cap.
- "Due" is computed from state by one function shared by `status` and `covered`.
- Merge-check rounds review the branch's own changes since the anchor, as well as the conflicts, and use Opus.
- The `outside` status and its v0.7 mapping.
- The final verification round at the cap.
- The large-delta threshold (40 files / 2,000 lines) and generated-file detection through gitattributes plus `--generated`.
