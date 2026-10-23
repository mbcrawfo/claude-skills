#!/usr/bin/env bash
# Eval scaffold: runs in the case's empty workspace. Builds the fixture repo in
# repo/ and an empty state/ next to it, because the eval sandbox only lets the
# agent write inside the workspace and the review keeps its state outside the
# repo being reviewed.
set -euo pipefail
unset CDPATH
here=$(cd "$(dirname "$0")" && pwd -P)
bash "$here/make_fixture.sh" repo >/dev/null
mkdir -p state
echo "fixture in $(pwd -P)/repo, state dir $(pwd -P)/state"
