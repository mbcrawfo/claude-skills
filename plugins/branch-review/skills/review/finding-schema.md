# Finding schema

Reference for the reviewer output block and the `review.json` state file. The reviewer agent produces the block; `review_state.py record-findings` validates it and writes the ledger.

## Reviewer output block

The reviewer ends its final message with one fenced `json` block. The block must be the last fenced ```json block in the message: `record-findings` takes the last one and ignores earlier ones, so example snippets earlier in the report are safe. The fence tag matches in any case (```` ```JSON ```` too) and CRLF line endings are accepted. `record-findings --file` also accepts a file that is only the JSON object; when that file is not valid JSON, the error says `invalid JSON`. The file must be UTF-8.

```json
{
  "summary": "Two sentences on what the change does and its main risk.",
  "merge_risk": {"level": "minimal|low|moderate|high", "reason": "One sentence."},
  "intent_check": {"unmet_claims": [], "unmentioned_behavior": []},
  "verdicts": [{"id": "R1-F2", "status": "verified|still-open", "reason": "..."}],
  "findings": [
    {
      "severity": "critical|major|minor",
      "category": "logic|sibling-path|security|tests|intent|conventions|docs|build-ci",
      "file": "path/from/repo/root",
      "lines": "12-20",
      "title": "Imperative sentence.",
      "trigger": "Input or state that causes the problem.",
      "consequence": "What goes wrong.",
      "reachable_now": "yes|no|unclear",
      "reachable_note": "Optional. Why reachable or not.",
      "evidence": "Optional. Code or output backing the claim.",
      "fix": "What to change.",
      "test": "Optional. A test that would catch it.",
      "source": "Optional. Where the reviewer learned this.",
      "effort": "quick|heavy"
    }
  ],
  "outside_change": [{"file": "path", "lines": "10-12", "title": "...", "detail": "..."}],
  "coverage": [{"item": "logic", "done": "What was run or read, or 'not applicable: <why>'."}],
  "not_checked": [{"what": "What was not checked.", "reason": "Why it could not be."}]
}
```

A finding may also carry `variants` (a list of `{"input", "result"}`, `result` being `fails` or `ok`: the members of an equivalence class the reviewer tried) and `fix_options` (two or more alternative fixes, as strings; `fix` then holds the recommendation). Both default to `[]`. `fix_options` with fewer than two non-empty entries is refused.

**Coverage.** In a `deep` round begun by plugin 0.9.0 or later, `coverage` and `not_checked` are required. `coverage` must have an entry whose `item` is each checklist category (`logic`, `sibling-path`, `security`, `tests`, `intent`, `conventions`, `docs`, `build-ci`) and each required step (`break-tests`, `doc-grep`, `fulfilled-promises`, `real-system`, `related-records`, `broad-mappings`, `measurement-tests`, `consumer-build`), with a non-empty `done`; extra items are kept. `not_checked` entries need a non-empty `what` and `reason`. A deep report that leaves an item out is refused with `coverage is missing <items>`. Follow-up and merge-check reports, and rounds begun under 0.8 or earlier, may leave both out; when present they are validated and stored. The round stores both, as `[]` when absent.

Required top-level keys: `summary` (string), `merge_risk` (`level` from the enum, `reason` a non-empty string), `intent_check` (`unmet_claims` and `unmentioned_behavior`, both lists of strings), `verdicts`, `findings`, `outside_change` (all lists, may be empty).

Required finding keys: `severity`, `category`, `file`, `lines`, `title`, `trigger`, `consequence`, `reachable_now`, `fix`. Optional: `reachable_note`, `evidence`, `test`, `source` (default `""`) and `effort` (default `"quick"`). `lines` is a string.

Verdicts are for earlier findings only: the entries of `OPEN_FINDINGS`, which a follow-up round always has and a deep round has when findings are carried forward. Give each entry one verdict and never re-report it as a new finding. `id` must exist in the ledger, name a finding whose status is `open`, `fixed` or `still-open`, and appear in at most one verdict; `status` is `verified` or `still-open`; `reason` is optional. Any violation rejects the whole block and leaves the ledger untouched.

`outside_change` entries require `file` and `title`; `lines` and `detail` default to `""`. `severity` and `category` may be given (same enums) and default to `minor` and `logic`.

## Ids

