# Security notes

## Intended boundary

The outer rootless Podman container is the security boundary. Codex is intentionally run without its own command sandbox inside that container.
Claude uses normal permission behavior; those prompts and file-tool restrictions
are defense in depth rather than the host isolation boundary.

Host data intentionally exposed to the container:

1. the selected Git repository at a stable unique
   `/workspaces/<name>-<id>` path for either agent;
2. the agent's own named volume: `codex-state` at `/state` or `claude-state`
   at `/state/claude`. The other agent's state is not mounted.

No other host filesystem paths should be mounted by the supplied launcher.

Run mode optionally accepts `--env NAME` and
`--secret-env VARIABLE=SECRET_NAME`. These explicitly expose selected host
variables or Podman secrets as container environment variables. They add no
host bind mounts. Login/status/logout/version/verify helpers reject these
options and receive no selected variables or secrets. The launcher does not
automatically read `.env` files or forward the full host environment. Podman's
implicit proxy-variable forwarding is disabled for work sessions and helpers.

## Important limitations

- The container has normal Internet/network access.
- Codex can read `/state`, including its own authentication material. Parallel
  Codex containers share this state volume, including configuration and history;
  it provides no per-repository isolation for data stored there. Each container
  still mounts only its selected repository at its unique `/workspaces/<name>-<id>` path.
- Rootless containers share the host kernel; a kernel/runtime escape is outside this design's protection.
- SELinux provides defense in depth when Enforcing but is not a substitute for checking the mount list.
- The workspace mount is writable by design, including `.git/`. An agent can add
  `.git/hooks/*` or `.git/config` settings such as `core.fsmonitor`,
  `core.sshCommand`, `core.pager`, or aliases. Those run **on the host, outside
  the container**, the next time you run Git there. When a session ends, the
  launchers warn if `.git/config`, `.git/config.worktree`, or `.git/hooks`
  changed; this is detection, not prevention, and it misses changes made after
  detaching from a session. Review those files before running host Git, or use
  a separate host clone. The same applies to other repository files you later
  execute on the host, such as build scripts or editor task files.
- A repository itself may contain sensitive data; this tool does not classify or filter repository contents.
- Supplied tokens and variables are readable by the agent and its child processes, and may appear in environment diagnostics or runtime inspection. Podman secrets control delivery; they do not conceal credentials from processes in the receiving container or its host user. The default file secret driver does not encrypt secrets at rest.
- Scope PATs to the intended repository and permissions, with an expiration. The launcher does not associate a secret with a particular repository; selecting the token authorizes that container to use it within the token's scope.

## Unsafe changes

Review carefully before adding any of the following to the launcher:

- `--privileged`;
- `--security-opt label=disable`;
- `--security-opt seccomp=unconfined`;
- host `/` or `$HOME` mounts;
- `~/.ssh`, `~/.kube`, or credential-directory mounts;
- the Podman/Docker socket;
- SSH-agent forwarding;
- `--env-host` or broad host environment forwarding;
- device passthrough.

These can materially weaken the isolation model.

## Authentication

Codex browser login uses a separate container with host networking so the localhost OAuth callback works. That login container does not mount a repository. The resulting credentials are stored in the named state volume.

When a work session receives `GH_TOKEN`, the entrypoint adds a process-local
GitHub HTTPS credential helper using `gh`. Neither the token nor this helper
configuration is persisted by the entrypoint. GitHub tokens do not affect Codex
account login. A session can still deliberately write its own credentials to
files, so do not ask the agent to print or persist tokens.

Secrets are managed by the host user with `podman secret`, independently of
installation and uninstallation. Recreate sessions after replacing a secret;
replacing/removing a local secret does not revoke the token at its provider.

## Claude-specific state and lifecycle

Claude keeps its disposable image home and persists only `CLAUDE_CONFIG_DIR` in
`claude-state`, including `.claude.json`, credentials, settings, trust, and history.
The volume is shared across that user's Claude projects. Code in any such session
can read or alter the whole volume; project path separation is not per-project
credential isolation. Deleting state does not revoke provider credentials or erase
repository-local settings. GitHub token delivery remains explicit per work session.

The image pins `CLAUDE_VERSION`. Runtime defaults disable automatic and manual
Claude updates. New user settings contain an empty `permissions` object and add
no file-tool restrictions. Processes can access the mounted repository and state;
Podman provides the filesystem boundary. No bypass mode is added by default.

Workspace paths hash canonical local roots, not repository-controlled remote URLs.
Separate clones do not inherit the same project identity. Claude rejects linked
worktrees and external Git metadata rather than adding more host mounts.

Both launchers use an advisory host-side repository lock to avoid simultaneous
private SELinux relabeling of the same repository. Workspace labels also detect
running detached agent containers. Direct Podman invocations can
bypass this coordination. Network access may reach host services; no network
service isolation is claimed. Rootless containers still share the host kernel.

See the README's Fedora acceptance procedure for runtime checks. Mocked launcher
tests alone cannot verify SELinux behavior, secret storage, file ownership, or
Claude authentication persistence.
