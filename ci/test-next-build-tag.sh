#!/usr/bin/env bash
# Exercises ci/next-build-tag.sh against fixed tag lists. No registry and no
# network: the script reads tags from stdin precisely so this can run anywhere.
set -euo pipefail
cd "$(dirname "$0")/.."

fail=0

check() {
  local name="$1" want="$2" week="$3"; shift 3
  local got
  got="$(printf '%s\n' "$@" | WEEK="$week" ci/next-build-tag.sh)"
  if [[ "$got" == "$want" ]]; then
    echo "ok   $name"
  else
    echo "FAIL $name: want ${want}, got ${got}"
    fail=1
  fi
}

check "first build of the week"   26W38B1  26W38
check "increments the highest"    26W38B3  26W38  26W38B1 26W38B2
check "numeric, not lexical"      26W38B11 26W38  26W38B9 26W38B10
check "ignores other weeks"       26W38B1  26W38  26W37B4 26W39B2
check "ignores sha tags"          26W38B2  26W38  26W38B1 \
                                                  863c56aaa7e25f3a2e42d0b528226573a399e1f4
check "gaps are not reused"       26W38B6  26W38  26W38B5 26W38B2
check "prefix is not a substring" 26W38B1  26W38  126W38B7 26W380B2

exit "$fail"