Finding ids are `R<round>-F<k>`. `k` counts from 1 within the round, in the order of the reviewer's `findings` list; `outside_change` entries are numbered after the in-change findings of the same round. Ids are never reused.

## review.json

Stored at `<state-dir>/<repo_key>/<branch_key>/review.json`. `repo_key` joins every path segment of the origin URL with `__`, lowercased (`owner__repo`, or `group__sub__repo` for a GitLab subgroup); without a usable origin URL it is `local-<hash of the git common dir>`, which linked worktrees share. `branch_key` is the branch name with `/` as `__`, or `detached-<first 7 hex digits of HEAD>`. The raw reviewer report of a round, when kept (`record-findings --report`), is `rounds/<N>.md` beside it; it is copied before the ledger is saved, so a failed copy leaves the ledger untouched.

Top level:

| key | meaning |
| --- | --- |
| `repo_key`, `branch` | identity of the reviewed repo and branch |
| `base_ref` | ref the branch is reviewed against |
| `intent` | stated intent of the change, or null |
| `second_opinion` | `{"requested", "skipped"}`, the invoker's choice, set by `begin-round` (see Second opinion); absent in v0.7 state |
| `generated` | the stored `--generated` globs, normalized like `paths`; absent in v0.7 state |
| `notes` | the text of the last `begin-round --notes-file`, or null; an empty file clears it |
| `related_branches` | the refs of the last `begin-round --related-branch`; `--related-branch .` clears them |
| `behavior_to_confirm` | list of `{"text", "round"}` added by `confirm-behavior`; exact duplicates are skipped |
| `created`, `updated` | ISO 8601 UTC timestamps |
| `rounds` | list of round objects |
| `findings` | list of finding records |

