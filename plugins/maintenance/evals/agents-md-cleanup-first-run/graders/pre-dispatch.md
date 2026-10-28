---
type: llm
focus: last_message
---

The message is the AGENTS.md cleanup skill's pre-dispatch message on a first run, where the repo has no `AGENTS.md guidelines` section. The repo has 2 AGENTS.md files holding 7 blocks, and the skill needs 2 agents. Pass only if every one of these holds, and name each item that fails:

1. It states the size of the run: 2 files, 7 blocks and 2 agents. Counts that differ from these fail.
2. It asks the user to confirm how many agents run at once, and proposes 4 as the recommended limit.
3. It asks three policy questions, each with a recommended answer:
   - whether the skill may create new nested AGENTS.md files: `allowed` is recommended, `ask each time` is the alternative;
   - where a file-specific quirk goes: `comment in that file` is recommended, `nearest AGENTS.md` is the alternative;
   - how far to push guidance down: `aggressive` is recommended, `conservative` is the alternative.
4. It shows no approval table and proposes no per-statement verdicts (no list of statements to delete, trim, fix or move), because no agent has reviewed anything yet.
5. It reports nothing as already edited, deleted, moved or committed.
6. It ends by waiting for the user's answers, with no further action taken.
