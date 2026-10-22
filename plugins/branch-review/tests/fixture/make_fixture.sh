#!/usr/bin/env bash
# Build the branch-review fixture repo: a tiny Python package on `main` and a
# branch `feature/discount` that adds a quantity discount with four planted
# defects for the reviewer to find:
#   (a) off-by-one: the tier uses `quantity > 10` but INTENT.md says "10 or more"
#   (b) the new code calls the untouched sibling `apply_percent_off`, which
#       computes money in float, against src/AGENTS.md ("money is Decimal")
#   (c) a test that only checks the function returned something
#   (d) INTENT.md says negative quantities are rejected; no code does that
# The test suite passes as-is: `python3 -m unittest discover -s src`.
#
# Usage: make_fixture.sh <dir>
# Re-running recreates <dir>. An existing <dir> is only removed when it is empty
# or was made by this script. Leaves `feature/discount` checked out and prints
# the absolute repo path.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "usage: $0 <dir>" >&2
  exit 2
fi

dir=$1
marker=.git/branch-review-fixture

if [ -e "$dir" ]; then
  if [ -f "$dir/$marker" ]; then
    rm -rf "$dir"
  elif [ -d "$dir" ] && [ -z "$(ls -A "$dir")" ]; then
    rmdir "$dir"
  else
    echo "error: $dir exists and is not a branch-review fixture; refusing to remove it" >&2
    exit 1
  fi
fi

mkdir -p "$dir"
cd "$dir"
repo=$(pwd -P)

# Fixed identity and dates so the repo builds the same everywhere, with no
# dependence on the user's git identity, signing setup or hooks.
export GIT_AUTHOR_NAME="Fixture Author" GIT_AUTHOR_EMAIL="fixture@example.invalid"
export GIT_COMMITTER_NAME="Fixture Author" GIT_COMMITTER_EMAIL="fixture@example.invalid"

git init -q
git symbolic-ref HEAD refs/heads/main
git config user.name "Fixture Author"
git config user.email "fixture@example.invalid"
git config commit.gpgsign false
git config tag.gpgsign false
git config core.hooksPath .git/no-hooks
touch "$marker"

commit() {
  local date=$1 message=$2
  git add -A
  GIT_AUTHOR_DATE="$date" GIT_COMMITTER_DATE="$date" git commit -q -m "$message"
}

# --- main -------------------------------------------------------------------

mkdir -p src/shop

cat >AGENTS.md <<'EOF'
# shop

A small pricing library.

## Rules

- Every public function has a docstring.
- Tests live next to the module as `test_<module>.py`.
- Run the tests with `python3 -m unittest discover -s src`.
EOF

cat >src/AGENTS.md <<'EOF'
# src

- Money is `Decimal`, never float. Every function that takes or returns an
  amount of money uses `Decimal` and rounds to cents with `ROUND_HALF_UP`.
EOF

cat >src/shop/__init__.py <<'EOF'
"""Pricing helpers for the shop."""
EOF

cat >src/shop/pricing.py <<'EOF'
"""Line totals and discounts."""

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def line_total(unit_price: Decimal, quantity: int) -> Decimal:
    """Return unit_price times quantity, rounded to cents."""
    return (unit_price * quantity).quantize(CENT, rounding=ROUND_HALF_UP)


def apply_percent_off(amount, percent):
    """Return amount reduced by percent, e.g. percent=15 takes 15% off."""
    return round(float(amount) * (1 - percent / 100), 2)
EOF

cat >src/shop/test_pricing.py <<'EOF'
import unittest
from decimal import Decimal

from shop.pricing import line_total


class LineTotalTest(unittest.TestCase):
    def test_multiplies_and_rounds_to_cents(self):
        self.assertEqual(line_total(Decimal("2.005"), 1), Decimal("2.01"))
        self.assertEqual(line_total(Decimal("1.25"), 4), Decimal("5.00"))


if __name__ == "__main__":
    unittest.main()
EOF

commit "2026-01-05T10:00:00Z" "Add line totals and percent discounts"

# --- feature/discount ---------------------------------------------------------

git checkout -q -b feature/discount

cat >INTENT.md <<'EOF'
# Quantity discount

Adds `discounted_total(unit_price, quantity)` to `shop.pricing`. Orders of 10
or more units get 10% off the line total; smaller orders pay the plain line
total. The result is a `Decimal` rounded to cents, like every other amount in
the package. `discounted_total` rejects negative quantities with `ValueError`.

Out of scope: more than one discount tier, and per-customer discounts.
EOF

cat >>src/shop/pricing.py <<'EOF'


BULK_QUANTITY = 10
BULK_PERCENT = 10


def discounted_total(unit_price: Decimal, quantity: int) -> Decimal:
    """Return the line total, with the bulk discount for 10 or more units."""
    total = line_total(unit_price, quantity)
    if quantity > BULK_QUANTITY:
        return apply_percent_off(total, BULK_PERCENT)
    return total
EOF

commit "2026-01-06T10:00:00Z" "Add bulk quantity discount"

cat >src/shop/test_pricing.py <<'EOF'
import unittest
from decimal import Decimal

from shop.pricing import discounted_total, line_total


class LineTotalTest(unittest.TestCase):
    def test_multiplies_and_rounds_to_cents(self):
        self.assertEqual(line_total(Decimal("2.005"), 1), Decimal("2.01"))
        self.assertEqual(line_total(Decimal("1.25"), 4), Decimal("5.00"))


class DiscountedTotalTest(unittest.TestCase):
    def test_small_order_pays_line_total(self):
        self.assertEqual(discounted_total(Decimal("2.50"), 3), Decimal("7.50"))

    def test_bulk_order_gets_discount(self):
        self.assertIsNotNone(discounted_total(Decimal("4.00"), 12))


if __name__ == "__main__":
    unittest.main()
EOF

commit "2026-01-06T11:00:00Z" "Test the bulk discount"

echo "$repo"
