---
type: tool_used
tool: Bash
input_match: 'record-findings[^|;&]*--file\s+\\?"?\S*round-\d+\.md'
min: 2
---

The orchestrator recorded at least two rounds with `record-findings --file <tmp>/round-<n>.md`, the path the reviewer writes its report to, instead of passing findings inline. This stands in for checking that the round file is the reviewer's own report: the eval cannot read the reviewer's messages or compare the file with them, so a true verbatim check is not possible. The grader only shows that the recording went through the report file.
