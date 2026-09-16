#!/usr/bin/env bash
# Prints the next build tag for the current ISO week, e.g. 26W38B3.
#
# Existing tags arrive on stdin, one per line, so this script needs no registry
# and no network. Whoever calls it decides where the list came from; this only
# decides the number. That is what makes it testable.
#
#   printf '%s\n' 26W38B1 26W38B2 | ci/next-build-tag.sh   ->   26W38B3
#
# WEEK can be set to override the current week, which the tests rely on.
set -euo pipefail

# %g is the two-digit ISO week-numbering year, NOT the calendar year. The two
# diverge around New Year: 2027-01-01 falls in ISO week 53 of 2026, so %Y%V
# would print 202753 while %g%V correctly prints 2653. UTC because CI runs in
# UTC and your laptop does not.
WEEK="${WEEK:-$(date -u +%g)W$(date -u +%V)}"

highest=0
while IFS= read -r tag; do
  [[ "$tag" =~ ^"${WEEK}"B([0-9]+)$ ]] || continue
  n="${BASH_REMATCH[1]}"
  # Arithmetic comparison. A lexical one would rank B9 above B10.
  (( n > highest )) && highest="$n"
done

# Always highest+1, never filling gaps. A reused ordinal would point at two
# different images over time, which is the one thing a tag must never do.
echo "${WEEK}B$(( highest + 1 ))"
