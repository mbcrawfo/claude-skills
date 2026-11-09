# Report schema

The reviewer ends its report with one JSON block. `review_state.py record-findings` validates it; `check-report` runs the same checks and saves nothing.

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

A finding may also carry `variants` (a list of `{"input", "result"}`, `result` being `fails` or `ok`: the members of an equivalence class the reviewer tried) and `fix_options` (two or more alternative fixes, as strings; `fix` then holds the recommendation). Both default to `[]`. An empty `fix_options` list is the same as none; one entry, or any blank entry, is refused.

**Coverage.** In a `deep` round begun by plugin 0.9.0 or later, `coverage` and `not_checked` are required. `coverage` must have an entry whose `item` is each checklist category (`logic`, `sibling-path`, `security`, `tests`, `intent`, `conventions`, `docs`, `build-ci`) and each required step (`break-tests`, `doc-grep`, `fulfilled-promises`, `real-system`, `related-records`, `broad-mappings`, `measurement-tests`, `consumer-build`), with a non-empty `done`; extra items are kept. `not_checked` entries need a non-empty `what` and `reason`. A deep report that leaves an item out is refused with `coverage is missing <items>`. Follow-up and merge-check reports, and rounds begun under 0.8 or earlier, may leave both out; when present they are validated and stored. The round stores both, as `[]` when absent.

Required top-level keys: `summary` (string), `merge_risk` (`level` from the enum, `reason` a non-empty string), `intent_check` (`unmet_claims` and `unmentioned_behavior`, both lists of strings), `verdicts`, `findings`, `outside_change` (all lists, may be empty).

Required finding keys: `severity`, `category`, `file`, `lines`, `title`, `trigger`, `consequence`, `reachable_now`, `fix`. Optional: `reachable_note`, `evidence`, `test`, `source` (default `""`) and `effort` (default `"quick"`). `lines` is a string.

**Errors.** `record-findings` checks each part of the block on its own: `summary`, `merge_risk`, `intent_check`, `coverage`, `not_checked`, and each verdict, finding and outside entry. A refused block gives one stderr line, `error: invalid reviewer output: ` and then the first problem of each failing part, joined with `; `.

**check-report.** `check-report --file PATH` runs the same checks as `record-findings` against the latest round and saves nothing. The reviewer runs it on its own report before it hands back. It refuses what `record-findings` refuses, with the same message, including a round that is already recorded and a missing round. On success it prints `ok`, `round`, `mode` and the counts of `findings`, `outside` and `verdicts`.

Verdicts are for earlier findings only: the entries of `OPEN_FINDINGS`, which a follow-up round always has and a deep round has when findings are carried forward. Give each entry one verdict and never re-report it as a new finding. `id` must exist in the ledger, name a finding whose status is `open`, `fixed` or `still-open`, and appear in at most one verdict; `status` is `verified` or `still-open`; `reason` is optional. Any violation rejects the whole block and leaves the ledger untouched.

`outside_change` entries require `file` and `title`; `lines` and `detail` default to `""`. `severity` and `category` may be given (same enums) and default to `minor` and `logic`.
