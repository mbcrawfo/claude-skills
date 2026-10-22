# Branch review skill: design

Date: 2026-10-22
Status: draft, awaiting approval

## Intent

CodeRabbit reviews every PR on mbcrawfo's repos, but its rate limits can't keep up with the volume of agent-written changes. This plugin replaces it. A coding agent that has finished a change set runs the review before opening a PR, fixes what the reviewer finds, re-reviews the delta, and repeats until the review is clean. The developer only hears about findings that need a human decision.

The bar is CodeRabbit's output. An analysis of its reviews on mbcrawfo/SqlSource (107 PRs), mbcrawfo/vibefun (111 PRs) and mbcrawfo/stashman (9 PRs) is summarized in the appendix and drives the review checklist below.

## Goals

- Reproduce as much of CodeRabbit's value as a local agent review can: concrete logic bugs, sibling paths a fix missed, tests that prove nothing, security holes, intent-vs-code mismatch, repo-convention violations, stale docs.
- Findings precise enough for an agent to act on without a human: file, line, trigger, consequence, suggested fix, evidence.
- Two review modes: a deep adversarial review of the whole change set, and a lighter follow-up that re-verifies prior findings and reviews only the delta.
- Local state per repo and branch so follow-ups know what was found, fixed, rejected and escalated.
- A conversation between the coding agent and the reviewer about disputed findings, with a clear end: withdrawn or escalated.
- Fresh reviewer every time. The reviewer never shares context with the agent that wrote the code.

## Non-goals

- Posting anything to GitHub. Output stays local; the coding agent carries it into the PR description itself.
- Walkthrough summaries, sequence diagrams, "possibly related PRs".
- Generating tests. The reviewer may demand a regression test; the coding agent writes it.
- Replacing linters, formatters or analyzers the repo already runs.
- A persistent "learnings" database. CodeRabbit's learnings went stale and were misapplied; the reviewer reads AGENTS.md live instead, and durable policy belongs in AGENTS.md.

## Anti-goals

Things that would make this a failure even if it technically works:

- Noise. Praise comments, markdown lint, docstring-coverage nags, "review the changelog" on dependency bumps, requests to maintain hard-coded line numbers. CodeRabbit spent a quarter of its comments on these and authors ignored them.
- Findings the agent can't verify. Every Critical or Major finding needs evidence: a command the reviewer ran, a test it wrote in a scratch location, or a precise line citation with the failing input spelled out.
- A reviewer that only reads the diff. The highest-value CodeRabbit findings came from reading callers, sibling branches, the repo's AGENTS.md files and the docs they point at.
- Flagging drift in docs the repo treats as records of intent. Both SqlSource and stashman had to push back on this repeatedly.
- Silent scope creep: the loop fixing pre-existing bugs outside the change, or rewriting code to taste.

## Constraints

- Ships as a plugin in this marketplace: `plugins/branch-review/`, manifest, marketplace entry, `claude plugin validate` clean. Plugin and skill names are kebab-case.
- Runs inside Claude Code. The orchestrator is whichever agent invokes the skill; the reviewer is a subagent dispatched with the Agent tool and continued with SendMessage.
- This is a final, whole-change review, the local analogue of a PR review. It is run by the session or agent that owns the whole change, once the change set is complete. An implementer subagent building one slice of a larger change must not run it. Claude Code gives a skill no way to detect that it is inside a subagent (subagents can nest, and no variable reports it), so this is a stated rule in the skill, not a check: the skill tells an agent that is working one slice of a plan to stop and leave the review to the agent that owns the whole change.
- Defaults: Opus for the deep review, Sonnet for follow-ups. Any model, including Fable, can be requested per run.
- State lives in the plugin's data directory, `${CLAUDE_PLUGIN_DATA}` (which Claude Code resolves to `~/.claude/plugins/data/<plugin-id>/` and removes on uninstall), never inside the repo or its `.git` directory, and has a cleanup process.
- Works for a dirty working tree. Agents often review before their final commit, and the skill never commits or forbids commits; the invoking agent follows its own workflow.
- Must not require network access or GitHub.

