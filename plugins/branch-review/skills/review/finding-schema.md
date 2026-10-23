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
  "outside_change": [{"file": "path", "lines": "10-12", "title": "...", "detail": "..."}]
}
```

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
| `created`, `updated` | ISO 8601 UTC timestamps |
| `rounds` | list of round objects |
| `findings` | list of finding records |

Round object: `number`, `mode` (`deep` or `followup`), `model`, `started`, `snapshot`, `head` (the commit the snapshot was taken on: its parent for a dirty snapshot, else the snapshot itself), `base_ref`, `merge_base` (of `head` and `base_ref`; `begin-round` exits 1 with `base ref not found: <ref>` when the base ref does not resolve), `diff_from`, `diff_to`, `changed_files`, `paths` (normalized: no leading `./`, a directory as `dir/**`; `.`, `./` or `/` means the whole repo and gives an empty list), then filled by `record-findings`: `summary`, `merge_risk`, `intent_check`, `added`. Before recording, `summary` and `merge_risk` are null and `intent_check` and `added` are absent. `added` is the number of new in-change findings the round produced; outside-change entries are not counted.

A round with `added` is recorded. A round without it was begun but its reviewer never reported, so it reviewed nothing: it never anchors a follow-up, `begin-round` drops it before adding the next round, and `status` and `summary` exit 1 while it is the last round. `begin-round` never saves an empty round (no changed files): it returns `saved: false`, and creates no state when none existed.

Finding record:

| key | meaning |
| --- | --- |
| `id` | `R<round>-F<k>` |
| `status` | see below |
| `scope` | `change` (in the diff) or `outside` (from `outside_change`) |
| `round_opened` | round that reported it |
| `round_closed` | round that closed it, else null |
| `resolution` | null, or `{"status", "note", "commit", "by", "round"}` |
| `discussion` | list of `{"from", "text", "round"}`; `from` is `orchestrator` or `reviewer` |
| reviewer fields | `severity`, `category`, `file`, `lines`, `title`, `trigger`, `consequence`, `reachable_now`, `reachable_note`, `evidence`, `fix`, `test`, `source`, `effort` |

Outside-change records carry `file`, `lines`, `title`, `detail`, `severity` and `category` instead of the trigger/consequence fields, with status `deferred`, `round_closed` null and `resolution.note` `"outside the change set"`. A verdict's `reason` is appended to the finding's `discussion` as a `reviewer` entry.

## Statuses and transitions

| status | meaning |
| --- | --- |
| `open` | reported, not yet addressed |
| `fixed` | orchestrator claims a fix; awaiting reviewer confirmation |
| `still-open` | reviewer says a claimed fix did not resolve it |
| `verified` | reviewer confirmed the fix (terminal) |
| `invalid` | orchestrator judged it not a real problem (terminal) |
| `escalated` | needs a developer decision |
| `deferred` | developer chose not to fix, or it is outside the change (terminal) |

Transitions are the ones the review skill follows:

- `open` or `still-open` to `fixed`, `invalid` or `escalated`: `set-status`. `fixed` needs `--commit` or a note; `invalid` and `escalated` need a note (`--note` or `--note-file`).
- any status back to `open`: `set-status --status open`.
- `fixed` (or a carried `open` or `still-open`) to `verified` or `still-open`: reviewer verdict. `verified` sets `round_closed`; `still-open` clears it.
- `escalated` to `open` (decision `fix`) or `deferred` (decision `defer`): `resolve`. `defer` sets `round_closed`.

The script enforces only part of this: `resolve` works only on an `escalated` finding, a verdict is accepted only for a finding that is `open`, `fixed` or `still-open`, and `set-status` takes only `fixed`, `invalid`, `escalated` or `open` as the target, with the notes above. It does not check the status a `set-status` moves from; following the transitions is the skill's job.

`invalid` and `escalated` set `round_closed` in `set-status`; `fixed` and `open` leave it null.

Text arguments have file forms: `set-status --note-file PATH`, `discuss --text-file PATH` and `resolve --note-file PATH` read the text from a UTF-8 file and strip surrounding whitespace. Each is mutually exclusive with its inline form (`--note`, `--text`). `discuss` and `resolve` reject an empty file; for `set-status` an empty file is no note.

`status` reports `continue: true` while any finding is `open`, `fixed` or `still-open` (`open-findings`, or `unverified-fixes` when only `fixed` remain) or when the last round added findings (`new-findings`). It reports `continue: false` with `converged` otherwise. When it would continue but there are six or more rounds numbered above `--since-round N` (default 0, so every round counts), it reports `continue: false` with `max-rounds` instead.

## Mode selection

`locate` picks the next round's mode from the last recorded round (the anchor) and returns it with a reason:

| reason | mode | when |
| --- | --- | --- |
| `no-state` | deep | no `review.json` for the branch |
| `no-rounds` | deep | no recorded round |
| `snapshot-missing` | deep | the anchor's snapshot commit is gone |
| `history-rewritten` | deep | HEAD does not descend from the anchor's HEAD |
| `base-moved` | deep | the merge base of HEAD and the base ref differs from the anchor's `merge_base`, as after merging the base into the branch |
| `paths-changed` | deep | the normalized `--paths` given to `locate` differ from the anchor's `paths` |
| `continuing` | followup | none of the above |

`open_findings` is returned in every case, so a deep round after an earlier one carries them forward. `locate` and `begin-round` use `--base` / `--base-ref` when given, else the stored `base_ref`, else `origin/HEAD`'s target, else `main`.

## State dir and cleanup

Every command refuses an empty, blank or relative `--state-dir` (`~` is expanded first), and one that resolves to the filesystem root or the home directory itself. `prune` and `cleanup` remove only `<repo_key>/<branch_key>` directories holding `review.json` or `rounds/`; any other directory, and any symlink, is left in place and listed under `skipped` in their output.

`prune` keeps a branch's state while `refs/heads/<branch>` or `refs/remotes/origin/<branch>` exists (an exact lookup, never a glob) and it was updated within `--days` (default 30). A `detached-<sha>` directory matches no branch, so it is removed only by age: its `updated`, or the file's age when `review.json` is unreadable. A git failure while checking branches is an error, never a reason to remove.

Both print `{"removed": [...], "skipped": [...], "errors": [...]}`. A directory that cannot be removed does not stop the run: it goes under `errors` as `{"dir", "error"}`, the rest are still processed, and the command exits 1 with a one-line message on stderr after printing the JSON. `errors` is empty on success.
