# Settings-files run review: design

Date: 2026-10-27. Source: the SqlSource "Settings files" run (manager plus 8 children, PRs #143–#158), which used implementation-manager 0.5.0 and branch-review 0.10.0. Changes go into implementation-manager 0.7.0 and branch-review 0.12.0.

## What the run showed

- **Turn-end notes are noise.** The app sends the manager a note at every child turn end. In this app every Agent call is async, so a child ends a turn at each subagent dispatch: 12 to 72 turn ends per child. The manager took no action on almost all of them. They cost 88 of its 271 API calls and 27M of its 82M cache-read tokens. Everything the manager acts on reaches it through `run.py wait`, `requests.log` and child messages.
- **Pointless child wake-ups.** Children told their implementers to run validation "in the background and wait". This echoed the app's own "run long commands in the background" guidance. The result was about 50 wake-ups with nothing to do (roughly 25M tokens). 0.6.0's foreground rule tells the child, but nothing makes it reach the implementers' briefs.
- **Escalations lost detail.** sf-3 paraphrased a finding's fix option and dropped two of its properties. The owner approved the narrower fix, and a second-opinion round reopened it (about 1h50m). sf-2 applied part of an owner answer, closed the finding and never reported what remained. Layer 6 found the same gap, and the owner had to decide again.
- **Fixers recorded tech debt on their own.** Two fixer subagents in sf-3 added a tech-debt entry without the owner, which cost two fix rounds.
- **Owner answers recorded by hand.** sf-6 ran the state script's `resolve` by hand instead of going through the review skill. It then edited the intent after the round had begun.
- **Integration child:**
  - It skipped the `syncing` line on its one conflicted merge, leaving unit merges unguarded for about 9 minutes.
  - It recorded 3 of 7 rounds with a "first record" reason.
  - It skipped the formatter before its final-phase reviews, so validation failed on the push.
- **A child proposed a session card.** The integration child proposed one for main's flaky test.
- **Sonnet follow-up checks missed gaps.** Twice a Sonnet follow-up called an owner-decided fix verified although a gap remained (sf-2 round 3, sf-7 round 2). Both gaps resurfaced later (layer 6 R1-F1, integration R9-F3).
- **An unchecked docs sentence caused churn.** sf-7 answered an "unmentioned behavior" item with a new docs sentence it had not checked. That opened two more rounds.
- **A reviewer edited its own report.** On a re-emit request, a reviewer rewrote its report file in place.
- **Already fixed in 0.6.0 / 0.11.0:**
  - per-task quality reviews skipped (40 minutes to 1.5 hours per child);
  - sync points on owner answers;
  - `fix_options: []` accepted;
  - `decisions_since_intent` reported.

## Changes

### implementation-manager 0.7.0

1. **Detach every child** right after recording its branch and worktree (`mcp__ccd_session_mgmt__detach_session`). The app then stops sending the manager turn-end notes. `list_events`, `get_session`, `archive_session` and messages by session id work for any session.
   - If detaching is refused or unavailable, carry on; notes stay harmless, since the manager ignores them.
   - `[blocked]` reaches the manager as a message, since notes no longer do.
2. **Part 5 gives a block the child pastes word for word** into every implementer and fixer brief:
   - foreground commands with a 600000 ms timeout, overriding the app's background guidance;
   - no tech debt or follow-ups without the owner;
   - a short hand-back.
   The child runs its own validation in the foreground too.
3. **Part 8 rules:**
   - Escalations quote every `fix_options` entry word for word.
   - An owner answer applied only in part sends the rest to the manager (blocks merge: yes) before any push request.
   - Owner answers are recorded only through `/branch-review:review --resolve`.
   - The formatter (and format check) runs before every review round.
4. **Part 10:** a child never proposes or starts sessions; it sends the work to the manager as a "blocks merge: no" question.
5. **Part 6:** a unit with `--after` units is told that its push request waits until they merge.
6. **Integration child:**
   - the `syncing` line is the first command after a conflicted `sync_with_base_branch`, wherever it runs;
   - the "first record" reasons apply only to the Record at Start;
   - fixes after the final line are formatted and validated before each review round.

### branch-review 0.12.0

1. **`begin-round` raises the model for some follow-ups.** When the follow-up has a claimed fix (`fixed`) of a finding whose developer decision is `fix`, and the orchestrator passed `--deep-model`, the round gets the deep model. It returns `model_reason: decided-fix`.
2. **Follow-up step 2** checks a decided fix against every case the decision's words name. A fix that rewrites a user-facing promise is re-checked against the other inputs that reach it, not only the finding's trigger.
3. **Deep step 5:** a family of properties, paths or settings that play one role is an equivalence class; list it in `variants`, and each `fix_options` entry covers the whole family.
4. **Section 7:** an unmentioned-behavior item is never answered with an unchecked new statement in the repo. Check it against the code first, or put it in the intent or PR description.
5. **Reviewer:** on a re-emit request, reply with the JSON block only and never touch `REPORT_PATH`.

## Not changed

- **Validation before the integration child's push request on clean merges.** It would shorten the `go` hold, but units merging during that validation would cause `behind-integration` redos. The hold is 3.5–7.5 minutes per sync, which is acceptable.
- **Making `begin-round` refuse on `decisions_since_intent`.** 0.11.0 reports it and the skill acts on it; it was not exercised in this run.