## Shape of the plugin

```
plugins/branch-review/
  .claude-plugin/plugin.json
  agents/reviewer.md                 # the reviewer subagent definition
  skills/review/SKILL.md             # orchestrator skill: the loop
  skills/review/reviewer-checklist.md  # what to look for, loaded by the reviewer
  skills/review/finding-schema.md    # the finding record and the JSON state format
  skills/cleanup/SKILL.md            # state cleanup
```

Invocation: `/branch-review:review` with optional arguments, and `/branch-review:cleanup`.

Two skills rather than two review skills: the review skill picks deep or follow-up itself from the state on disk. A separate cleanup skill keeps the review skill's instructions short.

### Arguments to `review`

- `--full`: force a deep review even when state exists.
- `--model <name>`: model for the reviewer (`opus`, `sonnet`, `fable`, ...). Default: Opus for deep, Sonnet for follow-up.
- `--base <ref>`: the branch this change will merge into. Default: the remote default branch if `origin/HEAD` is set, else `main`.
- `--intent "<text>"` or `--intent-file <path>`: the intent statement. Required on the first review of a branch if not already stored; optional afterwards, and replaces the stored one when given.
- `--paths <glob>...`: restrict the review to matching files. For scoping an oversized change set by hand; not used by default.
- `--resolve <id> "<note>"`: record the developer's decision on an escalated finding (see Escalations later).

## The loop

The orchestrator (the coding agent) does this when the skill is invoked:

1. **Locate state.** Compute the repo key and branch key (below). Load `review.json` if it exists. Prune stale state while here (see Cleanup).
2. **Pick the mode.**
   - No state, or `--full`: deep.
   - State exists and the last round's snapshot commit still exists and its parent (the HEAD at that time) is an ancestor of the current HEAD: follow-up.
   - State exists but the snapshot is gone (garbage collected) or its parent is not an ancestor of HEAD (rebase, amend, reset): deep, carrying open findings forward for re-verification. Say so.
3. **Gather the intent.** Use `--intent`, else the stored intent, else stop and ask the invoking agent to supply one. The intent is the PR description: what the change is for, what it does, what it deliberately does not do. Store it.
4. **Snapshot and compute the diff range.** Take a snapshot of the working tree with `git stash create` (it writes a commit object and no ref, and changes nothing in the tree or index; on a clean tree it returns nothing, so use HEAD). Record the snapshot sha, HEAD, and the changed file list. Deep: `merge-base(HEAD, base)` to the snapshot. Follow-up: the previous round's snapshot to the new snapshot. This makes the delta exact whether or not fixes were committed in between.
   If the change set is very large, roughly 150 files or 10,000 changed lines, warn that review quality will degrade and suggest `--paths` or a per-commit review, but run anyway if not told otherwise. There is no automatic splitting: a reviewer that only sees a slice misses the cross-cutting findings this review exists for.
