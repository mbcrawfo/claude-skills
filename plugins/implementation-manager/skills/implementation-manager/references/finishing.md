# The `specs` unit, Finishing and the Tracker

## The `specs` unit

This session's own PR goes through the same queue. Before each push of its branch, run `/branch-review:review --base origin/<base>` on it to convergence (escalated findings are items of the `specs` unit, `--kind finding` with no `--ref`; later runs reuse the stored base). Then handle it as a `REQUEST` for `specs` with this session's own working directory: covered gets `run.py go <run> specs <sha>` or `run.py queue <run> specs <sha>`; once it has the `go` (at once, or after `NEXT specs`), sync with `<base>`, validate, and push, as a child does on `go`. Its merge bar is the same. The rows that message a child apply to this session's own work instead.

## Finishing

When every unit is merged: confirm with `gh` that each PR merged into the base (in integration mode, each unit PR into `integration/<slug>` and the final PR into the base, and then delete the integration branch: `gh api -X DELETE repos/<repo>/git/refs/heads/integration/<slug>`; a 422 `Reference does not exist` means GitHub already deleted it on merge), and check whatever the repository uses to track the work (an epic table, a changelog). Report the merged PRs, decisions recorded along the way, and the follow-ups left (tech debt, deferred items, a release, tracking docs that are out of date). Propose real follow-ups as cards. Offer once to archive the child sessions; archive only on a yes, each by its session id from `notes.md` with `archive_session` (a detached child is not archived with this session).

## Tracker

After setup, after every merge, and whenever something waits on the user, end the reply with this, built from `run.py status <run>`, `run.py item list <run>` and `notes.md`. Name units as `status` lists them, with the title in brackets when there is one (e.g. `column-overrides (8.7)`).

```
Run <run> (<owner/repo>)
- Integration: <branch> into <base> · final merge: <manager|owner> · feature review: <yes|no>
- Slot: <unit> since <HH:MM> | free
- Queue: <unit> · <unit> | empty
- <unit> merged #<pr> · <unit> merged #<pr>
- <unit> PR #<pr> open · <unit> building · <unit> PR #<pr> open, merges after <unit> · <unit> held until it asks again
- <unit> gated on <units>
- Waiting on you: <id> (<ref>) <summary> · <N> more | nothing
- Parked: <id> (<summary>), <note>, since <HH:MM> | none
- Elsewhere: <unit> own session (<topic>) · card for <unit> waiting for you to start it · final PR #<pr> ready for you to merge · don't merge final PR #<pr> until <unit> lands | none
```

The `Integration:` line appears only in integration mode. `Waiting on you:` and `Parked:` come from `status`'s `- items:` and `- parked:` lines; `Elsewhere:` from the `own session` questions, card notes and final PR notes in `notes.md`.
