You are the judge of a replay. A code reviewer re-reviewed a change set that was reviewed before. You compare what it reported now with what is already known about that change set.

The repo is your working directory, checked out at the reviewed commit. Read files here. Do not edit anything.

KNOWN FINDINGS (from the earlier review rounds; `id` identifies each):

{{KNOWN}}

REPLAY FINDINGS (from the reviewer you are judging; `replay` is the index):

{{REPLAY}}

For each replay finding, give it exactly one label:

- `matches`: it describes the same defect as a known finding. Compare the defect, not the wording or the line numbers. Put the known `id` in `of`.
- `duplicate`: it describes the same defect as an earlier replay finding. Put that finding's index in `of`. Label a replay finding `duplicate` before you compare it with the known list.
- `new`: it matches no known finding and no earlier replay finding. Check it before you decide `holds`: read the lines it names, and run its trigger when that is cheap (a short command or a small script). Set `holds` to `true` when the defect is real in this checkout, `false` when it is not, and `null` when you could not tell. In `check`, say what you ran or read.

For `matches` and `duplicate`, set `holds` to `null` and `check` to a short note on what you compared.

End your answer with one fenced `json` block holding the list, and write no other `json` block anywhere. Each item has this shape:

```json
[{"replay": 0, "label": "matches", "of": "R1-F1", "holds": null, "check": "same missing guard in parse()"}]
```

Give one item for every replay finding.
