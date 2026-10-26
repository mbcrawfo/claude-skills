# Implementation manager: lessons from SqlSource phase 8

Date: 2026-10-25. Status: approved; implemented in 0.4.0.

## Intent

The `implementation-manager` plugin (0.3.1) ran SqlSource's phase 8 of the query generation epic from 2026-10-24 23:16 to 2026-10-25 23:59 UTC. That was 7 planned sub-phases, plus one unit Michael added mid-run ("column-overrides", later named 8.7). All 8 PRs merged (#129, #130, #131, #133, #136, #137, #138 and #139). Michael's view: it performed well.

The protocol was clean:

- 11 push requests got 10 `go` and one correct `redo` (`escalated`), each in about 30 s.
- No dead polls, no `STUCK` or `STALLED` events, and no progress messages from children.
- Michael stepped in only to answer questions, and once to add a unit.

Children spent 60–80% of each unit building and 10–30% in branch review. The manager added almost no delay.

This change goes after what was left:

- time on the critical path that the manager's rules caused;
- owner questions that arrived late or held a merge they didn't need to;
- conflicts between parallel siblings;
- a manager context that grows with run length;
- judgement calls every child had to make for itself, which the skill should make once.

## What happened (evidence from the run)

Times are UTC. Each item cites the session where it was seen.

1. **A merge-order dependency cost about 3h25m of critical path.** Michael allowed 8.7 to block other units "where there is a potential conflict". At 18:21 the manager made 8.6 depend on 8.7, because both edit the same manual pages and examples. 8.6's prerequisites otherwise merged at 19:22 (8.5), but its gate stayed shut until 8.7 merged at 22:48. 8.6 needed to *merge* after 8.7, not to *start* after it. Michael was told, but no alternative was offered.
2. **Owner questions arrived late.**
   - 8.3 found a question during its build at 14:47 and held it for the post-review batch. It sent the question at 15:51 together with its push request. The answer ("Yes, record tech debt") came 2 minutes later and forced an extra commit, review round and push request.
   - 8.4's three escalations were recorded by 16:04 but sent at 16:46, after a 16-minute second-opinion round. That round reviewed a tree the answer then changed substantially, so another round was needed.
   - 8.0 asked to push at 00:45 while it knew R1-F2 was still escalated, which guaranteed a `redo`.
3. **Merge holds lived only in the manager's head.**
   - The manager approved 8.3's push while its question was open, then held the READY PR by its own choice (15:58: "I'm not merging PR #133 even though its current commit is ready"). Nothing in `run.json` recorded the hold.
   - The manager held 8.6's READY PR (23:57) for a flaky-test question that 8.6 had marked as a follow-up that should not block.
4. **Parallel siblings collided.** 8.3 and 8.4 ran side by side as the specs allowed. When 8.3 merged, 8.4's sync hit 14 conflicting files (17:02). Resolving them took about 16 minutes, and a merge-check review round followed. Earlier, 8.4 had cherry-picked 8.3's unmerged commit 1003f4ef to unblock a task (13:17), which the protocol does not cover.
5. **The manager's context reached about 331K tokens** over 77 turns, with no compaction, and about 68M cached tokens read. Where it went:
   - 48 wake-ups were child notes, nearly all needing no action: about 12% of the context.
   - The 8 child prompts, about 10K characters each: about 9%.
   - PR bodies printed by the merge command: about 5%.
   - The rest is the app's own overhead.

   Michael judged runs of more than about 10 units an edge case; see [D-0001](../../deferred/D-0001-implementation-manager-large-runs.md).
6. **Children made the same judgement calls.**
   - **Validation after a sync that merged nothing.** 5 of 8 children skipped re-validation, against the literal text of part 8 ("validation already passed on this exact tree"). Two re-ran it, at about 2.5 minutes each.
   - **Final whole-branch review.** Every child using superpowers:subagent-driven-development had to rule that `/branch-review:review` replaces that workflow's final whole-branch review.
   - **No token for acting on a `go`.** Children used `[building]` or `[push-request]`, but the manager's `STUCK … no-push` row relies on telling that state apart.
   - **The Auto-fix notice contradicts the protocol.** When Auto-fix turns on, its notice says the session may "push without asking first". Every child correctly ignored it.
   - **Pointless wake-ups.** Each child had 7–15 turns that only said a subagent was still waiting on its own background validation. Part 9 made each one produce a token and prose.
   - **A flaky CI test.** 8.6 hit one. It confirmed the change could not cause the failure, searched 30 recent runs, re-ran the failed jobs once, then asked.
