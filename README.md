# Codex and Claude Code in rootless Podman containers

This repository provides independent wrappers for OpenAI Codex CLI and Claude Code with **only one Git repository bind-mounted from the host**.

It is designed for Linux workstations where either agent should be able to edit one repository and use the Internet, but should not see the rest of the user's home directory, SSH keys, kubeconfig, other repositories, or host container sockets.

## Security model

Normal run mode exposes only:

| Agent | Repository mount (read/write) | Persistent named volume |
| --- | --- | --- |
| Codex | `/workspaces/<name>-<id>` | `codex-state` at `/state` |
| Claude | `/workspaces/<name>-<id>` | `claude-state` at `/state/claude` |

Each agent receives its own state volume. Host environment variables and Podman
secrets are supplied only when explicitly selected.

The launcher additionally uses:

- rootless Podman (required),
- `--cap-drop=all`,
- `--security-opt=no-new-privileges`,
- Podman's default seccomp profile,
- private SELinux relabeling for the repository on SELinux hosts, and
- a non-recursive bind mount so separate filesystems mounted below the repository are not imported into the container.

Codex itself is started with `--dangerously-bypass-approvals-and-sandbox`. This is intentional: the **Podman container is the outer sandbox**. Do not use the same Codex flag directly on the host.

Each container can read its operating system, selected workspace, and its own state volume. It has unrestricted network access. Anything in those mounts, including the agent's authentication material, is inside its trust boundary.

This is container isolation, not VM isolation. A kernel/container-runtime escape remains a residual risk.

## Requirements

- Linux
- Podman, run rootless as the normal user
- Git
- Bash 4.4 or newer
- `realpath`, `env`, and `sha256sum` (normally provided by coreutils)
- `flock` (normally provided by util-linux)
- Python 3 for live-container verification and the automated tests
- SELinux Enforcing is recommended and is the configuration this setup was designed around on Fedora

Do not run the installer or either launcher with `sudo`.

## Install on a new computer

```bash
./install.sh
```

The installer:

1. verifies Podman is rootless;
2. builds separate images pinned by `CODEX_VERSION` and `CLAUDE_VERSION`;
3. creates (or preserves) `codex-state` and `claude-state`;
4. seeds initial settings only when the corresponding settings file is absent; and
5. installs both launchers into `~/.local/bin`.

Install or update just one agent independently:

```bash
./install.sh --agent codex
./install.sh --agent claude
```

The default is `--agent all`. Set `AGENTS_ISOLATED_INSTALL_DIR` to override the
launcher installation directory; the existing `CODEX_ISOLATED_INSTALL_DIR`
fallback is retained. Image, state volume, and container names can be overridden
with the corresponding `CODEX_ISOLATED_*` or `CLAUDE_ISOLATED_*` variables
(`IMAGE`, `STATE_VOLUME`, `CONTAINER_NAME`, and `LOGIN_CONTAINER_NAME`).
`CODEX_ISOLATED_CONTAINER_NAME` is an exact name override; using the same override
for different repositories prevents those sessions from running concurrently.

Both agent versions are pinned. Base images, the Claude installer script, and Debian package repositories are not digest/snapshot pinned, so builds are repeatable but not bit-for-bit reproducible.

## Authenticate Codex

For accounts where device-code authentication is disabled:

```bash
codex-isolated login
```

This login container uses host networking because Codex's browser login listens on localhost. It **does not mount a workspace**; only the Codex state volume is mounted.

Check authentication with:

```bash
codex-isolated auth-status
```

Log out with:

```bash
codex-isolated logout
```

## Run Codex

Change into any directory inside the Git repository you want to expose:

```bash
cd /path/to/repository
codex-isolated
```

The launcher resolves the Git top-level directory and mounts only that repository at a stable, unique `/workspaces/<name>-<id>` path.

You can pass normal Codex arguments:

```bash
codex-isolated -m <model>
codex-isolated "Review this repository"
```

