# TD-0009: agents-md-cleanup's known limits: doc destinations, the `references` seed, test detection, over-limit re-dispatch, unrecorded over-limit files, scanner edge cases and the `_docs` cap

## Problem

- **The doc page a reviewer picks rests on heading titles and its own reading.** `docs` in [`agents_md_inventory.py`](../../plugins/maintenance/skills/agents-md-cleanup/scripts/agents_md_inventory.py) lists each page's headings, and the reviewer chooses a page and heading from those titles. Nothing checks that a chosen heading is the best fit, only that the target exists or is a new file under the docs folder.
- **`references` for the root AGENTS.md is a seed only.** Its path matches every mention of any AGENTS.md, so the hits are broad and not specific to the rule being moved. [SKILL.md](../../plugins/maintenance/skills/agents-md-cleanup/SKILL.md) step 4 relies on a grep for each moved rule's backticked names to find what really points at it.
- **The test's "can't run" detection is per recognised framework.** Step 7 recognises `dotnet test --filter`, a `pytest` path, a `jest` path and `go test -run`. A test command the skill doesn't recognise is treated as unrunnable, so the test is left uncommitted even if it would have passed.
- **A re-dispatched reviewer can only judge the file's own blocks.** When an over-limit target is re-dispatched on its merged text (step 4, *Size accounting*), verdicts attach to block ids of the file on disk. Incoming rules have no block ids, so the reviewer can only name them in its summary, and each becomes a question.
- **A file the user accepts over its byte limit is recorded only in the test's exception list.** With no test, nothing records the choice, and the next run asks again.
- **The word-count scanner has untested edge cases.** These cases have no test: a fence closed by a longer fence, a tilde fence closed by backticks, an unterminated fence or comment, and a document whose first line is `---` followed later by a thematic `---`, which loses the text between as front matter.
- **`_docs` parses every page before it applies the 200-page cap.** When both `Docs` and `docs` exist on a case-sensitive filesystem, it picks one without saying so.

## Why it exists

The destination choice is judgement work, and the reviewer sees the same titles a person would. A stricter check needs a measure of fit that nobody has defined. The `references` seed is cheap and errs toward more hits; narrowing it for the root file needs the moved rule's names, which only the skill knows. The framework list covers the common test styles, and an unrecognised command is safer treated as unrunnable than run blind. The re-dispatch reuses the existing verdict shape rather than adding a new one for text that isn't on disk. Recording an accepted file needs somewhere to put it, and the exception list was the only place that already existed. The scanner's common cases are tested and the rest were judged rare. The `_docs` cap bounds the output, not the work.

## Impact

Low. A reviewer can pick a weaker page or heading, and the user sees it in the approval message. The root file's reference list can be noisy. A test for an unusual framework is left for the user to run. Incoming rules to an over-limit file cost one question each. A user who accepts a file over its limit without a test is asked again on every run. A scanner edge case miscounts words or bytes for an unusual document. A very large docs folder is slow to inventory, and the `Docs` and `docs` choice can surprise on a case-sensitive filesystem.

## Proposed fix

- Show the reviewer each candidate page's first lines as well as its headings, or add a check that flags a chosen heading when another heading shares more words with the rule.
- For the root AGENTS.md, have the skill pass each moved rule's backticked names to `references` as extra patterns, so the seed is replaced by specific hits.
- Read the framework from the repo's own test command (a CI step or `package.json` script) and take its filter idiom from a table in the script, with a fallback that runs the whole file's tests.
- Let the re-dispatched reviewer return verdicts for incoming rules under ids the skill assigns to them (for example `in-<n>`), and have `check-verdicts` accept them.
- Record an accepted over-limit file in the guidelines section, for example as a `Size exceptions:` line, so it holds without a test.
- Add scanner tests in `plugins/maintenance/tests/` for each edge case listed above, and fix whatever they show.
- In `_docs`, stop at 200 pages before parsing, and report both folders when `Docs` and `docs` exist.

## Trigger

A user report of a poorly chosen doc page, a noisy root reference list, a test left uncommitted for a framework the skill doesn't know, a repeated over-limit question, or a miscounted document. Also the next change to `docs`, `references`, the word-count scanner or step 7.
