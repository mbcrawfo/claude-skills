# Implementation manager: token efficiency

Status: approved design, 2026-11-09. Plugin: `plugins/implementation-manager` (0.9.0 today; this ships as 0.10.0).

## Intent

Cut the manager session's own token use without making runs less reliable. The owner reviewed the manager sessions of the last two weeks and wants the manager cheaper. Children, their subagents and branch-review's reviewers are out of scope (see Non-goals), even though they use most of a run's tokens.

## What the runs show

Fourteen manager sessions from 2026-10-20 to 2026-11-08, with the eight since 0.7.0 (2026-10-28 on) as the baseline. Figures are input-token equivalents: cache reads at 0.1, cache writes at 1.25, output at 5.

- **Context only grows.** No manager ever compacted: the 1M window is never reached. Peak context was 330k–855k tokens; the Nov 6 run averaged about 560k over 534 API calls.
- **Cost is context times calls.** Cache reads dominate. Each run also fully re-caches its context 4–15 times, mostly when the owner returns after more than an hour.
- **What wakes the manager** (post-0.7.0): owner messages (30–60% of spend) and run-watcher events (25–70%). Detach worked: child turn-end notes fell from 50–100 per run to under 10.
- **A watcher wake takes 4–5 calls**: `run.py status`, reads of logs or files, the action (`go`, `redo`, merge…), and a new `run.py wait`. A push request also loads `branch-review:check` and runs its long `rs covered` command (96 such commands in the eight runs).
- **The manager writes every child brief out in full.** Briefs are about 16k characters, most of them fixed text (parts 8–11 alone are about 10k). That is 20k–139k output tokens per run, which then stay in context.
- **Thinking** is an estimated 90–170k tokens per run, kept in context.
- **The skill body** is 87 KB (about 25k tokens), loaded once per run. Integration-branch mode is about 17k characters of it and is used only when the owner asks for it.

## What the self-clear spike showed

A throwaway session on 2026-11-09 tested having the manager clear itself (`clear_session "self"`):

- The clear killed the session's background Bash command, so `run.py wait` would die with it.
- The turn after a clear starts a new Claude Code process; its `SessionStart` hook source is `startup`, not `clear`. A project-local settings hook never ran in a desktop session; plugin hooks did.
- Auto mode refused a self-clear the user had not asked for directly.
- Messages from other sessions reach a cleared session, and its `local_…` id is unchanged.

Claude Code's documentation adds: background commands keep running through compaction; a model cannot compact its own session (only auto-compact or the user's `/compact`); and after compaction, invoked skills are re-injected **truncated to their first 5,000 tokens** (25,000 in total, oldest dropped first).

## Goals

1. Keep the manager's context bounded over a long run, by asking the owner to compact at quiet points.
2. Make the manager correct after any compaction, whenever the owner chooses to compact.
3. Cut what goes into the context: brief text, calls per watcher wake, and the skill body.
4. Roughly halve manager token use per unit, measured the way this review measured it.

## Non-goals

- Changing what children do: their models, review rounds, subagent use, or anything else in parts 1–11 beyond where the text comes from. The children receive the same instructions as today.
- Changing branch-review.
- Lowering the auto-compact threshold through settings (rejected: it lives outside the plugin and affects every session).
- Self-clear or a successor manager session (rejected: see the spike, and the Approaches section).
- Changing the push gate's decisions. The manager still decides every `go`, `redo` and `queue` itself; the script only gathers what it needs to decide.

## Anti-goals

- A run that goes deaf: no watcher running while units are open.
- A compacted manager that acts on half-remembered rules: merging past the merge bar, pushing without a check, re-starting children, or forgetting an owner decision.
- More owner effort than one pasted command now and then.

## Constraints

