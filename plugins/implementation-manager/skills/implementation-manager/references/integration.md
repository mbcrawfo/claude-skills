# Late work in integration mode

Every card, every unit the user adds, every ORPHAN and every owner-approved fix from an escalation targets `integration/<slug>`, before or after the final gate: its brief gets the unit prompt's integration-mode changes from `run.py brief`, its part 6 text follows the integration-mode bullet in children.md, its session starts with `source_branch: integration/<slug>`, and a PR opened against `<base>` is retargeted with `gh pr edit <pr> -R <repo> --base integration/<slug>`. The final PR waits until that work lands (`status` lists `final PR #<pr> waits on <units>`), an ORPHAN until you record it merged or closed.

## Red flags

| Thought | Reality |
|---|---|
| "I'll sync main into the integration branch myself" | The integration child does it. The manager writes no code. |