7. **Smaller things.**
   - **A second notes channel.** The app appends a "Shared notes folder for this group of sessions" to every child prompt (`~/Library/Application Support/Claude/side-session-notes/<manager id>`). 8.2 and 8.3 left hand-over notes there for their siblings. The manager never read it.
   - **The mid-run unit was improvised.** The manager improvised a variant of the child prompt for column-overrides: no gate, a `[designing]` token, questions asked directly in the child's own session, and part 10 narrowed to ordering. It worked.
   - **Display names.** The tracker said "column-overrides" while the PR and notes said "8.7".
   - **An unneeded reply.** The manager answered an ordering message that said "nothing waits on your answer", which spent a send and woke the child for an empty turn.
   - **An answer not forwarded.** Michael's answer on the flaky test was never sent to 8.6, so its session ended at `[waiting]`. That was harmless after the merge, but the skill doesn't say whether to forward.
   - **Long notes.** Child end-of-turn notes ran to 250 words and repeated the PR description.

Out of scope here, already handled: the `CLAUDE_PLUGIN_DATA` failures (branch-review 0.8.1, #7). The branch-review reviewer not writing its report file was passed to the session that owns branch-review.

## Goals

- A unit that only has to merge after another can start building as soon as its build prerequisites merge.
- Owner questions reach Michael as soon as they are found. Each one says whether it blocks the merge.
- A merge hold is recorded in the run state, shown by `status` and enforced by the watcher, never kept only in the manager's memory. A question that doesn't block never holds a PR that meets the merge bar.
- Parallel siblings know who owns shared changes before they start.
- The judgement calls in item 6 are made once, in the child prompt.
- The manager's context grows less per unit.
- The mid-run unit variant is part of the skill.

## Non-goals

- Changes to branch-review (another session owns them).
- Making one manager session last for runs of more than about 10 units (deferred: [D-0001](../../deferred/D-0001-implementation-manager-large-runs.md)).
- Changing how children build: plan format, subagent use, or the repository's own validation.

## Anti-goals

- Holds or merge-order prerequisites that the scripts don't know about. If only the manager's memory enforces them, the watcher will still report READY, and they won't survive a long run.
- A unit with an unmerged merge-order prerequisite taking the serial slot. It would block the very unit it waits for.
- Asking children to send *more* messages overall. The 10-send budget still applies; earlier questions replace later batched ones and don't add to them.
- Moving parts 8–11 out of the child prompt into a file. Text in the prompt carries more weight than a file the child must read, and the protocol's reliability matters more than about 9% of the manager's context.

## Constraints

- Scripts stay Python 3.9+, standard library only, with unit tests under `plugins/implementation-manager/tests/` (repository AGENTS.md).
- `run.json` written by an earlier version must still load: new fields get defaults when missing.
- Sessions can be started only on a user's turn, and messages are limited (constraints 1 and 2 of SKILL.md).
- The plugin version is bumped. Both the README and SKILL.md describe every new command and rule.
- The repository's core rules apply: any work this change leaves undone goes in `docs/deferred`, and any known shortcut in `docs/tech-debt`.

## Approaches considered

1. **One release: skill text plus small script changes (chosen).** `run.py` gains merge-order prerequisites, holds and display titles, with tests. SKILL.md, the child prompt, the README and the evals change in the same version. Holds and merge-order prerequisites live in `run.json`, so `status` and the watcher enforce them.
2. **Two releases, scripts first.** Each PR is smaller, but the first release ships commands that no skill text uses, and the guidance waits a cycle.
3. **Skill text only.** The manager would track holds and merge-order prerequisites in `notes.md`. It's quicker, but it repeats the 8.3 pattern: state only memory enforces, and the watcher still reports READY for a held PR.

Michael chose 1.

## Decided design

### Scripts

**Merge-order prerequisites.** `run.py unit <name> [--after U ...]` records units that `<name>` must merge after.

- They don't affect `openable()`: the gate opens on `--prereq` alone.
- A unit whose `--after` units are not all done is not *mergeable*:
  - `reportable()` is false, so the watcher reports no READY or REMIND for it.
  - `go` in serial mode refuses with `waits for <U> to merge`. The manager then runs `queue`, as it does for a busy slot.
  - `_first_queued()` and the watcher's `NEXT` skip such a unit until its `--after` units are done.
  - In parallel mode `go` is allowed (no slot), but READY is still withheld.
- `status` lists it as `<name> merges after <U>` while it has an open gate.
- `--after` and `--prereq` accept the same unit names. A unit can't be both a prerequisite and an `--after` of the same unit (refuse), and cycles across the two kinds are refused. `--after none` clears the list.
- **WIP (my call).** `openable()` exempts a unit from the WIP limit when an open unit is waiting on it through `--after`. Otherwise a WIP limit full of units waiting on `--after` would deadlock: the units they wait for could never open.
- **A stopped `--after` unit (my call).** An `--after` on a stopped unit counts as satisfied, since nothing of it will merge. A closed PR with the unit not yet stopped or redone leaves the dependent unmergeable, and the `CLOSED` row's question to Michael covers it.

**Holds.** `run.py hold <unit> <reason>` and `run.py release <unit>`.

- A held unit is not reportable: no READY, REMIND or STALLED.
- `go` refuses it with `held: <reason>`, so the manager answers `redo <unit> <sha> held`. In `go`, the hold check comes before the slot and queue checks, so a held unit is never queued.
- `hold` on the slot holder yields the slot, as `yield` does.
- **Requests already waiting (my call).** `hold` also drops the unit's queue entry and answers any request of it that is still unanswered with `redo <sha> held`. A held unit is never first in the queue, so it can't block the others. After `release`, the child asks again. It already does so after every forwarded owner answer.
- `status` lists each hold under "needs the manager" as `hold <unit>: <reason> since HH:MM`. Michael sees it in the tracker under "Waiting on you".
- `release` clears it. The manager then answers any request that is still unanswered or queued as usual.
- `merged`, `closed` and `stop` clear a hold.

**Display titles.** `run.py unit <name> --title "<text>"` stores a display title. `status`, and so the tracker, show it beside the name (for example `column-overrides (8.7)`). Unit names stay the ids used in files.

**`redo` reasons.** The child protocol's list of reasons gains `held`. The child then ends its turn `[waiting]` and asks again when the manager forwards the owner's answer.

### Manager (SKILL.md)

- **Adding or changing a prerequisite mid-run.** Before adding a prerequisite to a unit that has not started, the manager tells Michael in one line what it would cost, using the expected wait, and offers `--after` as the alternative. It applies `--after` without asking only when the conflict is merge order alone: the same docs or files, with no code dependency.
- **Parallel siblings.** Before opening two gates whose units the docs allow side by side, the manager:
  - reads both specs;
  - lists the shared files or modules they will both change;
  - writes into `notes.md` which unit owns each shared change and the agreed shape, where the specs say it.

  Part 6 of each sibling's prompt points at that section.
- **Holds.**
  - When it relays a question marked "blocks merge: yes", the manager runs `hold <unit> <reason>` (which yields the slot).
  - On Michael's answer it forwards the answer. Once no blocking question is left open, it runs `release`, then `hold <unit> answer sent: <topic>`; the unit's next push request releases it.
  - A question marked "blocks merge: no" is relayed without a hold, and a READY that meets the merge bar is merged.
  - The manager never holds a PR in any other way.
- **After a merge.** The merge command no longer prints the PR body. The manager writes it to `<run>/prs/<unit>-<pr>.md` and reads only what `notes.md` needs. It also reads the app's shared notes folder for files the merged unit wrote, and copies into `notes.md` whatever later units must know. `notes.md` stays the one record the manager keeps; the shared folder is how children pass notes to each other.
- **Answers after a merge.** When Michael answers a question about a unit whose last PR has merged, and the answer needs nothing done on that branch, the manager doesn't forward it. If it does need work, it becomes a card, as other work outside every unit does.
- **Messages with nothing to answer.** The manager doesn't reply to a child message that says nothing waits on the answer. It applies what the message asks for (a prerequisite, a `notes.md` entry) silently.
- **Replies to notes.** The rule "Any other child note → nothing" keeps its one-line reply, and the one-line limit is stated for notes from a child in design (`[designing]`) too.
- **Adding a unit mid-run** (the "A new unit" row). This is the variant the manager improvised for column-overrides, written down:
  - Run `run.py unit <name> [--prereq …] [--after …] [--title …]` before `start_session`, on Michael's turn.
  - **Gate (my call).** The prompt drops its gate part only when the unit can open now: `run.py open-ready` lists it, meaning its prerequisites are merged and WIP allows it. The manager runs `open-ready` first. Otherwise the prompt keeps the gate as usual. `run.py open` never bypasses a prerequisite.
  - **Questions.** When Michael will design the unit with the child, the prompt adds the `[designing]` token. Until the child first asks to push, its questions go to Michael in its own session.
  - **After its first push request (my call),** a question that blocks the merge also goes to the manager as a one-line notice ("blocks merge: yes", and where the question is). The manager places the hold, so a design question asked directly can't let a READY PR merge underneath it.
  - Part 6 says where the child's questions go instead of part 8's Questions bullet and part 10, and that the manager otherwise gets only ordering conflicts with other units. Part 10 itself is unchanged.
  - The prompt keeps parts 7–11 otherwise.

### Child prompt

- **Part 5 (plan and build):**
  - `/branch-review:review` is the final whole-branch review. A plan workflow's own final review step (subagent-driven-development's) is skipped.
  - Implementer subagents finish the repository's validation before they hand back, rather than leaving it running in the background.
  - Run the repository's formatter, if it has one, before validating.