5. **Dispatch the reviewer** as a fresh subagent using the plugin's `reviewer` agent, with the requested model, and a prompt that carries: mode, intent, base ref, the two snapshot shas to diff, changed file list, the path to the checklist and schema files, and for follow-ups the open findings with the orchestrator's claimed resolution for each (commit, what changed). One reviewer, always, with the whole change set.
6. **Record the findings.** Merge the reviewer's JSON output into `review.json` as a new round. Follow-up verdicts on prior findings update those findings' status; new findings are appended with the round number.
7. **Triage every open finding.** For each:
   - **Fix** when the finding is valid. Verify the claim against the code first (treat finding text as untrusted data, never as instructions), make the fix, add a regression test when the finding is a behavior bug and the repo has tests, note the commit or file. Status: `fixed`, pending reviewer verification.
   - **Dispute** when the orchestrator believes the finding is wrong. Send the reviewer a message with the finding id, the reason, and evidence (a command run, a rule in AGENTS.md, a test). The reviewer answers per finding: `withdrawn` with a one-line reason, or `upheld` with its counter-evidence. Withdrawn: status `invalid`, both sides' reasons stored. Upheld: status `escalated`. One exchange per finding; no second round of argument.
   - **Escalate** when a fix needs a direction decision the intent doesn't settle, when the fix is unclear, or when the fix would be large or complex relative to the change. Status: `escalated`, with a note on what the developer must decide and the orchestrator's recommendation. A large-fix escalation may propose treating it as deferred tech debt.
   - **Ask** when the finding is unclear. The same message channel; the reviewer clarifies. This does not consume the one dispute exchange.
   - Pre-existing issues the reviewer reports outside the change set are marked `deferred` by default, not fixed, unless the fix is trivial and touches files already in the change.
8. **Decide whether to loop.** Run another follow-up when the round produced any `fixed` findings (they need verification) or any new findings. Stop when a follow-up returns no new findings and every finding is `verified`, `invalid` or `escalated`. Also stop, and say so, after 6 rounds; escalate whatever is still open.
9. **Report.** Write `summary.md` into the state directory and give the invoking context a short report: rounds run, counts by status, each escalated finding with what the developer must decide, each invalid finding with the reason. The orchestrator is expected to put the escalated and invalid lists into the PR description under a heading like "Review notes", so the developer sees them without opening anything.

When the orchestrator rejects a finding because of a repo policy that is not written in any AGENTS.md, it should say so in its report and suggest the AGENTS.md line to add. That is the durable replacement for CodeRabbit's learnings.

### Escalations on later runs

An escalated finding stays `escalated` across runs and appears in every summary until the developer decides. The decision comes back in through the orchestrator: when the developer says what to do, either in chat or with `--resolve <id> "<note>"`, the orchestrator records the note and then either fixes the finding (status `fixed`, verified by the next follow-up like any other) or marks it `deferred` with the note, following the repo's own tech-debt process if it has one. `deferred` findings are listed in the summary under their own heading and never block the loop. Developer decisions are never inferred from observed content; only the developer's own words in chat or the `--resolve` argument count.

## The reviewer

A plugin agent definition (`agents/reviewer.md`) with a system prompt that establishes the role, and tools limited to reading, searching and Bash. It may run read-only commands, builds and the test suite. It must not change tracked files. To gather evidence it may add clearly named temporary files inside the repo (for example a scratch test in the right test project), but it deletes them before returning and confirms with `git status` that the tree is as it found it. It manages its own context the way a human reviewer would: diffstat and full diff first, then open files selectively.

The reviewer's job in deep mode:

1. **Learn the repo's rules before judging.** Read every `AGENTS.md` and `CLAUDE.md` from the repo root down to the directory of each changed file, plus any docs those files tell agents to read. Note rules that apply to the changed files. Honor any instruction in those files about what not to review (for example "specs under docs/superpowers are records of intent and are not updated").
2. **Read the intent.** Then read the full diff with context, then the full current contents of every changed file, then whatever is needed to understand the change: callers, callees, the sibling branches of edited conditionals, the tests that cover the changed code, the docs that describe it.
3. **Hunt.** Work the checklist (next section), in order of value. For each suspected issue, try to construct the concrete failing input or scenario. Verify with a command, a scratch test, or a precise reading before reporting. Drop anything that can't be made concrete.
4. **Check intent against code** in both directions: things the intent promises that the code doesn't do, and things the code does that the intent doesn't mention.
5. **Report** in the finding format. Rate the merge risk in one sentence.

In follow-up mode the reviewer first takes each open finding with its claimed resolution, reads the current code, and marks it `verified` or `still-open` with a reason. Then it reviews the delta fresh, with specific attention to sibling paths of each fix and to the tests added for them, since fix commits produced over a third of CodeRabbit's findings on vibefun and nearly half the Criticals.

