#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="${AGENTS_ISOLATED_INSTALL_DIR:-${CODEX_ISOLATED_INSTALL_DIR:-$HOME/.local/bin}}"
AGENT=all
PURGE_STATE=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --purge-state) PURGE_STATE=true; shift ;;
    --agent) [[ $# -ge 2 ]] || { printf 'Missing --agent value\n' >&2; exit 2; }; AGENT="$2"; shift 2 ;;
    *) printf 'Usage: %s [--agent codex|claude|all] [--purge-state]\n' "$0" >&2; exit 2 ;;
  esac
done
[[ "$AGENT" =~ ^(codex|claude|all)$ ]] || { printf 'Invalid agent\n' >&2; exit 2; }
command -v podman >/dev/null || { printf 'Podman is required\n' >&2; exit 1; }
[[ "$(podman info --format '{{.Host.Security.Rootless}}')" == true ]] \
  || { printf 'Podman must be rootless\n' >&2; exit 1; }

AGENTS=(codex claude)
[[ "$AGENT" == all ]] || AGENTS=("$AGENT")
CONTAINERS=()
declare -A IMAGES VOLUMES SEEN_CONTAINERS REMOVED_IMAGE_REPOS

# Discover and check every selected agent before removing anything. Keep the
# defaults discoverable when an override changes between install and uninstall.
for agent in "${AGENTS[@]}"; do
  prefix="${agent^^}_ISOLATED"
  image_var="${prefix}_IMAGE"
  volume_var="${prefix}_STATE_VOLUME"
  run_var="${prefix}_CONTAINER_NAME"
  login_var="${prefix}_LOGIN_CONTAINER_NAME"
  IMAGES[$agent]="${!image_var:-localhost/${agent}-one-folder}"
  VOLUMES[$agent]="${!volume_var:-${agent}-state}"
  discovered="$(podman ps -a --filter "label=io.agents-podman.agent=$agent" --format '{{.Names}}')"
  candidates=("${!run_var:-${agent}-isolated}" "${!login_var:-${agent}-isolated-login}"
              "${agent}-isolated" "${agent}-isolated-login")
  while IFS= read -r container; do
    [[ -z "$container" ]] || candidates+=("$container")
  done <<< "$discovered"
  for container in "${candidates[@]}"; do
    [[ -z "${SEEN_CONTAINERS[$container]:-}" ]] || continue
    SEEN_CONTAINERS[$container]=1
    if podman container exists "$container"; then
      running="$(podman inspect --format '{{.State.Running}}' "$container")"
      case "$running" in
        true) printf 'Stop the running container first: podman stop %s\n' "$container" >&2; exit 1 ;;
        false) ;;
        *) printf 'Cannot determine state of container %s\n' "$container" >&2; exit 1 ;;
      esac
      CONTAINERS+=("$container")
    fi
  done
done

# A failed image query must not turn a partial uninstall into apparent success.
IMAGE_REFS="$(podman image ls --format '{{.Repository}}:{{.Tag}}')"
for container in "${CONTAINERS[@]}"; do
  podman rm "$container" >/dev/null
done

remove_agent() {
  local agent="$1" image="$2" volume="$3" repo ref
  local refs=()
  rm -f "$INSTALL_DIR/${agent}-isolated"
  # Remove every tag of the image repository, including older installed versions.
  repo="$image"
  [[ "${image##*/}" != *:* ]] || repo="${image%:*}"
  if [[ -z "${REMOVED_IMAGE_REPOS[$repo]:-}" ]]; then
    while IFS= read -r ref; do
      if [[ "${ref%:*}" == "$repo" && "$ref" != *:'<none>' ]]; then refs+=("$ref"); fi
    done <<< "$IMAGE_REFS"
    [[ ${#refs[@]} -eq 0 ]] || podman rmi "${refs[@]}"
    REMOVED_IMAGE_REPOS[$repo]=1
  fi
  if $PURGE_STATE && podman volume exists "$volume"; then
    printf 'Warning: deleting %s, including authentication, settings, and history.\n' "$volume" >&2
    podman volume rm "$volume"
  else
    printf 'Preserved state volume %s. Use --purge-state to remove it too.\n' "$volume"
  fi
}

for agent in "${AGENTS[@]}"; do
  remove_agent "$agent" "${IMAGES[$agent]}" "${VOLUMES[$agent]}"
done
# Podman secrets are intentionally outside the lifecycle of this installer.
