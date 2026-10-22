# Finding schema

Reference for the reviewer output block and the `review.json` state file. The reviewer agent produces the block; `review_state.py record-findings` validates it and writes the ledger.

## Reviewer output block

The reviewer ends its final message with one fenced `json` block. The block must be the last fenced ```json block in the message: `record-findings` takes the last one and ignores earlier ones, so example snippets earlier in the report are safe. `record-findings --file` also accepts a file that is only the JSON object.

```json
{
  "summary": "Two sentences on what the change does and how it looks.",
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

Required top-level keys: `summary` (string), `merge_risk` (`level` from the enum, `reason` string), `intent_check` (`unmet_claims` and `unmentioned_behavior`, both lists), `verdicts`, `findings`, `outside_change` (all lists, may be empty).

Required finding keys: `severity`, `category`, `file`, `lines`, `title`, `trigger`, `consequence`, `reachable_now`, `fix`. Optional: `reachable_note`, `evidence`, `test`, `source` (default `""`) and `effort` (default `"quick"`). `lines` is a string.

Verdicts are for earlier findings only, and only in follow-up rounds. `id` must exist in the ledger; `status` is `verified` or `still-open`; `reason` is optional.

`outside_change` entries require `file` and `title`; `lines` and `detail` default to `""`. `severity` and `category` may be given (same enums) and default to `minor` and `logic`.

## Ids

Finding ids are `R<round>-F<k>`. `k` counts from 1 within the round, in the order of the reviewer's `findings` list; `outside_change` entries are numbered after the in-change findings of the same round. Ids are never reused.

## review.json

Stored at `<state-dir>/<repo_key>/<branch_key>/review.json`. The raw reviewer report of a round, when kept (`record-findings --report`), is `rounds/<N>.md` beside it.

Top level:

| key | meaning |
| --- | --- |
| `repo_key`, `branch` | identity of the reviewed repo and branch |
| `base_ref` | ref the branch is reviewed against |
| `intent` | stated intent of the change, or null |
| `created`, `updated` | ISO 8601 UTC timestamps |
| `rounds` | list of round objects |
| `findings` | list of finding records |

Round object: `number`, `mode` (`deep` or `followup`), `model`, `started`, `snapshot`, `head`, `base_ref`, `diff_from`, `diff_to`, `changed_files`, `paths`, then filled by `record-findings`: `summary`, `merge_risk`, `intent_check`, `added`. Before recording, `summary` and `merge_risk` are null and `intent_check` and `added` are absent. `added` is the number of new in-change findings the round produced; outside-change entries are not counted.

Finding record:

| key | meaning |
| --- | --- |
| `id` | `R<round>-F<k>` |
| `status` | see below |
| `scope` | `change` (in the diff) or `outside` (from `outside_change`) |
| `round_opened` | round that reported it |
| `round_closed` | round that closed it, else null |
| `resolution` | null, or `{"status", "note", "commit", "by", "round"}` |
| `discussion` | list of `{"from", "text", "round"}`; `from` is `orchestrator`, `reviewer` or `developer` |
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

Transitions:

- `open` or `still-open` to `fixed`, `invalid` or `escalated`: `set-status`. `fixed` needs `--commit` or `--note`; `invalid` and `escalated` need `--note`.
- any status back to `open`: `set-status --status open`.
- `fixed` to `verified` or `still-open`: reviewer verdict. `verified` sets `round_closed`.
- `escalated` to `open` (decision `fix`) or `deferred` (decision `defer`): `resolve`. Only valid from `escalated`. `defer` sets `round_closed`.

`invalid` and `escalated` set `round_closed` in `set-status`; `fixed` and `open` leave it null.

`status` reports `continue: true` while any finding is `open`, `fixed` or `still-open` (`open-findings`, or `unverified-fixes` when only `fixed` remain) or when the last round added findings (`new-findings`). It reports `continue: false` with `converged` otherwise, and with `max-rounds` once there are six rounds.