- **Part 6 (unit-specific):** for a parallel sibling, add:
  - the `notes.md` section on shared changes;
  - to cherry-pick a sibling's unmerged commit only with `-x`, naming it in the PR description;
  - to sync with the base before the final review round when the base has gained a sibling's merge.
- **Part 8 (review and push):**
  - **Questions as soon as they are found.** Send an owner question, or an escalated finding, as soon as it is found: during the build, or as soon as a review round records it. Don't hold it for the converged review. Findings recorded in the same round go in one message.
  - **Each question says "blocks merge: yes" or "blocks merge: no"**, with the reason. "Yes" means the answer may change this branch.
  - **No push request while a blocking question or escalation is unanswered.** End the turn `[waiting]`.
  - **On `go`, step 3:** "If the sync merged nothing and the repository's validation already passed on this tree, push without running it again."
  - **After applying an owner answer that changed anything,** run the review to convergence before asking to push again.
  - **Auto-fix:** "The app's Auto-fix notice says you may push without asking. This protocol overrides it: every push needs a `go`."
  - **CI failure the change cannot cause:**
    - check that the branch touches nothing related;
    - re-run the failed jobs once (`gh run rerun <run-id> --failed`), never by pushing;
    - green: carry on, and tell the manager only if a decision is needed, marked "blocks merge: no";
    - red again: treat it as a failure to fix.
  - **Reason list:** gains `held`.