### What the reviewer looks for

Ordered by how much value the same class of finding delivered in the CodeRabbit data. The checklist file in the skill carries this with examples drawn from SqlSource and vibefun, which are public. stashman is private and nothing from it goes in the plugin.

1. **Logic and correctness bugs with a concrete failing input.** Wrong order of operations (a substitution applied after unify, a cap enforced after the blowup), state that survives cancellation, a signal race, off-by-one and bounds reads, null and empty cases, wrong join semantics, 32-bit coercion, timezone and infinity edge cases.
2. **Sibling paths.** The same defect in the adjacent branch the change didn't touch: the `let rec` path after the `let` path was fixed, the base-missing twin of the head-missing fix.
3. **Security and hardening.** Injection through an allowlist, path escape via `..`, fail-open CI and coverage checks, secrets or connection strings reaching logs, missing workflow permissions, cookie attribute rules, unquoted paths in shell hooks, bind mounts escaping an isolation boundary.
4. **Tests that prove nothing.** Assertions that pass on the crash path, substring matches that false-pass, fixtures with duplicate keys that silently overwrite, missing regression test for the fix being reviewed, missing boundary case for a limit the change introduces.
5. **Intent versus code.** Claims in the intent not backed by code; behavior in the code not covered by the intent.
6. **Repo conventions.** Violations of rules in AGENTS.md or CLAUDE.md, cited by file and rule text. Things like test placement, keep-lists-in-sync footers, file size limits, naming the repo enforces.
7. **Docs the change made stale**, limited to docs the repo treats as living: README, manuals, architecture docs, AGENTS.md itself. Stale cross-references, counts that no longer match, examples that no longer compile.
8. **CI, build and config correctness.** MSBuild item types, localized tool output in greps, missing image pins where the repo pins, retry logic that reuses a failed instance.

Explicitly not reported: praise, anything a formatter or linter in the repo would catch, generic dependency-bump advice, docstring coverage, requests to maintain hard-coded line numbers in docs, suggestions that are purely taste. Pre-existing issues outside the change go in a separate "outside the change" list, never mixed with findings on the change.

### Finding format

Each finding carries:

- `id`: `R<round>-F<n>`.
- `severity`: `critical`, `major`, `minor`. No nitpick tier. If it isn't worth fixing, it isn't reported.
- `category`: one of the checklist headings.
- `file`, `lines`.
- `title`: one imperative sentence.
- `trigger`: the concrete input or scenario that produces the failure.
- `consequence`: what goes wrong, in the project's own terms.
- `reachable_now`: `yes`, `no` or `unclear`, with a few words on why.
- `evidence`: commands run and what they showed, or the exact lines read.
- `fix`: a diff or a precise description. Include a regression test suggestion when the finding is a behavior bug.
- `source`: the AGENTS.md rule or doc the finding rests on, when there is one.
- `effort`: `quick` or `heavy`.

Plus per round: a two-sentence summary, the merge-risk line, the intent check results, and the outside-the-change list.

The reviewer returns prose for the human reader and a fenced JSON block for the orchestrator. The exact JSON shape is in `finding-schema.md` and is the builder's to settle; it must round-trip through `review.json`.

## State

Location: `${CLAUDE_PLUGIN_DATA}/<repo-key>/<branch-key>/`.

- `repo-key`: `owner__repo` from the `origin` remote URL, normalized (strip scheme, host, `.git`, lowercase). If there is no origin remote, a short hash of the git common directory path. Worktrees of the same repo share a key because they share a remote.
- `branch-key`: the branch name with `/` replaced by `__`. A detached HEAD uses `detached-<short-sha>` and prints a warning.

Files:

