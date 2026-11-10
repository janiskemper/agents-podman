#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

bash -n "$ROOT/install.sh"
bash -n "$ROOT/uninstall.sh"
sh -n "$ROOT/entrypoint.sh"
sh -n "$ROOT/images/claude/entrypoint.sh"

for agent in codex claude; do
  launcher="$ROOT/bin/${agent}-isolated"
  bash -n "$launcher"
  for property in --cap-drop=all --security-opt=no-new-privileges --http-proxy=false \
    bind-nonrecursive relabel=private; do
    grep -F -- "$property" "$launcher" >/dev/null
  done
done
grep -F -- '--dangerously-bypass-approvals-and-sandbox' "$ROOT/bin/codex-isolated" >/dev/null
if grep -F -- '--dangerously-skip-permissions' "$ROOT/bin/claude-isolated" >/dev/null; then
  printf 'Claude launcher must not bypass permissions\n' >&2
  exit 1
fi

printf 'Static checks passed.\n'
