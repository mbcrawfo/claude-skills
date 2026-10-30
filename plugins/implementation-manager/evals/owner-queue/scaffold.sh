#!/usr/bin/env bash
# Eval scaffold: runs in the case's empty workspace. Builds, with no network and
# no git repository, a run folder in run/ with `run.py`:
#   - units u1 and u2 (their worktree folders are empty folders u1/ and u2/), both
#     gates open;
#   - three owner items, filed in this order with `run.py item add`:
#       u1-1  u1 Q1 question "keep or drop the retry", blocks merge: yes (current)
#       u2-1  u2 Q1 question "rename the setting"
#       u1-2  u1 Q2 question "log level for retries", depends on Q1
#   - notes.md (the units table).
# The item bodies are files in bodies/, outside run/.
#
# graders/state.md names the item ids (u1-1, u2-1, u1-2) and their order: change
# the order or the units here and that grader changes too.
set -euo pipefail
unset CDPATH # a set CDPATH makes `cd` print and can send it elsewhere
here=$(cd "$(dirname "$0")" && pwd -P)
runpy="$here/../../scripts/run.py"
ws=$(pwd -P)

if [ -e run ]; then
  echo "error: run already exists in $ws; run the scaffold in an empty directory" >&2
  exit 1
fi

mkdir u1 u2 bodies

python3 "$runpy" init "$ws/run" --repo fixture/shop --base main --method merge
python3 "$runpy" unit "$ws/run" u1 --branch u1 --worktree "$ws/u1"
python3 "$runpy" unit "$ws/run" u2 --branch u2 --worktree "$ws/u2"
python3 "$runpy" open "$ws/run" u1
python3 "$runpy" open "$ws/run" u2

cat >bodies/u1-q1.md <<'BODY'
Escalated finding R1-F1: the retry in the order client resends a failed request three times with no backoff.
Keep the retry or drop it?
The child recommends keeping it. blocks merge: yes
BODY
cat >bodies/u2-q1.md <<'BODY'
The new setting is called `shop.timeout`. It sets the request timeout, so `shop.request_timeout` may read better. Rename it?
The child recommends renaming it. blocks merge: no
BODY
cat >bodies/u1-q2.md <<'BODY'
If the retry stays, at which log level should each retry be logged: debug, info or warning?
The child recommends warning. It builds on the answer to Q1.
BODY

python3 "$runpy" item add "$ws/run" u1 --ref Q1 --kind question --blocks yes \
  --summary "keep or drop the retry" --body-file "$ws/bodies/u1-q1.md"
python3 "$runpy" item add "$ws/run" u2 --ref Q1 --kind question --blocks no \
  --summary "rename the setting" --body-file "$ws/bodies/u2-q1.md"
python3 "$runpy" item add "$ws/run" u1 --ref Q2 --kind question --blocks no --depends Q1 \
  --summary "log level for retries" --body-file "$ws/bodies/u1-q2.md"

cat >run/notes.md <<NOTES
# Run owner-queue

| unit | title | spec | prerequisites | session | branch | worktree | PRs |
|---|---|---|---|---|---|---|---|
| u1 | Unit one | none | none | child-u1 | u1 | $ws/u1 | none yet |
| u2 | Unit two | none | none | child-u2 | u2 | $ws/u2 | none yet |
NOTES

echo "workspace $ws: run folder run, items u1-1 (current), u2-1, u1-2"