Round object: `number`, `mode` (`deep`, `followup` or `merge-check`), `model`, `started`, `snapshot`, `head` (the commit the snapshot was taken on: its parent for a dirty snapshot, else the snapshot itself), `base_ref`, `merge_base` (of `head` and `base_ref`; `begin-round` exits 1 with `base ref not found: <ref>` when the base ref does not resolve), `diff_from`, `diff_to`, `changed_files`, `paths` (normalized: no leading `./`, a directory as `dir/**`; `.`, `./` or `/` means the whole repo and gives an empty list), `focus` (`second-opinion` or null), `mode_reason` (the `--mode-reason` given, `locate`'s reason, or `large-delta`), `conflicts` (merge-check rounds only: the conflicted paths), `plugin_version` (the plugin version that began the round; v0.7 rounds have none), `intent` (the state's intent after this round's `--intent-file`, so each round keeps the intent it was reviewed against; 0.8 rounds have none), then filled by `record-findings`: `summary`, `merge_risk`, `intent_check`, `coverage`, `not_checked`, `added`. Before recording, `summary` and `merge_risk` are null and `intent_check` and `added` are absent. `added` is the number of new in-change findings the round produced; outside-change entries are not counted.

A round with `added` is recorded. A round without it was begun but its reviewer never reported, so it reviewed nothing: it never anchors a follow-up, `begin-round` drops it before adding the next round, and `status` and `summary` exit 1 while it is the last round. `begin-round` never saves an empty round (no changed files): it returns `saved: false`, and creates no state when none existed.

Finding record:

| key | meaning |
| --- | --- |
| `id` | `R<round>-F<k>` |
| `status` | see below |
| `scope` | `change` (in the diff) or `outside` (from `outside_change`) |
| `round_opened` | round that reported it |
| `round_closed` | round that closed it, else null |
| `resolution` | null, or `{"status", "note", "commit", "by", "round"}`, plus `intent_quote` when `set-status --intent-quote-file` settled fix options |
| `decision` | the developer's latest `resolve`, `{"decision", "note", "by", "round"}`; `set-status` never changes it; absent before any `resolve` |
| `discussion` | list of `{"from", "text", "round"}`; `from` is `orchestrator` or `reviewer` |
| reviewer fields | `severity`, `category`, `file`, `lines`, `title`, `trigger`, `consequence`, `reachable_now`, `reachable_note`, `evidence`, `fix`, `test`, `source`, `effort`, `variants`, `fix_options` |

Outside-change records carry `file`, `lines`, `title`, `detail`, `severity` and `category` instead of the trigger/consequence fields, with status `outside`, `round_closed` null and `resolution` `{"status": "outside", "note": "outside the change set", "by": "reviewer", ...}`. v0.7 stored them as `deferred`; every load maps a finding with scope `outside`, status `deferred` and that note to `outside`, so `deferred` means only a developer's deferral. A verdict's `reason` is appended to the finding's `discussion` as a `reviewer` entry.

## Statuses and transitions

| status | meaning |
| --- | --- |
| `open` | reported, not yet addressed |
| `fixed` | orchestrator claims a fix; awaiting reviewer confirmation |
| `still-open` | reviewer says a claimed fix did not resolve it |
| `verified` | reviewer confirmed the fix (terminal) |
| `invalid` | orchestrator judged it not a real problem (terminal) |
| `escalated` | needs a developer decision |
| `deferred` | developer chose to fix later (terminal) |
| `accepted` | developer chose to keep it as it is, documented or not (terminal) |
| `outside` | reported in `outside_change`; not part of the fix loop |

Transitions are the ones the review skill follows:

- `open` or `still-open` to `fixed`, `invalid` or `escalated`: `set-status`. `fixed` needs `--commit` or a note; `invalid` and `escalated` need a note (`--note` or `--note-file`).
- any status back to `open`: `set-status --status open`.
- `fixed` (or a carried `open` or `still-open`) to `verified` or `still-open`: reviewer verdict. `verified` sets `round_closed`; `still-open` clears it.
- `escalated` to `open` (decision `fix`), `deferred` (decision `defer`) or `accepted` (decision `accept`): `resolve`. `defer` and `accept` set `round_closed`.
- `outside` to `escalated` (the repo forbids deferring without the developer) or `fixed` (a trivial fix, or the developer said to fix it): `set-status`.

The script enforces only part of this: `resolve` works only on an `escalated` finding, a verdict is accepted only for a finding that is `open`, `fixed` or `still-open`, and `set-status` takes only `fixed`, `invalid`, `escalated` or `open` as the target, with the notes above. It does not check the status a `set-status` moves from; following the transitions is the skill's job.

`invalid` and `escalated` set `round_closed` in `set-status`; `fixed` and `open` leave it null.

**Fix options.** `set-status --status fixed` on a finding with two or more `fix_options` is refused unless the finding's `decision` is `fix` (the developer chose with `resolve --decision fix`), or `--intent-quote-file PATH` holds text that, with runs of whitespace collapsed and ends stripped, occurs in the `intent` stored on the round that opened the finding (`round_opened`), compared case-sensitively. An empty quote never matches. The refusal is one line naming both ways out; a round with no stored intent gives `no intent stored for round <N>`.

**confirm-behavior.** `confirm-behavior --text-file PATH` adds a behavior change that existing users rely on to `behavior_to_confirm`, for the developer to confirm in the PR. It is not a finding and blocks nothing; it prints `{"behavior_to_confirm": <count>}`. An empty file is refused.

**Summary files.** `record-findings`, `set-status`, `discuss`, `resolve` and `confirm-behavior` rewrite `summary.md` and `review-notes.md` after saving, unless the latest round is unrecorded; a failed write leaves the old files and does not fail the command.

Text arguments have file forms: `set-status --note-file PATH`, `discuss --text-file PATH` and `resolve --note-file PATH` read the text from a UTF-8 file and strip surrounding whitespace. Each is mutually exclusive with its inline form (`--note`, `--text`). `discuss` and `resolve` reject an empty file; for `set-status` an empty file is no note.

## status

`status` prints `rounds`, `last_mode`, `counts` (findings per status), `continue`, `reason`, `next_mode`, `next_focus`, `second_opinion_reasons` and `second_opinion_held`. The reason is the first that applies:

| reason | continue | when |
| --- | --- | --- |
| `open-findings` | true | a finding is `open` or `still-open` |
| `second-opinion` | true | nothing is `open` or `still-open` and a second opinion is due; `fixed` findings are carried into it for verdicts; `next_mode` is `deep`, `next_focus` is `second-opinion`, and `second_opinion_reasons` says why |
| `unverified-fixes` | true | only `fixed` findings are unsettled |
| `awaiting-decisions` | false | the loop would stop and a finding is `escalated` |
| `converged` | false | otherwise |

`escalated`, `invalid`, `deferred`, `accepted`, `outside` and `verified` findings never keep the loop going. `second_opinion_held` is true when a second opinion would be due but waits for escalated findings. `next_mode` and `next_focus` are null except as shown here and for `final-verification`; `second_opinion_reasons` is `[]` unless the reason is `second-opinion`.

**Round cap.** The floor is the later of `--since-round N` (default 0) and, when a round's `mode_reason` is a forced deep restart (`snapshot-missing`, `history-rewritten`, `paths-changed`), the round just before the last such restart. The cap counts the rounds numbered above the floor, so the restart itself counts. When the loop would continue and six or more rounds count:

- `unverified-fixes` with exactly six becomes `final-verification` (`continue: true`, `next_mode: followup`): one more follow-up only to verify the claimed fixes;
- `second-opinion` stays, so a due second opinion still runs; once one is recorded it is no longer due, and the cap applies to whatever follows;
- anything else becomes `max-rounds` (`continue: false`).

Merge-check and second-opinion rounds count toward the cap and do not reset it.

## Second opinion

`begin-round --second-opinion` sets `second_opinion` to `{"requested": <this round's number>, "skipped": false}`; `--no-second-opinion` sets `{"requested": null, "skipped": true}`. Either is stored whenever the state is saved, even by an empty round that is not (`requested` is then the number that round would have had), and stays until a later run passes the other.

One function decides whether a second opinion is due, from recorded state alone; `status`, `locate` and `covered` all use it. It is never due while a finding is `escalated`: the developer's answer may add code the second opinion must see, so it is held (`second_opinion_held`) and becomes due once nothing is escalated. Its reasons, in this order:

- `requested`: `requested` is set and no second-opinion round numbered `requested` or later is recorded.
- `merge-risk`: a `deep` round of the current cycle has `merge_risk.level` `moderate` or `high`.
- `major-finding`: a finding with scope `change`, opened in the current cycle, has severity `critical` or `major`, whatever its status now.

The current cycle starts at the last recorded forced deep restart, else at round 1. `merge-risk` and `major-finding` count only when `skipped` is false and no second-opinion round is recorded on the branch, so they fire at most once per branch. State whose recorded rounds all lack `plugin_version` (v0.7) never gets `merge-risk` or `major-finding`; `requested` still applies. While one is due, `covered` lists the reason `second-opinion-pending`, after all its other reasons.

## Mode selection

`locate` picks the next round's mode from the last recorded round (the anchor) and returns it with a reason:

| reason | mode | when |
| --- | --- | --- |
| `no-state` | deep | no `review.json` for the branch |
| `no-rounds` | deep | no recorded round |
| `snapshot-missing` | deep | the anchor's snapshot commit is gone |
| `history-rewritten` | deep | HEAD does not descend from the anchor's HEAD |
| `base-moved` | deep | the merge base of HEAD and the base ref differs from the anchor's `merge_base`, and either HEAD has no merge base with the base ref, or merging the base's new commits into the anchor's snapshot conflicts and the conflicted paths cannot be listed (git older than 2.40) |
| `paths-changed` | deep | the normalized `--paths` given to `locate` differ from the anchor's `paths` |
| `base-moved` | merge-check | as deep `base-moved`, but `git merge-tree --write-tree --name-only` lists the conflicted paths; `conflicts` holds them |
| `base-updated` | followup | the merge base differs from the anchor's `merge_base`, but merging the base's new commits into the anchor's snapshot is clean; the delta excludes the base's code |
| `continuing` | followup | none of the above |
| `second-opinion` | deep | the row above is `continuing` or `base-updated`, no finding is `open` or `still-open`, and a second opinion is due; `focus` is `second-opinion` and the `fixed` findings in `open_findings` get verdicts |

`locate --second-opinion` or `--no-second-opinion` counts the invoker's flag in the `second-opinion` row as the next `begin-round` will store it (a request numbered as the next round, or a skip), so a requested second opinion replaces the follow-up; the escalation hold still applies. The skill passes it on a run's first `locate` only.

The rows are checked in this order. `locate` prints `repo_key`, `branch`, `base`, `dir`, `exists`, `mode`, `reason`, `focus` (`second-opinion` or null), `rounds` (recorded), `open_findings`, `conflicts` (empty unless the mode is `merge-check`), `plugin_version` (the running plugin's version) and `earlier_not_checked` (the `not_checked` entries of the current cycle's recorded deep rounds, one per `what`, the latest kept, each with its `round`). `open_findings` is returned in every case, so a deep round after an earlier one carries them forward. `locate` and `begin-round` use `--base` / `--base-ref` when given, else the stored `base_ref`, else `origin/HEAD`'s target, else `main`.

## begin-round

`--snapshot` is optional: without it, `begin-round` takes the snapshot itself, as `snapshot` does. `--notes-file PATH` and `--related-branch REF...` store `notes` and `related_branches` on the state; absent, they keep the stored values.

`begin-round` prints the new round object plus `notes` and `related_branches` (the stored values), `dir`, `files` and `lines` (all changed files and lines), `generated` (the changed files counted as generated), `counted_files` and `counted_lines` (the rest), `oversized` (more than 150 counted files or 10,000 counted lines), `empty` and `saved`. A merge-check round also prints `upstream_overlap`.

- **Generated files.** A changed file is generated when it matches the `generated` globs, or when `git check-attr` at the snapshot says `linguist-generated` is set or `diff` is unset (git older than 2.40 reads the working tree's attributes). `--generated GLOB...` replaces the stored globs and absent keeps them; `--generated .` clears them, as `--paths .` does. Generated files stay in `changed_files`; they are left out only of the counted values.
- **Large-delta promotion.** A `--mode followup` round whose counted delta has 40 or more files, or 2,000 or more lines, is saved as `mode: deep` with `mode_reason: large-delta`, `diff_from` the merge base, `changed_files` the whole change set, and `model` the `--deep-model` value (default `--model`).
- **Merge-check.** `--mode merge-check` needs a recorded round, and exits 1 when the base update cannot be simulated. `diff_from` is the anchor's snapshot. `changed_files` holds the delta's files that the base did not change since the anchor's merge base or that are in the change set (merge base to snapshot), plus the conflicted files. `upstream_overlap` is the files the base changed since the anchor's merge base that are also in the change set.

## summary and covered

`summary` writes and prints `summary.md` (`path`, `text`) and `review-notes.md` (`review_notes_path`, `review_notes`). `review-notes.md` is the block to paste into a PR description: `## Review notes`, a generated line (`<N> rounds (<k> deep with <models>, <k> followup with <models>, <k> merge-check with <models>); second opinion: yes|no; merge risk <level> (round <n>, the latest deep round).`, leaving out modes with no rounds and joining a mode's distinct models with "and"), then only the non-empty sections among Escalated, Behavior changes to confirm, Deferred, Accepted, Invalid, Outside the change, Intent check and Not checked, then `### Found and fixed` (one `- <id> (<severity>) <title>` per verified finding), and a last line `Nothing to report under: <the empty sections>.` The intent check in both files takes unmet claims and unmentioned behavior from the latest deep round on; Not checked is the latest deep round's `not_checked`.

`covered` also prints `second_opinion_held` and `behavior_to_confirm` (a count). Neither changes `covered` or `reasons`.

## Arguments

`--state-dir` and `--repo` may come before or after the subcommand; when both positions give one, the value after the subcommand wins.

## State dir and cleanup

Every command refuses an empty, blank or relative `--state-dir` (`~` is expanded first), and one that resolves to the filesystem root or the home directory itself. `prune` and `cleanup` remove only `<repo_key>/<branch_key>` directories holding `review.json` or `rounds/`; any other directory, and any symlink (a symlinked `<repo_key>` directory included, which is never walked into), is left in place and listed under `skipped` in their output.

`prune` keeps a branch's state while `refs/heads/<branch>` or `refs/remotes/origin/<branch>` exists (an exact lookup, never a glob) and it was updated within `--days` (default 30). A `detached-<7 hex digits>` directory matches no branch, so it is removed only by age (a branch that merely starts with `detached-`, such as `detached-ui`, follows the normal rules): its `updated`, or the file's age when `review.json` is unreadable. A git failure while checking a branch is an error, never a reason to remove.

Both print `{"removed": [...], "skipped": [...], "errors": [...]}`. A directory that cannot be removed, or whose check fails during `prune` (an unreadable file, a failing git lookup), does not stop the run: it goes under `errors` as `{"dir", "error"}`, the rest are still processed, and the command exits 1 with a one-line message on stderr after printing the JSON. `errors` is empty on success. A failure before any directory is processed (for example listing the repo's branches) is an ordinary one-line error with no JSON.