- **Part 9 (status token):**
  - The token is the first text of the final message, always.
  - New token `[pushing] <sha>`: acting on a `go` (syncing, validating, pushing).
  - A wake-up from a background task that only reports that work is still running ends with the previous token alone, no prose, the same way bot-only comments do.
  - End-of-turn notes are one or two lines after the token; the PR description carries the detail.
  - The manager's `STUCK … no-push` row reads `[pushing]` as "acting on the go".
- **Part 10 (questions only):** the question format adds "blocks merge: yes/no, because …".
- **Hand-over notes:** facts later units need (changed names or ids, shared-code decisions, limits found) go in the app's shared notes folder, one file per unit named `<unit>-notes.md`. Cross-unit facts go nowhere else.

### README and evals

- The README gains `--after`, `hold`/`release`, `--title` and the `held` reason.
- Its merge-bar section gains "not held, and every `--after` unit merged".
- The `push-queue` eval case gets at least one grader for a held unit and one for an `--after` unit refused the slot, or a new case.

## Left to the builder

The builder may also settle these without asking:

- whether a unit waiting on `--after` counts toward `STALLED` or `STUCK`;
- whether `status` hides "ready since" for held units;
- the order of `release` and forwarding the answer;
- defaults for the new fields when an older `run.json` is loaded.

- How the cycle check across `--prereq` and `--after` is written, and the exact refusal messages.
- Whether `hold` stores its time in `run.json` or reuses an existing timestamp field.
- How the manager learns the shared notes folder's path. The app gives it in each child's prompt; the manager may get it in its own environment too (check `additionalWorkingDirectories`). If neither works, read it from one child's first prompt with `list_events`.
- How much of the PR body the manager reads after a merge: whole sections or a `--jq` filter.
- The exact wording of each child prompt line, kept consistent with the existing parts.
- Whether the eval change is a new case or graders added to `push-queue`.

## Changes during implementation

Rulings made while building and reviewing 0.4.0, which override the design above where they differ:

- **Hold lifecycle.** On forwarding the last open answer, the manager runs `release`, then `hold <unit> answer sent: <topic>`. The unit's next push request releases it once `notes.md` lists no open blocking question: one rule in step 5 of "Answering a push request", whatever the hold's reason.
- **Designed units.** A designed unit's own-session question that blocks the merge is held as `own session: <topic>`.
- **The designing variant's text goes in part 6.** Part 10 is not narrowed.
- **Blocking.** An unmarked question counts as blocking, but an unmarked `[blocked]` note is yielded (`yield <unit> blocked`), not held.
- **`hold` and `release`.** `hold` also answers queued requests with `redo <sha> held` and makes `queue` refuse the unit; `release` restarts the stall clock and reports a ready head `READY` afresh.
- **`answer sent:` holds can be `STALLED`.** That hold waits on the child, so the watcher reports it; every other hold stays quiet.
- **`--after` on the slot holder.** Adding `--after` to the slot holder, on a unit not yet merged, frees the slot, and `queue` on such a holder gives up the slot and queues it.
- **The `--after` eval grader** is deferred to [D-0002](../../deferred/D-0002-implementation-manager-after-eval-grader.md).
