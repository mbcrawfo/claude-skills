# TD-0003: A unit gated on a stopped prerequisite keeps the integration final gate shut

## Problem

In integration-branch mode the final gate opens, and the final PR may be reported ready, only once every unit other than the integration unit is done or stopped (`late_units` and `final_due` in [`runstate.py`](../../plugins/implementation-manager/scripts/runstate.py)). A unit whose prerequisite (`--prereq`) was stopped never opens: `openable` waits for every prerequisite to be done, and a stopped one never is. That unit is neither done nor stopped, so it stays in `late_units` for good. The final gate never opens, `status` lists `final PR #<pr> waits on <unit>` once a final PR exists (and nothing at all before), and nothing says the wait can never end or why.

## Why it exists

The gating is older than integration-branch mode: in a default run the same unit also stays gated forever, and `status` lists it as `<unit> on <prereq>`, which costs nothing else. Integration-branch mode added the consequence: one such unit now holds back the whole run's final PR. It was found in the final review of that change and left as it is, since the manager can stop the dependent unit by hand and the fix touches how prerequisites and stopped units interact in every run.

## Impact

Low. It needs a stopped unit with dependents in an integration-branch run. The run then stalls quietly at its end: no final gate, no final PR ready, and only the `gated` line (`<unit> on <prereq>`) hints at the cause. Stopping the dependent unit (`run.py stop <unit> <reason>`) clears it. Nothing merges early.

## Proposed fix

Either treat a unit as stopped when any of its prerequisites is stopped (in `late_units`, `final_due` and `openable`, with `status` showing it as `<unit> stopped: needs <prereq>`), or keep the gating and have `status` list each such unit under "needs the manager" (`<unit> waits on stopped <prereq>: stop it or drop the prerequisite`), so the manager asks the owner. Add tests in `plugins/implementation-manager/tests/test_runstate.py` and `test_run_cli.py` with a stopped prerequisite and a dependent unit in an integration run.

## Trigger

An integration-branch run whose final gate never opened because of a stopped prerequisite, or the next change to how stopped units or prerequisites are handled.