## Run Claude Code

```bash
./install.sh --agent claude
claude-isolated login
claude-isolated auth-status
cd /path/to/repository
claude-isolated
claude-isolated "Review this repository"
claude-isolated --secret-env GH_TOKEN=my-repo-token run
```

`login`, `auth-status`, and `logout` run `claude auth login`, `claude auth status`,
and `claude auth logout` without a repository mount or injected work-session
secrets. Login uses normal container networking. Open the printed login URL in
your host browser and paste the authorization code into the terminal if the
browser callback cannot reach the container. No graphical sockets are mounted;
terminal paste is sufficient. See [Claude's container login guidance](https://code.claude.com/docs/en/devcontainer).

Claude starts with its normal permission behavior. The launcher adds no
permission-bypass flags. Podman remains the primary boundary; approving a Claude
action grants access within the container, including its network and credentials.

The image uses the [official native installer](https://code.claude.com/docs/en/setup)
with the exact `CLAUDE_VERSION` and checks the installed version during the build.
Both background and manual Claude updates are disabled by environment defaults.
Update the version file and rebuild to update Claude. These defaults are not an
additional security boundary against code already running inside the container.
MCP servers and plugins that require extra runtimes need those dependencies added
to the image; arbitrary host applications and their configuration are not imported.

### Claude state and workspace identity

`CLAUDE_CONFIG_DIR=/state/claude` persists credentials, user settings, onboarding,
history, plugins, personal MCP configuration, and project trust in `claude-state`.
`HOME` stays at the image's disposable home directory. Generic application caches,
shell history, and other tools' credentials in that home disappear with the
container. Project-local `.claude` files live in the repository and persist there.
See [Claude's state persistence guidance](https://code.claude.com/docs/en/devcontainer#persist-authentication-and-settings-across-rebuilds).

The container workspace name combines a sanitized repository basename with the
first 16 hexadecimal characters of SHA-256 of the canonical host Git root. This
keeps the path stable across launches and distinguishes separate clones, even
with identical remotes. Moving or renaming a clone creates a new project identity;
clones on other machines do not automatically inherit trust. The full host path
is not included in the container workspace name. Git metadata itself may still
contain host paths.

Both launchers require a standalone clone with an ordinary `.git` directory.
Linked worktrees, submodule roots, and external Git directories are rejected
because their metadata can require paths outside the selected mount. Ordinary
subdirectories and submodules contained in a standalone repository remain inside
the mount; this does not promise that every host-specific Git configuration works.


Claude's configuration location, home, and update controls cannot be overridden
through launcher `--env` or `--secret-env` options. Both launchers reserve variable
names beginning `_AGENTS_` for their internal environment snapshot.

### Concurrent sessions

Codex sessions in different repositories may run concurrently. Each container is
named `codex-isolated-<name>-<id>`, using the sanitized repository basename
(truncated to 48 characters) and the first 16 hexadecimal characters of SHA-256
of the canonical Git root. Names are stable across launches and subdirectories;
separate clones get different names even when they have the same basename or
remote. Moving a clone changes its container name. The launcher prints the name.

The same repository allows one normal Codex session at a time. Both launchers
hold a host-side lock for the canonical repository while Podman runs. A second
isolated session using the same repository is rejected, including across Codex
and Claude, to avoid conflicting private SELinux relabels. Workspace labels on
running containers also prevent relabeling after a launcher detaches, including
containers started with the older fixed name. Claude continues to use its fixed
`claude-isolated` name and allows one Claude container at a time.

List active sessions, reconnect to one, or stop it with:

```bash
podman ps
podman attach <container-name>
podman stop <container-name>
```

A running session is never automatically stopped or replaced. A stopped stale
container with the selected name is removed automatically. Repository locks are
advisory and do not cover containers started directly with Podman; keep
`XDG_RUNTIME_DIR` consistent between launches. Do not delete lock files while
sessions are running.

All Codex containers share `codex-state`, including authentication, global and TUI
configuration, and session/history state. Each container still mounts only its
own selected repository at `/workspaces/<name>-<id>` and this shared volume at `/state`.
Repository filesystem isolation does not isolate data stored in shared state.

Codex workspace paths use the same sanitized basename and canonical host-path
hash as the container names. This gives each clone a distinct working directory
for directory-based session selection, including `resume --last`, while keeping
its path stable across launches and subdirectories. Moving or renaming a clone
changes this identity. The full host path is not included in the container path.

Older conversations saved with `/workspace` remain in shared history but no
longer match the new working directory. Use `codex-isolated resume --all` to
select one, or `codex-isolated resume <SESSION_ID>`, and choose the current
directory if prompted. Unique workspace paths do not restrict access to shared
history or configuration.

## Environment variables and secrets

Both launchers support explicit environment variables and Podman secrets in run mode:

| Option | Source | Container value |
| --- | --- | --- |
| `--env NAME` | Exported host variable | Value of `NAME` |
| `--secret-env VARIABLE=SECRET_NAME` | Named Podman secret | Secret content, exposed as `VARIABLE` |

Options are repeatable and must precede `run` and Codex arguments:

```bash
export MY_SETTING='some value'
codex-isolated --env MY_SETTING run "Review this repository"
codex-isolated --secret-env GH_TOKEN=my-repo-token run
codex-isolated --env MY_SETTING --secret-env GH_TOKEN=my-repo-token run -m <model>
```

Use `run` or `--` to end launcher option parsing. The launcher rejects duplicate
variable selections, invalid names, unexported variables, and unavailable
secrets. Environment options apply only to work sessions; `login`, `auth-status`,
`logout`, `version`, and `verify` reject them. No `.env` file is loaded
automatically. Podman's implicit proxy-variable forwarding is disabled for work
sessions and helpers. Select a proxy explicitly for a work session if needed,
for example `--env HTTPS_PROXY`.

### GitHub authentication

Both images include GitHub CLI (`gh`). Supplying `GH_TOKEN` enables GitHub CLI
commands and configures Git to use `gh` for HTTPS authentication to `github.com`.
The Git configuration applies only to the session and its child processes;
the entrypoint writes neither the token nor the helper configuration to disk.
Other existing Git settings are preserved.

Use an HTTPS remote such as `https://github.com/OWNER/REPO.git`. This integration
does not configure authentication for SSH remotes or GitHub Enterprise hosts.

Create a fine-grained personal access token (PAT) restricted to the target
repository with an expiration date. Required repository permissions are:

- **Contents: read/write** for pushing commits.
- **Pull requests: read/write** for creating and updating pull requests.

Organization policy may require token approval. See
[GitHub's token guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens).

Store the token as a Podman secret from a zsh shell on the host, using the same
rootless user as the launcher. The prompt hides input and keeps the token out of
shell history. Shell tracing (`set -x`) must be disabled.

```zsh
read -rs 'repo_pat?GitHub PAT: '
printf '\n'
printf '%s' "$repo_pat" | podman secret create my-repo-token -
unset repo_pat
```

Start a session from the target repository:

```bash
codex-isolated --secret-env GH_TOKEN=my-repo-token run
```

Check authentication inside the session with `gh auth status` and
`git ls-remote origin`.

### Token rotation and removal

Stop the session and create a replacement PAT. Replace the stored secret on the
host:

```zsh
read -rs 'repo_pat?New GitHub PAT: '
printf '\n'
printf '%s' "$repo_pat" | podman secret create --replace my-repo-token -
unset repo_pat
```

Start a new session to use the replacement and revoke the old PAT on GitHub.

Remove the local secret with:

```bash
podman secret rm my-repo-token
```

Removing a local secret does not revoke the PAT on GitHub. Uninstallation
preserves user-managed Podman secrets.

### Credential access

Secret selection is required on each launch. Repository access is determined by
the PAT's permissions; secret names do not restrict access to a repository.

The agent and its child processes can read supplied variables and tokens.
Environment diagnostics and runtime inspection may expose their values. Avoid
printing tokens or storing them in repository files, remote URLs, or image build
arguments. Podman's default file secret driver stores secrets without encryption;
see [Podman secret storage](https://docs.podman.io/en/latest/markdown/podman-secret-create.1.html).

## Workspace checks

The launcher refuses to run when:

- the current directory is not in a Git working tree;
- the resolved repository root is `/`;
- the resolved repository root is the user's home directory or an ancestor of it;
- another container with the selected agent name is already running; or
- either launcher is already using the same canonical repository.

These checks reduce the risk of accidentally mounting a broad host directory.

## Verify

```bash
codex-isolated verify
```

Run `verify` inside the repository to check its running Codex container. Outside a
repository, general setup checks still run and live-container checks are skipped.
An explicit `CODEX_ISOLATED_CONTAINER_NAME` selects that container for verification.

For manual checks, use `podman ps` to find the container name and replace
`<container-name>` below:

```bash
podman inspect <container-name> \
  --format '{{range .Mounts}}{{println .Source "->" .Destination}}{{end}}'

podman info --format '{{.Host.Security.Rootless}}'

podman inspect <container-name> \
  --format 'Privileged={{.HostConfig.Privileged}} CapDrop={{.HostConfig.CapDrop}} SecurityOpt={{.HostConfig.SecurityOpt}}'

podman inspect <container-name> \
  --format 'EffectiveCaps={{.EffectiveCaps}} BoundingCaps={{.BoundingCaps}}'

podman inspect <container-name> \
  --format 'ProcessLabel={{.ProcessLabel}} MountLabel={{.MountLabel}}'
```

A Fedora/SELinux run should show only the repository and state volume as host-backed mounts, rootless Podman, `Privileged=false`, empty effective/bounding capabilities, `no-new-privileges`, and SELinux container labels.

## Why the bind mount is non-recursive

A normal bind mount can include separate mount points that exist underneath the selected host directory. The launcher uses Podman's `bind-nonrecursive` option to avoid accidentally exposing such nested filesystems to Codex.

Ordinary repository files, subdirectories, and Git submodules that are normal directories remain available. A separately mounted filesystem underneath the repository is not intentionally imported.

## SELinux note

`relabel=private` is equivalent to Podman's `:Z` behavior. Podman relabels the repository for the container's private SELinux context. Those labels can remain after the container exits. This is normally fine for ordinary workstation access, but can matter if another confined service also needs that directory.

If necessary, restore the normal host labels after the container is stopped, for example on Fedora:

```bash
restorecon -RFv /path/to/repository
```

The next `codex-isolated` run will relabel the repository again.

## Persistent state

The `codex-state` volume survives container/image recreation and contains Codex authentication and user state. Claude uses the separate `claude-state` volume.

Inspect it with:

```bash
podman volume inspect codex-state
```

Do not publish, copy, or commit its contents.

## Update Codex

Change `CODEX_VERSION` (or `CLAUDE_VERSION` for Claude), review the new version, and rerun:

```bash
./install.sh
codex-isolated version
codex-isolated verify
```

The existing state volume is retained.

## Uninstall

Remove both agents' launchers and images while keeping authentication/state. Stop
running agent containers first; uninstallation discovers labeled agent containers
and checks all selected agents before removing launchers, containers, or images:

```bash
./uninstall.sh
```

Remove the state volume as well:

```bash
./uninstall.sh --purge-state
```

Use `--agent codex` or `--agent claude` with either uninstall command to select
one agent. `--purge-state` warns before deleting the selected agents' authentication,
history, and settings. It does not delete repository-local configuration, revoke
provider credentials, or delete Podman secrets. Purging Claude state gives Claude
a fresh user configuration; existing repository-local files still apply.

## Claude security acceptance checks on Fedora

Run `make test` for syntax, launcher, entrypoint, and lifecycle tests. Launcher
tests use fake Podman and do not establish runtime isolation. Before relying on
Claude support, complete this procedure with rootless Podman and SELinux Enforcing:

1. Run `./install.sh --agent claude`, `claude-isolated login`, and
   `claude-isolated auth-status`. Confirm login succeeds without host networking.
2. Start `claude-isolated` in a disposable standalone Git repository. Complete
   onboarding, trust the project, and create a test session. In another terminal:

   ```bash
   podman info --format '{{.Host.Security.Rootless}}'
   getenforce
   podman inspect claude-isolated --format \
     'Privileged={{.HostConfig.Privileged}} Caps={{.EffectiveCaps}}/{{.BoundingCaps}} Security={{.HostConfig.SecurityOpt}}'
   podman inspect claude-isolated --format \
     '{{range .Mounts}}{{println .Type .Source "->" .Destination}}{{end}}'
   podman inspect claude-isolated --format \
     'Process={{.ProcessLabel}} Mount={{.MountLabel}} Network={{.HostConfig.NetworkMode}}'
   claude-isolated verify
   ```

   Require rootless `true`, SELinux `Enforcing`, privileged `false`, empty effective
   and bounding capabilities, `no-new-privileges`, container SELinux labels, and
   ordinary container networking. Require exactly the selected repository bind
   mount at its hashed path and the `claude-state` volume at `/state/claude`.
   No unrelated host directories, graphical sockets, agents, or runtime sockets
   should appear. Inspect the repository mount for private labeling and
   non-recursive binding; for a functional check, arrange a separate test mount
   under the repository before launch and confirm its mounted contents are absent.
3. Create a host-only sentinel outside the repository and verify it is absent:

   ```bash
   host_sentinel=$(mktemp /tmp/agents-host-only.XXXXXXXX)
   printf 'host-only sentinel\n' > "$host_sentinel"
   podman exec claude-isolated sh -c 'test ! -e "$1"' sh "$host_sentinel"
   rm -- "$host_sentinel"
   ```

   Also try reading it through Claude's shell tool. Test isolation through shell
   commands as well as file tools, since permission prompts alone prove nothing
   about the mount boundary.
4. Have Claude create a repository file. Confirm the host user owns it and can
   edit/delete it. Attempt a second Codex or Claude launch in the same repository;
   it must fail. Confirm the other agent can run in a different repository.
5. End the session and launch a new container from the same clone. Confirm login,
   onboarding, project trust, and session resume survive. Rebuild the image and
   repeat. Check that an arbitrary file written to the disposable container home
   does not survive container recreation. A different clone of the same remote
   must use a different container workspace path and require its own trust decision.
6. Launch with `--secret-env GH_TOKEN=my-repo-token`. Run `gh auth status` and
   `git ls-remote origin` without printing the token. Recreate without the secret
   and confirm `GH_TOKEN` is absent. Check that the entrypoint created no GitHub
   credential files or persistent Git credential-helper settings.
7. Verify existing Claude settings survive reinstalling. Test default uninstall
   and targeted uninstall: state and secrets must survive. Test state purge only
   with disposable state; it must reset user authentication/onboarding while
   leaving Codex state and Podman secrets intact when targeting Claude alone.

Network access remains available, including reachable host services. The boundary
protects unmounted files through filesystem isolation, not network-reachable
services or a compromised kernel/runtime. Each agent's shared state can include
credentials and history from other projects used with that same agent.

## Deliberately not exposed

The launcher does not mount or forward:

- the host home directory;
- `~/.ssh`;
- `~/.kube`;
- host Git credentials;
- the SSH agent;
- Podman/Docker sockets;
- host environment variables unless explicitly selected for a work session; or
- other repositories.

GitHub access uses explicitly selected Podman secrets as described in
[GitHub authentication](#github-authentication). Host credential directories
remain outside the container.
