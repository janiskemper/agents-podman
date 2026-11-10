#!/bin/sh
set -eu

mkdir -p "${CODEX_HOME:-/state/.codex}"

# Scope GitHub's credential helper to this process and its children. Nothing is
# written to /state or the repository, and Git config contains no token value.
if [ -n "${GH_TOKEN:-}" ]; then
  config_count="${GIT_CONFIG_COUNT:-0}"
  case "$config_count" in
    ''|*[!0-9]*) printf 'Error: invalid GIT_CONFIG_COUNT\n' >&2; exit 1 ;;
  esac
  # Normalize leading zeroes before shell arithmetic.
  while [ "${config_count#0}" != "$config_count" ] && [ "$config_count" != 0 ]; do
    config_count="${config_count#0}"
  done
  [ "$config_count" -le 1000 ] || { printf 'Error: GIT_CONFIG_COUNT is too large\n' >&2; exit 1; }
  export "GIT_CONFIG_KEY_${config_count}=credential.https://github.com.helper"
  export "GIT_CONFIG_VALUE_${config_count}="
  config_count=$((config_count + 1))
  export "GIT_CONFIG_KEY_${config_count}=credential.https://github.com.helper"
  export "GIT_CONFIG_VALUE_${config_count}=!/usr/bin/gh auth git-credential"
  export GIT_CONFIG_COUNT=$((config_count + 1))
fi
exec codex "$@"
