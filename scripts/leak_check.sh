#!/usr/bin/env bash
# Before publishing: make sure no private words (company, product, table or person names) are in the repo.
# Usage: scripts/leak_check.sh /path/to/private/denylist.txt   (one word or phrase per line; keep it OUT of the repo)
set -euo pipefail
denylist="${1:?usage: scripts/leak_check.sh <denylist file>}"
cd "$(dirname "$0")/.."
found=0
while IFS= read -r word || [[ -n "$word" ]]; do
  [[ -z "$word" || "$word" == \#* ]] && continue
  if git ls-files --cached --others --exclude-standard -z | xargs -0 grep -I -n -i -F -- "$word" 2>/dev/null; then
    found=1
  fi
done < "$denylist"
if [[ $found -eq 1 ]]; then
  echo "✗ Private words found above. Remove them before publishing." >&2
  exit 1
fi
echo "✓ No private words found."