- The four constraints at the top of SKILL.md still hold (sessions start only on the user's turn; 10-message send budget; only the watcher reports events; child notes carry nothing).
- After compaction only the first 5,000 tokens of SKILL.md come back. Anything past that point is gone until the manager reads it again.
- Plugin scripts are Python 3.9+, standard library only (AGENTS.md).
- `CLAUDE_CODE_SESSION_ID` is set in Bash subprocesses and names the session's transcript file (documented). The transcript's JSON layout is not a documented interface.
- AGENTS.md rules: tech debt and deferred work are documented in the same PR; bump the plugin version; README in Simplified Technical English; internals in ARCHITECTURE.md.

## Approaches

| Approach | Trade-off | Outcome |
|---|---|---|
| A. Self-clear at quiet points, with a plugin hook to resume | Automatic and bounded, but the spike showed the clear kills the watcher, nothing wakes the cleared session except a message, and auto mode refuses the clear. | Rejected after the spike. |
| B. Auto-compact at a lower window (`autoCompactWindow` / `CLAUDE_CODE_AUTO_COMPACT_WINDOW`) + slimming | Automatic, keeps the watcher. The setting lives in user or project settings, outside the plugin, and changes every session there. | Not chosen by the owner. |
| C. Owner compacts when the manager asks + slimming | No settings change; the watcher, session id and messages survive. Saves less when the owner is away. | **Chosen.** |
| D. Hand off to a fresh manager session | Clean reset, but children have the manager's id in their prompts; every message route and the send budget would need redesign. | Dropped. |
| Slimming alone | Safe, but context still grows without bound. | Part of C. |

Any compaction (the owner's own, or the 1M auto-compact) truncates the skill, so the restructure in "A compaction-safe skill" is needed in every approach.

## Design

### 1. Asking the owner to compact

- `run.py status` prints a `- context: <N>k` line: the session's current context size, from the last assistant entry's usage (input + cache read + cache creation) in its transcript. The script finds the transcript as `~/.claude/projects/*/<CLAUDE_CODE_SESSION_ID>.jsonl`. With no session id, no file, or no usage entry, it prints no `context` line and nothing else changes.
- After handling a wake, when the context is at or above **300k** and the run is quiet, the manager ends its reply with one line the owner can paste:
  `/compact Resume as the implementation manager for <run>: read notes.md, then run.py status, before acting.`
  Quiet means: nothing under "needs the manager" in `status`, no push request waiting for an answer, every started child recorded, and a `wait` running.
- It asks again only after the context has grown another **150k** since the last ask. The last ask's size is kept in `run.json` (for example `run.py set compact-asked <N>`), so the rule survives a compaction.
- The 300k and 150k figures are the owner-approved starting values; the builder may make them `run.py` defaults rather than constants in prose.

### 2. A compaction-safe skill

- **Core SKILL.md under 5,000 tokens**, most important first: role and authorization, the four constraints, the wake loop (handle, then make sure one `wait` runs), the compaction ask, the resume rule, the red flags that guard merges and pushes, and a table of "situation → reference file".
- **The resume rule.** A turn that finds the conversation compacted (a compaction summary, or no memory of the run's details) reads `<run>/notes.md` and runs `run.py status` before acting, then reads the reference file for what it is about to do. It never re-runs Setup or starts children again.
- **Reference files** in `skills/implementation-manager/references/` (names are the builder's call), read with Read when needed: the run script's command table, Setup, Starting children, Integration-branch mode (with the integration child), a unit the owner designs, Event handling (the per-event rows and "Answering a push request"), the merge bar, the owner queue, the `specs` unit, Finishing and the tracker format.
- Moving text keeps its meaning. This change is a move and a trim, not a rewrite of rules; where two copies of a rule exist today, keep one.
- **Decisions on disk.** `notes.md` gets a "Decisions" section. An owner decision that is not an item answer (a WIP change, a scope ruling, a "do X for every unit") goes there in the turn it is made, one line each with the date. Item answers already live in `<run>/items/`. A compacted manager reads it with `notes.md`.

### 3. Briefs from a template

- The fixed text of the child prompt moves into template files in the plugin: parts 1–4, the quoted block and fixed text of part 5, the fixed text of part 7, and parts 8–11 verbatim. The integration child's prompt gets its own template.
- A new `run.py brief <unit> [--extra-file P]` writes `<run>/briefs/<unit>.md`: the templates filled from `run.json` (unit name, title, spec path, base, repo, run folder, manager session id) and the unit-specific text the manager wrote to `--extra-file` (part 6, and any unit-specific additions to part 5). Fields `run.json` lacks (spec path, the manager's session id) are added to `unit`, `init` or `set` as needed.
- The `start_session` prompt becomes a short pointer: the unit name, that this is an implementation-manager unit, and "read `<run>/briefs/<unit>.md` in full and follow it". The gate part stays in the brief file.
- Repository facts the manager knows (formatter, validation commands, PR rules), which today go into parts 5 and 7 by hand, go into `--extra-file` or a run-level file the template includes. Builder's choice.
- The card prompt for work outside every unit uses the same command.

### 4. Fewer calls per watcher wake

- `run.py wait` prints the event line, then the `status` block, so the manager needs no separate `status` call.
- For `REQUEST`, `PENDING` and `NEXT`, the watcher also runs branch-review's `rs covered` for the unit's worktree and sha, and prints its JSON on the next line. The manager then follows "Answering a push request" with the verdict already in hand, and does not load `branch-review:check`.
- For `READY` and `REMIND`, the watcher prints the same verdict for the PR's head sha. **Owner's decision (2026-11-09):** the merge bar accepts this printed verdict in place of loading `branch-review:check`, but only when it was computed for the exact head sha in that event. A verdict for any other sha, or `verdict unavailable`, means the manager runs `branch-review:check` as today. The merge bar text and the red flags "All checks pass, merge" and "The child says its review is clean" are reworded to say so.
- `run.py` learns the `rs` command and branch-review's data folder once: at Setup the manager loads `branch-review:check`, which shows both paths with its placeholders filled in, and records them (for example `run.py set check-cmd …`). If the recorded `rs` path no longer exists (branch-review updated mid-run), the event prints `verdict unavailable: <reason>` and the manager falls back to `branch-review:check` as today, then records the new paths.
- Other events that today send the manager to a file (`pushes.log`, `requests.log`, `run.json`) print the lines it needs with the event, where the event table says to read them. The builder picks which, guided by the transcripts' common sequences: `status`, then a `sed`/`cat`/`grep` of a run file, then the action.

### 5. Trims to the core loop

- Short replies stay short: the one-line reply rules move into the core.
- The command table in the core lists only the commands used on most wakes (`status`, `wait`, `go`, `redo`, `queue`, `check`, `merged`, `item …`); the full table is a reference file.

## Verification

- **Existing evals** (`push-gate`, `push-queue`, `owner-queue`) pass with the default runs, run as ARCHITECTURE.md says. (Designer's call.) `push-gate` stays as it is: its scaffold records no check command, so it now covers the `verdict unavailable` fallback. `push-queue`'s scaffold records the check command with the eval's own state folder, and its `check-ran.md` grader is replaced by one that checks the manager acted on the printed verdict without loading `branch-review:check`.
- **New eval, compaction resume.** The prompt sets up a run folder mid-run (units open, a request in `requests.log`, a Decisions line, an open item) and loads the skill as a compacted session would see it: only the core SKILL.md and a compaction-summary-style prompt. Graders check that it read `notes.md` and ran `status` before acting, answered the request through the gate, did not re-run Setup or start sessions, and honoured the Decisions line.
- **Unit tests** for `run.py brief` (golden output against today's prompt text for a sample unit), the `context` line (a sample transcript, a missing one), `wait`'s status and verdict output (including that a `READY` verdict names the head sha it was computed for), and the recorded check command with a missing path.
- **Size check.** A test asserts the core SKILL.md stays under 5,000 tokens by a stated character proxy (for example 17,500 characters), so later edits cannot silently push rules past the truncation point.
- **Measurement.** After the next real run, compare manager use per unit against this baseline with the same method (weighted input-token equivalents). The analysis scripts are not part of the plugin; ARCHITECTURE.md records the method.

## Documentation

- README: one short section for owners on the compact prompt (what it means, that pasting it is safe, that ignoring it only costs tokens).
- ARCHITECTURE.md: the reference-file layout, the 5,000-token rule and its test, `brief`, the `wait` output, the context line, and the measurement method.
- Tech debt, in the same PR:
  - The `context` line parses the transcript's undocumented JSON layout.
  - The recorded `rs` path can go stale when branch-review updates mid-run; the fallback costs one extra check.
  - The compaction resume relies on the 5,000-token re-injection limit as documented on 2026-11-09.
- Bump `plugin.json` to 0.10.0.

## Left to the builder

- Reference file names and the exact split, within the core's 5,000-token budget.
- Template file format and placeholder syntax.
- Which events print which extra lines, beyond the status block and the push verdict.
- Whether the thresholds are script defaults or skill prose.
- Field names in `run.json` for the new settings.
