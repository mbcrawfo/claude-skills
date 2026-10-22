#!/usr/bin/env bash
# Build the branch-review fixture repo for a manual review run.
# The fixture's source of truth is the review-loop eval's scaffold script; see
# its header for the planted defects and the rules for <dir>.
#
# Usage: make_fixture.sh [<dir>]
set -euo pipefail
exec bash "$(cd "$(dirname "$0")" && pwd -P)/../../evals/review-loop/make_fixture.sh" "$@"
