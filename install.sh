#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(tr -d '[:space:]' < "$SCRIPT_DIR/CODEX_VERSION")"
IMAGE="${CODEX_ISOLATED_IMAGE:-localhost/codex-one-folder}"
STATE_VOLUME="${CODEX_ISOLATED_STATE_VOLUME:-codex-state}"
INSTALL_DIR="${AGENTS_ISOLATED_INSTALL_DIR:-${CODEX_ISOLATED_INSTALL_DIR:-$HOME/.local/bin}}"
# Minimum supported Claude native CLI version.
CLAUDE_MIN_VERSION=2.1.314

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

AGENT=all
if [[ "${1:-}" == --agent && $# -ge 2 ]]; then
  AGENT="$2"
  shift 2
fi
[[ $# -eq 0 && "$AGENT" =~ ^(codex|claude|all)$ ]] \
  || fail "Usage: $0 [--agent codex|claude|all]"

command -v podman >/dev/null 2>&1 || fail "Podman is required"
command -v git >/dev/null 2>&1 || fail "Git is required"

[[ "$(podman info --format '{{.Host.Security.Rootless}}' 2>/dev/null || true)" == "true" ]] \
  || fail "Podman must be run rootless as your normal user; do not run this installer with sudo"

if command -v getenforce >/dev/null 2>&1; then
  selinux="$(getenforce)"
  if [[ "$selinux" != "Enforcing" ]]; then
    printf 'Warning: SELinux is %s. The setup still uses rootless Podman namespaces, but the tested Fedora configuration used SELinux Enforcing.\n' "$selinux" >&2
  fi
fi

install_codex() {
  local image_repo="$IMAGE"
  [[ "${IMAGE##*/}" != *:* ]] || image_repo="${IMAGE%:*}"
  [[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "invalid CODEX_VERSION"
  printf 'Building Codex %s image...\n' "$VERSION"
  podman build \
    --build-arg "CODEX_VERSION=$VERSION" \
    -t "$IMAGE" \
    -t "${image_repo}:${VERSION}" \
    -f "$SCRIPT_DIR/Containerfile" \
    "$SCRIPT_DIR"

  if ! podman volume exists "$STATE_VOLUME"; then
    printf 'Creating persistent state volume %s...\n' "$STATE_VOLUME"
    podman volume create "$STATE_VOLUME" >/dev/null
  else
    printf 'Keeping existing state volume %s.\n' "$STATE_VOLUME"
  fi

  printf 'Initializing Codex configuration if needed...\n'
  podman run --rm \
    --http-proxy=false \
    --cap-drop=all \
    --security-opt=no-new-privileges \
    --entrypoint sh \
    --mount "type=volume,src=${STATE_VOLUME},dst=/state" \
    "$IMAGE" \
    -c 'set -eu
        mkdir -p /state/.codex
        if [ ! -e /state/.codex/config.toml ]; then
          cat > /state/.codex/config.toml <<"CONFIG"
[tui]
alternate_screen = "never"
copy_on_select = "never"
CONFIG
        fi'

  mkdir -p "$INSTALL_DIR"
  install -m 0755 "$SCRIPT_DIR/bin/codex-isolated" "$INSTALL_DIR/codex-isolated"

  printf '\nInstalled %s/codex-isolated\n' "$INSTALL_DIR"
  if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    printf 'Note: %s is not currently on PATH. Add it to your shell PATH.\n' "$INSTALL_DIR" >&2
  fi

}

install_claude() {
  local claude_version claude_image claude_volume image_repo
  claude_version="$(tr -d '[:space:]' < "$SCRIPT_DIR/CLAUDE_VERSION")"
  [[ "$claude_version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail "invalid CLAUDE_VERSION"
  [[ "$(printf '%s\n' "$CLAUDE_MIN_VERSION" "$claude_version" | sort -V | head -n 1)" == "$CLAUDE_MIN_VERSION" ]] \
    || fail "CLAUDE_VERSION must be at least $CLAUDE_MIN_VERSION"
  claude_image="${CLAUDE_ISOLATED_IMAGE:-localhost/claude-one-folder}"
  image_repo="$claude_image"
  [[ "${claude_image##*/}" != *:* ]] || image_repo="${claude_image%:*}"
  claude_volume="${CLAUDE_ISOLATED_STATE_VOLUME:-claude-state}"
  [[ "$claude_volume" != "$STATE_VOLUME" ]] || fail "Claude and Codex must use different state volumes"
  printf 'Building Claude %s image...\n' "$claude_version"
  podman build --build-arg "CLAUDE_VERSION=$claude_version" \
    --build-arg "CLAUDE_MIN_VERSION=$CLAUDE_MIN_VERSION" \
    -t "$claude_image" -t "${image_repo}:${claude_version}" \
    -f "$SCRIPT_DIR/images/claude/Containerfile" "$SCRIPT_DIR"
  if ! podman volume exists "$claude_volume"; then
    podman volume create "$claude_volume" >/dev/null
  fi
  # Run the normal entrypoint so installation and first-run seeding agree.
  podman run --rm --http-proxy=false --cap-drop=all --security-opt=no-new-privileges \
    --mount "type=volume,src=${claude_volume},dst=/state/claude" \
    "$claude_image" --version
  mkdir -p "$INSTALL_DIR"
  install -m 0755 "$SCRIPT_DIR/bin/claude-isolated" "$INSTALL_DIR/claude-isolated"
  printf 'Installed %s/claude-isolated\n' "$INSTALL_DIR"
  if [[ ":$PATH:" != *":$INSTALL_DIR:"* ]]; then
    printf 'Note: %s is not currently on PATH. Add it to your shell PATH.\n' "$INSTALL_DIR" >&2
  fi
}

# Preserve independent maintenance: a selected agent never builds the other image.
if [[ "$AGENT" == codex || "$AGENT" == all ]]; then install_codex; fi
if [[ "$AGENT" == claude || "$AGENT" == all ]]; then install_claude; fi

printf '\nNext steps: run <agent>-isolated login, then <agent>-isolated verify.\n'
printf 'Change to a Git repository and launch codex-isolated or claude-isolated.\n'
