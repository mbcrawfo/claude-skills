#!/usr/bin/env bash
# Eval scaffold: runs in the case's empty workspace and builds the fixture repo
# right there, so the agent's working directory is the repo. No network, and no
# dependence on the user's git config.
#
# Everything is committed on `main`; `chore/cleanup` is checked out at the end,
# so every file is on the base. Old files are dated 2020; the search spec and
# plan are dated today, so "older than two weeks" must leave them alone.
#
# Planted cases, for the prompt "remove specs older than two weeks":
#   specs/2020-01-15-retry-design.md + plans/2020-01-16-retry.md
#       finished (src/client.py exists): both go in "Files to remove".
#   src/client.py comment pointing at the retry spec
#       the 5-requests-per-second reason for BACKOFF_SECONDS lives only in the
#       spec: an inline fix.
#   src/util.py comment pointing at the retry spec
#       clamp() is fully documented already: a remove fix.
#   specs/2020-01-20-export-design.md + plans/2020-01-21-export.md
#       the plan names src/export.py, which doesn't exist: unfinished, so
#       "Needs your confirmation", never "Files to remove".
#   research/2020-01-10-upstream-limits/
#       cited by the retry spec and by the kept search spec: kept.
#   specs/<today>-search-design.md
#       kept; it links to the retry spec and AGENTS.md states no policy on
#       stale links: the policy question, with that row tagged by it.
set -euo pipefail
unset CDPATH # a set CDPATH makes `cd` print and can send it elsewhere

# Isolate from the user's git configuration, signing and hooks.
for var in GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR GIT_OBJECT_DIRECTORY \
  GIT_ALTERNATE_OBJECT_DIRECTORIES GIT_NAMESPACE GIT_PREFIX GIT_CONFIG GIT_CONFIG_PARAMETERS GIT_CONFIG_COUNT; do
  unset "$var"
done
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME="Fixture Author" GIT_AUTHOR_EMAIL="fixture@example.invalid"
export GIT_COMMITTER_NAME="Fixture Author" GIT_COMMITTER_EMAIL="fixture@example.invalid"

for existing in .git AGENTS.md docs src; do
  if [ -e "$existing" ]; then
    echo "error: $existing already exists in $(pwd -P); run the scaffold in an empty directory" >&2
    exit 1
  fi
done

TODAY=$(date +%F)
OLD=2020-02-01T12:00:00

commit() {
  git add -A
  if [ -n "${2:-}" ]; then
    GIT_AUTHOR_DATE="$2" GIT_COMMITTER_DATE="$2" git commit -q -m "$1"
  else
    git commit -q -m "$1"
  fi
}

git init -q .
git symbolic-ref HEAD refs/heads/main
git config commit.gpgsign false
git config core.hooksPath .git/no-hooks

mkdir -p docs/superpowers/specs docs/superpowers/plans docs/superpowers/research/2020-01-10-upstream-limits src

cat >AGENTS.md <<'EOF'
# Agent instructions

Run tests with `python3 -m unittest`.
EOF

cat >docs/superpowers/research/2020-01-10-upstream-limits/report.md <<'EOF'
# Upstream limits

Measured against the upstream API in January: it drops bursts above 5 requests
per second without an error, so a client that retries at once makes it worse.
EOF

cat >docs/superpowers/specs/2020-01-15-retry-design.md <<'EOF'
# Retry design

## Goal

The HTTP client retries a failed request three times, waiting 2, 4 and then 8
seconds. The upstream API drops bursts above 5 requests per second (see
docs/superpowers/research/2020-01-10-upstream-limits/), so retries must back off
instead of firing at once.

## Design

`src/client.py` holds the schedule as `BACKOFF_SECONDS` and `fetch()` walks it.
EOF

cat >docs/superpowers/plans/2020-01-16-retry.md <<'EOF'
# Retry Implementation Plan

**Goal:** Add the retry schedule to the HTTP client.

**Spec:** `docs/superpowers/specs/2020-01-15-retry-design.md`

### Task 1: Backoff schedule

**Files:**
- Modify: `src/client.py`

- [ ] **Step 1:** Add `BACKOFF_SECONDS = (2, 4, 8)` and make `fetch()` retry with it.
EOF

cat >docs/superpowers/specs/2020-01-20-export-design.md <<'EOF'
# CSV export design

## Goal

Users can export their records as CSV. A new module `src/export.py` provides
`export_csv(records, path)`, writing one row per record with a header row.
EOF

cat >docs/superpowers/plans/2020-01-21-export.md <<'EOF'
# CSV Export Implementation Plan

**Goal:** Ship CSV export.

**Spec:** `docs/superpowers/specs/2020-01-20-export-design.md`

### Task 1: export_csv

**Files:**
- Create: `src/export.py`
- Test: `src/test_export.py`

- [ ] **Step 1:** Write `export_csv(records, path)` in `src/export.py`.
EOF

cat >src/client.py <<'EOF'
import time
import urllib.request

# Backoff schedule: see docs/superpowers/specs/2020-01-15-retry-design.md
BACKOFF_SECONDS = (2, 4, 8)


def fetch(url):
    for delay in (*BACKOFF_SECONDS, None):
        try:
            with urllib.request.urlopen(url) as response:
                return response.read()
        except OSError:
            if delay is None:
                raise
            time.sleep(delay)
EOF

cat >src/util.py <<'EOF'
# See 2020-01-15-retry-design.md
def clamp(value, low, high):
    """Return value limited to the range low..high, inclusive."""
    return max(low, min(value, high))
EOF

commit "Add retry, export design and utilities" "$OLD"

cat >"docs/superpowers/specs/$TODAY-search-design.md" <<'EOF'
# Search design

## Goal

Add a search endpoint client. It builds on the retry behavior in
docs/superpowers/specs/2020-01-15-retry-design.md, and its rate assumptions
come from docs/superpowers/research/2020-01-10-upstream-limits/.
EOF

cat >"docs/superpowers/plans/$TODAY-search.md" <<EOF
# Search Implementation Plan

**Goal:** Add the search client.

**Spec:** \`docs/superpowers/specs/$TODAY-search-design.md\`
EOF

commit "Add search design"

git checkout -q -b chore/cleanup
echo "fixture repo in $(pwd -P)"
