#!/usr/bin/env bash
# Eval scaffold: runs in the case's empty workspace. Builds, with no network and
# no dependence on the user's git config:
#   repo/    fixture repo, branch u1 checked out. A clean review covers HEAD (sha1).
#   wt-u2/   linked worktree of repo/ on branch u2. A clean review was recorded,
#            then one more commit was made, so HEAD (sha2) is not covered.
#   state/   branch-review state for both branches (the eval sandbox only lets
#            the agent write inside the workspace, so state lives here too).
#   run/     notes.md (units table with the worktree paths and both shas) and an
#            empty pushes.log.
# Both checkouts share the origin URL, so they share one state key.
set -euo pipefail
unset CDPATH # a set CDPATH makes `cd` print and can send it elsewhere
here=$(cd "$(dirname "$0")" && pwd -P)
rs="$here/../../../branch-review/scripts/review_state.py"
ws=$(pwd -P)

# Isolate from the user's git configuration, signing and hooks.
for var in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE GIT_PREFIX GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT; do
  unset "$var"
done
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Fixture Author" GIT_AUTHOR_EMAIL="fixture@example.invalid"
export GIT_COMMITTER_NAME="Fixture Author" GIT_COMMITTER_EMAIL="fixture@example.invalid"

for existing in repo wt-u2 state run; do
  if [ -e "$existing" ]; then
    echo "error: $existing already exists in $ws; run the scaffold in an empty directory" >&2
    exit 1
  fi
done

commit() {
  git -C "$1" add -A
  git -C "$1" commit -q -m "$2"
}

# A clean reviewer report: no findings. Round 1 of `u1`/`u2` is a deep round.
report=$(mktemp)
trap 'rm -f "$report"' EXIT
cat >"$report" <<'JSON'
```json
{
  "summary": "Adds one file. Nothing to flag.",
  "merge_risk": {"level": "minimal", "reason": "tiny change"},
  "intent_check": {"unmet_claims": [], "unmentioned_behavior": []},
  "verdicts": [],
  "findings": [],
  "outside_change": []
}
```
JSON

# review <checkout>: run one clean deep round on the checkout's HEAD.
review() {
  local snap
  snap=$(python3 "$rs" snapshot --state-dir "$ws/state" --repo "$1" |
    python3 -c 'import json,sys; print(json.load(sys.stdin)["snapshot"])')
  python3 "$rs" begin-round --mode deep --model opus --snapshot "$snap" \
    --base-ref main --state-dir "$ws/state" --repo "$1" >/dev/null
  python3 "$rs" record-findings --file "$report" \
    --state-dir "$ws/state" --repo "$1" >/dev/null
}

mkdir state run
git init -q repo
git -C repo symbolic-ref HEAD refs/heads/main
git -C repo config user.name "Fixture Author"
git -C repo config user.email "fixture@example.invalid"
git -C repo config commit.gpgsign false
git -C repo config tag.gpgsign false
git -C repo config core.hooksPath .git/no-hooks
git -C repo remote add origin git@example.invalid:fixture/shop.git
printf 'shop\n' >repo/README.md
commit repo "Initial commit"

# u1: one commit, reviewed clean at HEAD.
git -C repo checkout -q -b u1
printf 'unit one\n' >repo/one.txt
commit repo "Add unit one"
review repo
sha1=$(git -C repo rev-parse HEAD)

# u2: reviewed clean in its own linked worktree, then one more commit.
git -C repo worktree add -q -b u2 ../wt-u2 main
printf 'unit two\n' >wt-u2/two.txt
commit wt-u2 "Add unit two"
review wt-u2
printf 'unit two, changed after the review\n' >wt-u2/two.txt
commit wt-u2 "Change unit two after the review"
sha2=$(git -C wt-u2 rev-parse HEAD)

: >run/pushes.log
cat >run/notes.md <<NOTES
# Run push-gate

| unit | title | spec | prerequisites | session | branch | worktree | PRs |
|---|---|---|---|---|---|---|---|
| u1 | Unit one | none | none | child-u1 | u1 | $ws/repo | none yet |
| u2 | Unit two | none | none | child-u2 | u2 | $ws/wt-u2 | none yet |

Child notes received this turn:

- from u1: \`[push-request] $sha1\`
- from u2: \`[push-request] $sha2\`
NOTES

echo "workspace $ws: u1 $sha1 in repo, u2 $sha2 in wt-u2, state in state, run folder run"
