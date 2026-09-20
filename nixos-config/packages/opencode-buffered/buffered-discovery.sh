#!/usr/bin/env bash
set -euo pipefail

binary=${1:?expected the real OpenCode executable}
shift

if [[ ${1-} != models ]] && [[ ${1-} != agent || ${2-} != list ]]; then
  exec "$binary" "$@"
fi

# OpenCode can exit before pending pipe writes drain. A regular file avoids this.
output=$(mktemp)
trap 'rm -f -- "$output"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

status=0
"$binary" "$@" > "$output" || status=$?
copy_status=0
cat -- "$output" || copy_status=$?
if (( status != 0 )); then
  exit "$status"
fi
exit "$copy_status"
