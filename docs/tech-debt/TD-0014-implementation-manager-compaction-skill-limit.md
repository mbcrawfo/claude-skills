# TD-0014: Compaction relies on the skill re-injection limit

## Problem

After a compaction, Claude Code re-injects only the first 5,000 tokens of an invoked skill (documented on 2026-11-09). The manager skill is split into a short core and `references/*.md` because of that limit. The core is about 12.3k characters, and `tests/test_skill_text.py` fails above 17,500 characters, the proxy for the limit. If the limit becomes smaller, the core no longer fits, and a compacted manager loses rules it needs to resume.

## Why it exists

The limit is a Claude Code behavior that the plugin does not control. The 17,500 characters are a rough proxy for tokens.

## Impact

Low. The core-size test and the `compaction-resume` eval check only the current limit, so they would not show a change in it.

## Proposed fix

Re-check the documented limit at each Claude Code release that mentions skills or compaction. If it shrinks, cut the core and move more text into references.

## Trigger

A change to the documented skill re-injection limit, or a manager that forgets a core rule after a compaction.