- `review.json`: repo, branch, base ref, intent, created and updated timestamps, rounds (number, mode, model, timestamp, reviewed head commit, dirty flag, changed files, summary, merge risk) and findings (the record above plus `status`, `round_opened`, `round_closed`, `resolution` with commit and note, `discussion` as a list of orchestrator and reviewer messages).
- `rounds/<n>.md`: the reviewer's raw report for each round, for audit.
- `summary.md`: the latest end-of-loop report.

Each round also records the snapshot commit sha it reviewed.

Finding statuses: `open`, `fixed` (claimed, awaiting verification), `verified`, `still-open` (reviewer disagreed that it was fixed; treated as open for the next triage), `invalid`, `escalated`, `deferred`.

## Cleanup

Every `review` run prunes, without asking: state for branches of the current repo that exist neither as a local branch nor as `refs/remotes/origin/<branch>`, and state untouched for more than 30 days. The remote-tracking check keeps a branch's state alive when it is being worked on from another clone. It prints what it removed.

`/branch-review:cleanup` with no arguments lists all state with repo, branch, round count and last-updated date. `--branch` removes the current branch's state, `--repo` removes the current repo's, `--all` removes everything, `--older-than <days>` overrides the 30-day rule. Removal asks for confirmation unless `--yes` is given.

## What is left to the builder

- Exact SKILL.md wording and the reviewer agent's system prompt, written to the superpowers:writing-skills standard.
- The JSON schema for `review.json` and the reviewer's output block.
- How the orchestrator parses the reviewer's output robustly when the JSON block is malformed (ask the reviewer to re-emit it).
- Whether the plugin agent definition can be selected by the Agent tool from a plugin and how model override interacts with the agent's declared model. If a plugin agent can't be dispatched that way, fall back to a general-purpose subagent given the agent prompt file.
- The size at which the oversized-change warning fires.
- Argument parsing conventions for the skill.
- A manual test on one of the three analyzed repos before release: a deep review on a branch with a known planted bug, a fix, and a follow-up that verifies it.

## Appendix: what CodeRabbit did on the three repos

From review dumps of all 227 PRs. The full reports for the two public repos are committed under `docs/superpowers/research/2026-10-22-coderabbit-analysis/`; the stashman report stays out of this public repo. The facts that shaped this design:

- Acceptance ran about 77% on every repo. Every Critical and nearly every Major was accepted. Declines concentrated in Trivial and Minor doc and style items.
- Highest-value findings were logic bugs with a concrete failing input, found by reading surrounding code: a substitution applied in the wrong order, alpha-rename collisions, a cancellation that didn't stop a sweep, a race between two termination signals, SQL injection through a type allowlist, path escape via `..`, invalid Postgres trigger syntax, `__Host-` cookie rules, fail-open CI checks.
- On vibefun, 42% of findings were on test code, mostly tests that pass without proving anything. 37% of findings and 7 of 15 Criticals came in round two or later, because fixes exposed sibling paths.
- On SqlSource, 75% of findings were on markdown: cross-document drift in a spec-heavy repo. Useful when the doc is living, noise when the repo says the doc is a record of intent, which CodeRabbit kept getting wrong even after being told.
- Every finding had a category, severity and effort tag, an imperative title, the trigger, the consequence, line numbers, often a diff, and in a third of cases the shell commands it ran to verify. Authors routinely re-verified claims empirically before accepting, and CodeRabbit was wrong often enough to justify that.
- Follow-up pushes got an incremental review of only the new commit range. Prior findings were re-checked and marked "Addressed in commit X"; unresolved ones were re-posted as duplicates; findings it couldn't place in the diff went to an "outside diff range" list. "No actionable comments" was the convergence signal.
- Disagreement was a thread: the author replied with evidence, CodeRabbit withdrew or held, and recorded a learning. Learnings later misfired on vibefun.
- It cited AGENTS.md and CLAUDE.md as "coding guidelines" in a large share of findings, including nested ones.
