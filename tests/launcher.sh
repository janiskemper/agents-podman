#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/bin" "$TMP/home" "$TMP/repo/subdir"

git -C "$TMP/repo" init -q
git -C "$TMP/repo" config user.email test@example.invalid
git -C "$TMP/repo" config user.name Test
touch "$TMP/repo/file"
git -C "$TMP/repo" add file
git -C "$TMP/repo" commit -qm init

cat > "$TMP/bin/podman" <<'PODMAN'
#!/usr/bin/env bash
set -euo pipefail
log="${FAKE_PODMAN_LOG:?}"
printf '%q ' "$@" >> "$log"
printf '\n' >> "$log"

case "${1:-} ${2:-}" in
  "info --format")
    printf 'true\n'
    ;;
  "image exists"|"volume exists")
    exit 0
    ;;
  "container exists")
    exit 1
    ;;
  "secret exists")
    [[ "${3:-}" != missing-secret ]]
    ;;
  "run --rm")
    # Match auth-status/version helpers well enough for launcher tests.
    if [[ " $* " == *" login status "* ]]; then
      printf 'Logged in\n'
    elif [[ " $* " == *" --version "* ]]; then
      printf 'codex-cli test\n'
    fi
    ;;
  *)
    ;;
esac
PODMAN
chmod +x "$TMP/bin/podman"

export PATH="$TMP/bin:$PATH"
export HOME="$TMP/home"
export FAKE_PODMAN_LOG="$TMP/podman.log"
: > "$FAKE_PODMAN_LOG"

(
  cd "$TMP/repo/subdir"
  "$ROOT/bin/codex-isolated" "Review this repo"
)

run_line="$(tail -n 1 "$FAKE_PODMAN_LOG")"
[[ "$run_line" == *"--cap-drop=all"* ]]
[[ "$run_line" == *"--security-opt=no-new-privileges"* ]]
[[ "$run_line" == *"src=${TMP}/repo"* ]]
[[ "$run_line" == *"dst=/workspaces/repo-"* ]]
[[ "$run_line" == *"relabel=private"* ]]
[[ "$run_line" == *"bind-nonrecursive"* ]]
[[ "$run_line" != *"--network=host"* ]]
[[ "$run_line" == *"--dangerously-bypass-approvals-and-sandbox"* ]]
[[ "$run_line" == *"Review\\ this\\ repo"* ]]

: > "$FAKE_PODMAN_LOG"
"$ROOT/bin/codex-isolated" login
login_line="$(tail -n 1 "$FAKE_PODMAN_LOG")"
[[ "$login_line" == *"--network=host"* ]]
[[ "$login_line" != *"dst=/workspaces/"* ]]
[[ "$login_line" == *"dst=/state"* ]]

if (
  cd "$TMP"
  "$ROOT/bin/codex-isolated" >/dev/null 2>&1
); then
  echo "expected non-Git directory to be rejected" >&2
  exit 1
fi

git -C "$TMP/home" init -q
if (
  cd "$TMP/home"
  "$ROOT/bin/codex-isolated" >/dev/null 2>&1
); then
  echo "expected HOME as Git root to be rejected" >&2
  exit 1
fi

printf 'Launcher tests passed.\n'

# Selected values never appear in Podman's command line, only names/mappings.
# shellcheck disable=SC2016 # Verify literal shell syntax survives forwarding.
export ISOLATED_TEST_SETTING='a value with spaces and $shell syntax'
export ISOLATED_TEST_OTHER='another value'
: > "$FAKE_PODMAN_LOG"
(
  cd "$TMP/repo/subdir"
  "$ROOT/bin/codex-isolated" --env ISOLATED_TEST_SETTING --env ISOLATED_TEST_OTHER \
    --secret-env GH_TOKEN=test-token --secret-env OTHER_TOKEN=other-token run \
    --model test-model 'Review this repo'
)
run_line="$(tail -n 1 "$FAKE_PODMAN_LOG")"
[[ "$run_line" == *'--env ISOLATED_TEST_SETTING'* ]]
[[ "$run_line" == *'--env ISOLATED_TEST_OTHER'* ]]
[[ "$run_line" == *'test-token\,type=env\,target=GH_TOKEN'* ]]
[[ "$run_line" == *'other-token\,type=env\,target=OTHER_TOKEN'* ]]
[[ "$run_line" == *'--model test-model'* ]]
[[ "$run_line" != *"$ISOLATED_TEST_SETTING"* ]]
[[ "$run_line" != *"$ISOLATED_TEST_OTHER"* ]]
[[ "$run_line" == *'--cap-drop=all'* && "$run_line" == *'bind-nonrecursive'* ]]

expect_rejected() {
  : > "$FAKE_PODMAN_LOG"
  if (cd "$TMP/repo"; "$ROOT/bin/codex-isolated" "$@") >"$TMP/error" 2>&1; then
    printf 'Expected launcher rejection\n' >&2
    exit 1
  fi
  [[ "$(cat "$FAKE_PODMAN_LOG")" != *'run --rm'* ]]
}

unset ISOLATED_TEST_UNSET
expect_rejected --env
expect_rejected --env ISOLATED_TEST_UNSET
expect_rejected --env 'BAD=literal-secret'
expect_rejected --secret-env
expect_rejected --secret-env GH_TOKEN
expect_rejected --secret-env GH_TOKEN=
expect_rejected --secret-env 'GH_TOKEN=test-token,type=mount'
expect_rejected --secret-env '=test-token'
expect_rejected --secret-env GH_TOKEN=missing-secret
expect_rejected --env ISOLATED_TEST_SETTING --secret-env ISOLATED_TEST_SETTING=test-token
for helper in login auth-status logout version verify; do
  expect_rejected --secret-env GH_TOKEN=test-token "$helper"
  expect_rejected --env ISOLATED_TEST_SETTING "$helper"
  : > "$FAKE_PODMAN_LOG"
  "$ROOT/bin/codex-isolated" "$helper" > /dev/null
  [[ "$(cat "$FAKE_PODMAN_LOG")" != *'--secret'* ]]
  [[ "$(cat "$FAKE_PODMAN_LOG")" != *'--env'* ]]
done

# Explicit run / -- ends launcher parsing and forwards the remaining arguments.
for delimiter in run --; do
  : > "$FAKE_PODMAN_LOG"
  (cd "$TMP/repo"; "$ROOT/bin/codex-isolated" "$delimiter" --env CODEX_ARGUMENT) >/dev/null
  run_line="$(tail -n 1 "$FAKE_PODMAN_LOG")"
  [[ "$run_line" == *'--dangerously-bypass-approvals-and-sandbox --env CODEX_ARGUMENT'* ]]
done

printf 'Environment and secret launcher tests passed.\n'
