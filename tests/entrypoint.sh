#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TASK_TMP="$(mktemp -d)"
trap 'rm -rf "$TASK_TMP"' EXIT
mkdir -p "$TASK_TMP/bin" "$TASK_TMP/state"

cat > "$TASK_TMP/bin/codex" <<'CODEX'
#!/bin/sh
set -eu
printf 'argument:%s\n' "$@"
git config --get-all credential.https://github.com.helper || :
git config --get test.preserved || :
CODEX
chmod +x "$TASK_TMP/bin/codex"

run_entrypoint() {
  env -i PATH="$TASK_TMP/bin:$PATH" HOME="$TASK_TMP/state" \
    CODEX_HOME="$TASK_TMP/state/.codex" "$@" \
    sh "$ROOT/entrypoint.sh" 'a prompt with spaces'
}

output="$(run_entrypoint)"
[[ "$output" == 'argument:a prompt with spaces' ]]
output="$(run_entrypoint GH_TOKEN=)"
[[ "$output" == 'argument:a prompt with spaces' ]]
output="$(run_entrypoint GH_TOKEN=fake-test-token)"
[[ "$output" == *'!/usr/bin/gh auth git-credential'* ]]
[[ "$output" != *fake-test-token* ]]
[[ ! -e "$TASK_TMP/state/.gitconfig" ]]

# Existing process config is preserved, while earlier credential helpers reset.
output="$(run_entrypoint GH_TOKEN=fake-test-token GIT_CONFIG_COUNT=02 \
  GIT_CONFIG_KEY_0=test.preserved GIT_CONFIG_VALUE_0=existing-setting \
  GIT_CONFIG_KEY_1=credential.https://github.com.helper GIT_CONFIG_VALUE_1=old-helper)"
[[ "$output" == *existing-setting* ]]
[[ "$output" == *$'old-helper\n\n!/usr/bin/gh auth git-credential'* ]]
if run_entrypoint GH_TOKEN=fake-test-token GIT_CONFIG_COUNT=invalid >/dev/null 2>&1; then
  printf 'Expected invalid Git config count to be rejected\n' >&2
  exit 1
fi

printf 'Entrypoint credential configuration tests passed.\n'
