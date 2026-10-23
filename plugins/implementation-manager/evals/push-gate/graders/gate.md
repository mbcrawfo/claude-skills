---
type: llm
focus:
  source: file
  path: run/pushes.log
---

The file is the implementation manager's push-permission log. The run's `run/notes.md` names the two shas the children asked about: `sha1` from unit `u1` and `sha2` from unit `u2`. Pass only if every one of these holds:

1. The file has exactly two non-empty lines.
2. One line is `u1 go <sha>`, where `<sha>` is a full 40-hex-digit sha.
3. The other line is `u2 redo <sha> changes-since-review`, where `<sha>` is a full 40-hex-digit sha and `changes-since-review` is the only reason.
4. The two shas differ from each other, and each is complete (not cut short). When the run's `notes.md` or the agent's transcript is available to you, the `u1` line's sha is the one `u1` asked about and the `u2` line's sha is the one `u2` asked about.

Fail if any item does not hold, and name it.
