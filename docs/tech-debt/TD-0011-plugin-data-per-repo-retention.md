# TD-0011: Plugin data is pruned per repo, and runs by age alone

## Problem

Claude Code removes a plugin's data directory (`~/.claude/plugins/data/<id>/`) only when the plugin is uninstalled from its last scope or its marketplace is removed. Updates keep it, and `cleanupPeriodDays` does not cover it. So each plugin prunes its own state, by the `retention_days` option (default 30):

- [`branch-review`](../../plugins/branch-review/ARCHITECTURE.md) prunes at the start of each review, but only the reviewed repo's `<owner__repo>/` folder.
- [`implementation-manager`](../../plugins/implementation-manager/ARCHITECTURE.md) prunes when a run starts, but only that repo's `<owner>-<repo>/` folder.

State for a repo where neither plugin runs again is never removed until the plugin is uninstalled.

`implementation-manager` also judges a run by age alone. A run records neither its manager's session nor that it ended, and no hook reports a session being archived or deleted (`SessionEnd` fires on any process exit, and the session can still be resumed). So it cannot tell a quiet live run from one whose manager is gone; a live run untouched for the whole retention period would be removed under its manager.

## Why it exists

The owner chose per-repo pruning at each plugin's entry point and age alone for runs (design: [`2026-11-05-plugin-data-retention-design.md`](../superpowers/specs/2026-11-05-plugin-data-retention-design.md)). A live run a month without activity is not expected in practice.

## Impact

Low. The data is small (about 13 MB across both plugins after a month of heavy use), and nothing breaks while it stays.

## Proposed fix

At the same entry points, sweep every repo folder under the plugin's data by age, not only the current one (approach 3 in the design). For runs, optionally record the manager's session id at `run.py init` and, before removing a run, have the manager check it with the desktop app's `get_session`, keeping runs whose manager is still there and not archived.

## Trigger

Plugin data growing large, or a live run lost to pruning.
