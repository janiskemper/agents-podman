#!/usr/bin/env python3
"""Exercise actual launchers/lifecycle scripts against an argv-aware fake Podman."""
import hashlib
import json
import os
import re
from pathlib import Path
import subprocess
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
FAKE = r'''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
args = sys.argv[1:]
with open(os.environ['FAKE_LOG'], 'a') as log:
    log.write(json.dumps({'args': args, 'env': {k: os.environ.get(k) for k in ['IMAGE', 'name', 'workspace', 'RUN_ENV_ARGS', 'RUN_ENV_EXPORTS', 'SETTING']}}) + '\n')
pair = args[:2]
registry = Path(os.environ['FAKE_REGISTRY'])
containers = {p.name: json.loads(p.read_text()) for p in registry.iterdir()}

if pair == ['info', '--format']:
    print(os.environ.get('FAKE_ROOTLESS', 'true'))
elif pair == ['container', 'exists']:
    sys.exit(0 if args[2] in containers or args[2] in os.environ.get('FAKE_CONTAINER', '').splitlines() else 1)
elif pair == ['secret', 'exists']:
    sys.exit(1 if args[2] == 'missing' else 0)
elif pair == ['volume', 'exists']:
    sys.exit(1 if os.environ.get('FAKE_MISSING_VOLUME') else 0)
elif pair == ['image', 'ls']:
    if os.environ.get('FAKE_IMAGE_LS_FAIL'):
        sys.exit(125)
    for agent in ('codex', 'claude'):
        print(f'localhost/{agent}-one-folder:latest\nlocalhost/{agent}-one-folder:0.0.1-old')
    print('localhost/unrelated:latest\n<none>:<none>')
elif args[0] == 'ps':
    print(os.environ.get('FAKE_ACTIVE', ''), end='')
    for name, container in containers.items():
        filters = [args[i+1] for i, arg in enumerate(args) if arg == '--filter']
        if all(f == 'status=running' or (f.startswith('label=') and f[6:] in container['labels']) for f in filters):
            print(name)
elif args[0] == 'inspect':
    if os.environ.get('FAKE_INSPECT_FAIL'):
        sys.exit(125)
    if '--format' not in args:
        print(os.environ['FAKE_INSPECT'])
    else:
        fmt = args[args.index('--format') + 1]
        print({'{{.State.Running}}': os.environ.get('FAKE_RUNNING', 'true'),
               '{{.HostConfig.Privileged}}': 'false',
               '{{.EffectiveCaps}}/{{.BoundingCaps}}': '[]/[]',
               '{{.HostConfig.SecurityOpt}}': '[no-new-privileges]',
               '{{.ProcessLabel}}': 'system_u:system_r:container_t:s0:c1,c2'}.get(fmt, 'labels'))
elif pair == ['run', '--rm'] and os.environ.get('FAKE_GIT_HOOK'):
    hook = Path(os.environ['FAKE_GIT_HOOK'])
    hook.parent.mkdir(exist_ok=True)
    hook.write_text('#!/bin/sh\n')
    sys.exit(3)
elif pair == ['run', '--rm'] and os.environ.get('FAKE_BLOCK'):
    name = args[args.index('--name') + 1]
    state = registry / name
    state.write_text(json.dumps({'labels': [args[i+1] for i, arg in enumerate(args) if arg == '--label']}))
    Path(os.environ['FAKE_BLOCK'] + '.started').touch()
    try:
        for _ in range(1500):
            if Path(os.environ['FAKE_BLOCK'] + '.release').exists(): break
            time.sleep(.02)
        else: sys.exit(2)
    finally:
        state.unlink()
'''


def main():
    with tempfile.TemporaryDirectory(prefix='agents-test-') as tmp:
        base = Path(tmp)
        (base / 'bin').mkdir()
        (base / 'home').mkdir()
        (base / 'runtime').mkdir()
        (base / 'registry').mkdir()
        fake = base / 'bin/podman'
        fake.write_text(FAKE)
        fake.chmod(0o755)
        log = base / 'calls.jsonl'
        env = dict(os.environ, PATH=f'{base}/bin:' + os.environ['PATH'],
                   HOME=str(base / 'home'), XDG_RUNTIME_DIR=str(base / 'runtime'),
                   FAKE_LOG=str(log), FAKE_REGISTRY=str(base / 'registry'), AGENTS_ISOLATED_INSTALL_DIR=str(base / 'installed'))
        for key in list(env):
            if key.startswith(('CODEX_ISOLATED_', 'CLAUDE_ISOLATED_', 'FAKE_')) and key not in ('FAKE_LOG', 'FAKE_REGISTRY'):
                del env[key]
        def git(*args):
            subprocess.run(['git', *map(str, args)], check=True, capture_output=True, env=env)
        repo = base / 'repo with spaces'
        repo.mkdir()
        git('-C', repo, 'init', '-q')
        (repo / 'subdir').mkdir()
        other = base / 'other' / repo.name
        other.mkdir(parents=True)
        git('-C', other, 'init', '-q')
        for path in (repo, other):
            git('-C', path, 'remote', 'add', 'origin', 'https://example.invalid/team/repo.git')
        git('-C', base / 'home', 'init', '-q')
        def calls():
            return [json.loads(x) for x in log.read_text().splitlines()] if log.exists() else []
        def run(script, *args, cwd=repo, extra=None, ok=True):
            log.write_text('')
            result = subprocess.run([str(ROOT / script), *args], cwd=cwd,
                                    env=dict(env, **(extra or {})), text=True, capture_output=True)
            assert (result.returncode == 0) == ok, (script, args, result.stdout, result.stderr)
            return calls(), result
        def launch(agent, *args, **kwargs):
            return run(f'bin/{agent}-isolated', *args, **kwargs)
        def podman_run(records):
            return [x for x in records if x['args'][:2] == ['run', '--rm']][-1]
        def mounts(record):
            args = record['args']
            return [args[i+1] for i, value in enumerate(args) if value == '--mount']
        codex_name = 'codex-isolated-repo-with-spaces-' + hashlib.sha256(os.fsencode(str(repo))).hexdigest()[:16]
        def workspace_destination(path):
            name = re.sub(rb'[^A-Za-z0-9_.-]', b'-', os.fsencode(path.name))[:48].decode('ascii')
            return '/workspaces/' + name + '-' + hashlib.sha256(os.fsencode(str(path))).hexdigest()[:16]
        def container_name(record):
            args = record['args']
            return args[args.index('--name') + 1]
        def assert_no_removals(records):
            assert not any(x['args'][0] in ('rm', 'rmi') or
                           x['args'][:2] in (['volume', 'rm'], ['secret', 'rm'])
                           for x in records), records
        for agent in ('codex', 'claude'):
            records, _ = launch(agent, cwd=repo / 'subdir')
            record = podman_run(records)
            args = record['args']
            assert '--cap-drop=all' in args and '--security-opt=no-new-privileges' in args
            assert '--http-proxy=false' in args
            assert f'io.agents-podman.agent={agent}' in args
            assert not any(x.startswith(('--network', '--privileged', '--env-host')) for x in args)
            selected_mounts = mounts(record)
            assert len(selected_mounts) == 2
            assert f'src={repo},' in selected_mounts[0]
            assert 'relabel=private,bind-nonrecursive' in selected_mounts[0]
            assert not any(x in ' '.join(selected_mounts) for x in ('/home/', '.ssh', '.kube', 'docker.sock', 'podman.sock', 'wayland', '.X11'))
            assert '--env' not in args and '--secret' not in args
            if agent == 'claude':
                assert 'src=claude-state,dst=/state/claude' in selected_mounts[1]
                assert '--dangerously-skip-permissions' not in args
            records, _ = launch(agent, '--env', 'IMAGE', '--env', 'name', '--env', 'workspace',
                                '--env', 'RUN_ENV_ARGS', '--env', 'RUN_ENV_EXPORTS', '--env', 'SETTING',
                                '--secret-env', 'GH_TOKEN=test-token', 'run', 'a prompt with spaces',
                                extra={'IMAGE': 'original image', 'name': 'original name', 'workspace': 'original workspace',
                                       'RUN_ENV_ARGS': 'original args', 'RUN_ENV_EXPORTS': 'original exports',
                                       'SETTING': 'quotes \' " $;\nsecond line\n'})
            record = podman_run(records)
            assert record['env'] == {'IMAGE': 'original image', 'name': 'original name', 'workspace': 'original workspace',
                                     'RUN_ENV_ARGS': 'original args', 'RUN_ENV_EXPORTS': 'original exports',
                                     'SETTING': 'quotes \' " $;\nsecond line\n'}
            assert 'test-token,type=env,target=GH_TOKEN' in record['args']
            assert 'a prompt with spaces' in record['args']
            assert not any(value in record['args'] for value in record['env'].values())
            for opts in (['--env'], ['--env', 'UNSET_TEST_NAME'], ['--env', 'BAD=value'],
                         ['--env', '_AGENTS_PRIVATE'], ['--secret-env', 'GH_TOKEN'],
                         ['--secret-env', 'GH_TOKEN='], ['--secret-env', 'GH_TOKEN=missing'],
                         ['--secret-env', 'GH_TOKEN=token,type=mount'],
                         ['--env', 'SETTING', '--secret-env', 'SETTING=token'],
                         ['--secret-env', 'GH_TOKEN=token', '--secret-env', 'GH_TOKEN=token']):
                records, _ = launch(agent, *opts, extra={'SETTING': 'test'}, ok=False)
                assert not any(x['args'][0] == 'run' for x in records)
            for cwd in (base, base / 'home'):
                records, _ = launch(agent, cwd=cwd, ok=False)
                assert not any(x['args'][0] == 'run' for x in records)
            launch(agent, extra={'FAKE_ROOTLESS': 'false'}, ok=False)
            launch(agent, extra={'FAKE_ACTIVE': 'detached-other-agent'}, ok=False)
            for helper in ('login', 'auth-status', 'logout', 'version', 'verify'):
                for opts in (['--env', 'SETTING'], ['--secret-env', 'GH_TOKEN=token']):
                    launch(agent, *opts, helper, extra={'SETTING': 'test'}, ok=False)
                records, _ = launch(agent, helper)
                for record in records:
                    if record['args'][0] == 'run':
                        assert '--env' not in record['args'] and '--secret' not in record['args']
                        assert all('type=bind' not in x for x in mounts(record))
                        if helper == 'login':
                            assert f'io.agents-podman.agent={agent}' in record['args']
                        if agent == 'claude': assert '--network=host' not in record['args']
            for delimiter in ('run', '--'):
                records, _ = launch(agent, delimiter, '--env', 'AGENT_OPTION')
                assert podman_run(records)['args'][-2:] == ['--env', 'AGENT_OPTION']
            # Host-executed Git metadata changes are reported; the exit status survives.
            _, result = launch(agent)
            assert '.git/config or .git/hooks' not in result.stderr
            hook = repo / '.git/hooks/post-checkout'
            _, result = launch(agent, extra={'FAKE_GIT_HOOK': str(hook)}, ok=False)
            assert result.returncode == 3 and '.git/config or .git/hooks' in result.stderr
            hook.unlink()
            name = codex_name if agent == 'codex' else 'claude-isolated'
            records, result = launch(agent, extra={'FAKE_CONTAINER': name, 'FAKE_RUNNING': 'true'}, ok=False)
            if agent == 'codex':
                assert f'podman attach {name}' in result.stderr and f'podman stop {name}' in result.stderr
                assert not any(x['args'][0] in ('rm', 'stop', 'run') for x in records)
            records, _ = launch(agent, extra={'FAKE_CONTAINER': name, 'FAKE_RUNNING': 'false'})
            assert any(x['args'] == ['rm', name] for x in records)
        # A Git repository at /, or at a parent of HOME, must never be mounted.
        root_bin = base / 'root-git-bin'
        root_bin.mkdir()
        root_git = root_bin / 'git'
        root_git.write_text('#!/bin/sh\ncase "$*" in\n  "rev-parse --is-inside-work-tree") echo true ;;\n  "rev-parse --show-toplevel") echo / ;;\n  *) exit 1 ;;\nesac\n')
        root_git.chmod(0o755)
        for agent in ('codex', 'claude'):
            launch(agent, extra={'PATH': str(root_bin) + ':' + env['PATH']}, ok=False)
        git('-C', base, 'init', '-q')
        comma_repo = base / 'repo,comma'
        comma_repo.mkdir()
        git('-C', comma_repo, 'init', '-q')
        for agent in ('codex', 'claude'):
            launch(agent, cwd=base, ok=False)
            launch(agent, cwd=comma_repo, ok=False)
        for reserved in ('HOME', 'CLAUDE_CONFIG_DIR', 'DISABLE_AUTOUPDATER', 'DISABLE_UPDATES'):
            launch('claude', '--secret-env', f'{reserved}=token', ok=False)
        for reserved in ('HOME', 'CODEX_HOME'):
            launch('codex', '--secret-env', f'{reserved}=token', ok=False)
        # Stable canonical identities distinguish same-remote clones and resolve aliases.
        records, _ = launch('claude')
        first = podman_run(records)
        destination = first['args'][first['args'].index('--workdir') + 1]
        assert destination == '/workspaces/repo-with-spaces-' + hashlib.sha256(os.fsencode(str(repo))).hexdigest()[:16]
        records, _ = launch('claude', cwd=other)
        assert destination != podman_run(records)['args'][podman_run(records)['args'].index('--workdir') + 1]
        alias = base / 'alias'
        alias.symlink_to(repo, target_is_directory=True)
        records, _ = launch('claude', cwd=alias)
        assert destination == podman_run(records)['args'][podman_run(records)['args'].index('--workdir') + 1]
        # Codex identities are canonical, bounded, safe, and independent of remotes.
        for cwd in (repo, repo / 'subdir', alias):
            records, _ = launch('codex', cwd=cwd)
            record = podman_run(records)
            assert container_name(record) == codex_name
            assert record['args'][record['args'].index('--workdir') + 1] == workspace_destination(repo)
            assert f'dst={workspace_destination(repo)},' in mounts(record)[0]
        records, _ = launch('codex', cwd=other)
        assert container_name(podman_run(records)) != codex_name
        for basename in ('-odd ü $;![]', 'x' * 240):
            unusual = base / basename
            unusual.mkdir()
            git('-C', unusual, 'init', '-q')
            records, _ = launch('codex', cwd=unusual)
            name = container_name(podman_run(records))
            assert re.fullmatch(r'codex-isolated-[A-Za-z0-9_.-]{1,48}-[0-9a-f]{16}', name), name
            assert len(name) <= 80
            record = podman_run(records)
            assert record['args'][record['args'].index('--workdir') + 1] == workspace_destination(unusual)
            assert f'dst={workspace_destination(unusual)},' in mounts(record)[0]
        records, _ = launch('codex', extra={'CODEX_ISOLATED_CONTAINER_NAME': 'custom-codex'})
        assert container_name(podman_run(records)) == 'custom-codex'
        record = podman_run(records)
        assert record['args'][record['args'].index('--workdir') + 1] == workspace_destination(repo)
        records, _ = launch('codex', extra={'CODEX_ISOLATED_CONTAINER_NAME': 'custom-codex',
                                          'FAKE_CONTAINER': 'custom-codex'}, ok=False)
        assert not any(x['args'][0] == 'run' for x in records)
        _, result = launch('codex', 'verify', cwd=base.parent)
        assert 'Run verify inside a Git repository' in result.stdout
        # Detached and legacy sessions are detected by their workspace labels.
        _, result = launch('codex', extra={'FAKE_ACTIVE': 'codex-isolated'}, ok=False)
        assert 'podman attach codex-isolated' in result.stderr
        external = base / 'external'
        external.mkdir()
        git('init', '-q', '--separate-git-dir', base / 'git-dir', external)
        for agent in ('codex', 'claude'):
            launch(agent, cwd=external, ok=False)
        # Live verification checks actual sources and rejects unexpected mounts.
        inspected = {'Mounts': [
            {'Type': 'bind', 'Source': str(repo), 'Destination': destination},
            {'Type': 'volume', 'Name': 'claude-state', 'Destination': '/state/claude'}],
            'Config': {'WorkingDir': destination}}
        launch('claude', 'verify', extra={'FAKE_CONTAINER': 'claude-isolated', 'FAKE_INSPECT': json.dumps([inspected])})
        inspected['Mounts'].append({'Type': 'bind', 'Source': '/tmp', 'Destination': '/extra'})
        launch('claude', 'verify', extra={'FAKE_CONTAINER': 'claude-isolated', 'FAKE_INSPECT': json.dumps([inspected])}, ok=False)
        inspected = {'Mounts': [
            {'Type': 'bind', 'Source': str(repo), 'Destination': workspace_destination(repo)},
            {'Type': 'volume', 'Name': 'codex-state', 'Destination': '/state'}],
            'Config': {'WorkingDir': workspace_destination(repo)}}
        _, result = launch('codex', 'verify', extra={'FAKE_CONTAINER': codex_name, 'FAKE_INSPECT': json.dumps([inspected])})
        assert f'Checking running container {codex_name}' in result.stdout
        launch('codex', 'verify', cwd=base.parent,
               extra={'CODEX_ISOLATED_CONTAINER_NAME': 'custom-codex', 'FAKE_CONTAINER': 'custom-codex',
                      'FAKE_INSPECT': json.dumps([inspected])})
        # Reject a mismatched workspace identity, cwd, extra mounts, and legacy paths.
        for destination_path, working_dir in (
                (workspace_destination(other), workspace_destination(other)),
                (workspace_destination(repo), '/workspace'),
                ('/workspace', '/workspace')):
            invalid = json.loads(json.dumps(inspected))
            invalid['Mounts'][0]['Destination'] = destination_path
            invalid['Config']['WorkingDir'] = working_dir
            launch('codex', 'verify', extra={'FAKE_CONTAINER': codex_name, 'FAKE_INSPECT': json.dumps([invalid])}, ok=False)
        invalid = json.loads(json.dumps(inspected))
        invalid['Mounts'].append({'Type': 'bind', 'Source': '/tmp', 'Destination': '/extra'})
        launch('codex', 'verify', extra={'FAKE_CONTAINER': codex_name, 'FAKE_INSPECT': json.dumps([invalid])}, ok=False)
        inspected['Mounts'][0]['Source'] = str(other)
        launch('codex', 'verify', extra={'FAKE_CONTAINER': codex_name, 'FAKE_INSPECT': json.dumps([inspected])}, ok=False)
        inspected['Mounts'][0]['Source'] = str(base / 'home')
        launch('codex', 'verify', extra={'FAKE_CONTAINER': codex_name, 'FAKE_INSPECT': json.dumps([inspected])}, ok=False)
        # The host lock is held across Podman and works in both agent directions.
        for first_agent, second_agent in (('codex', 'claude'), ('claude', 'codex'), ('codex', 'codex')):
            block = str(base / f'block-{first_agent}-{second_agent}')
            proc = subprocess.Popen([str(ROOT / f'bin/{first_agent}-isolated')], cwd=repo,
                                    env=dict(env, FAKE_BLOCK=block), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            try:
                deadline = time.monotonic() + 5
                while not Path(block + '.started').exists():
                    assert proc.poll() is None, proc.communicate()
                    assert time.monotonic() < deadline, 'Podman did not start'
                    time.sleep(.02)
                first_run = podman_run(calls())
                records, result = launch(second_agent, ok=False)
                assert not any(x['args'][0] in ('rm', 'stop', 'run') for x in records)
                if second_agent == 'codex':
                    assert f'podman attach {container_name(first_run)}' in result.stderr
                records, _ = launch(second_agent, cwd=other)
                assert proc.poll() is None, 'first session must remain active'
                if first_agent == second_agent == 'codex':
                    second_run = podman_run(records)
                    assert container_name(first_run) != container_name(second_run)
                    for record, workspace in ((first_run, repo), (second_run, other)):
                        assert mounts(record) == [
                            f'type=bind,src={workspace},dst={workspace_destination(workspace)},relabel=private,bind-nonrecursive',
                            'type=volume,src=codex-state,dst=/state']
                        assert '--cap-drop=all' in record['args']
                        assert '--security-opt=no-new-privileges' in record['args']
                        assert not any(x.startswith(('--network', '--privileged', '--env-host', '--device', '--cap-add')) for x in record['args'])
                    # Uninstall must discover the active repository-specific container.
                    records, result = run('uninstall.sh', '--agent', 'codex', ok=False)
                    assert f'podman stop {container_name(first_run)}' in result.stderr
                    assert not any(x['args'][0] in ('rm', 'rmi') or x['args'][:2] == ['volume', 'rm'] for x in records)
            finally:
                Path(block + '.release').touch()
                proc.communicate(timeout=5)
                assert proc.returncode == 0
        # Changed run/login overrides must not hide active default containers.
        for agent in ('codex', 'claude'):
            for suffix, default in (('CONTAINER_NAME', f'{agent}-isolated'),
                                    ('LOGIN_CONTAINER_NAME', f'{agent}-isolated-login')):
                records, result = run('uninstall.sh', '--agent', agent,
                                      extra={f'{agent.upper()}_ISOLATED_{suffix}': f'custom-{agent}',
                                             'FAKE_CONTAINER': default}, ok=False)
                assert f'podman stop {default}' in result.stderr
                assert_no_removals(records)
        # Label discovery also finds a custom Claude name from an earlier launch.
        custom_claude = base / 'registry/earlier-custom-claude'
        custom_claude.write_text(json.dumps({'labels': ['io.agents-podman.agent=claude']}))
        try:
            records, result = run('uninstall.sh', '--agent', 'claude', ok=False)
            assert 'podman stop earlier-custom-claude' in result.stderr
            assert_no_removals(records)
            records, _ = run('uninstall.sh', '--agent', 'codex')
            assert not any('earlier-custom-claude' in x['args'] for x in records)
        finally:
            custom_claude.unlink()
        # Preflight every selected agent before deleting either installation.
        installed = base / 'installed'
        installed.mkdir(exist_ok=True)
        launcher_contents = {agent: f'keep {agent}\n' for agent in ('codex', 'claude')}
        for agent, contents in launcher_contents.items():
            (installed / f'{agent}-isolated').write_text(contents)
        for options, extra in (
                (['--purge-state'], {'FAKE_CONTAINER': 'claude-isolated'}),
                (['--purge-state'], {'FAKE_CONTAINER': 'codex-isolated',
                                     'FAKE_RUNNING': 'unknown'}),
                (['--purge-state'], {'FAKE_CONTAINER': 'codex-isolated',
                                     'FAKE_INSPECT_FAIL': '1'}),
                (['--purge-state'], {'FAKE_CONTAINER': 'codex-isolated',
                                     'FAKE_RUNNING': 'false', 'FAKE_IMAGE_LS_FAIL': '1'})):
            records, _ = run('uninstall.sh', *options, extra=extra, ok=False)
            assert_no_removals(records)
            for agent, contents in launcher_contents.items():
                assert (installed / f'{agent}-isolated').read_text() == contents
        # Targeted lifecycle actions preserve the other agent and user-managed secrets.
        for agent in ('codex', 'claude'):
            # Version tags replace the final tag and preserve registry ports.
            version = (ROOT / f'{agent.upper()}_VERSION').read_text().strip()
            for image_repo in (f'localhost/{agent}-custom', f'registry.example:5000/team/{agent}'):
                for tag in ('', ':custom'):
                    image = image_repo + tag
                    records, _ = run('install.sh', '--agent', agent,
                                     extra={f'{agent.upper()}_ISOLATED_IMAGE': image})
                    builds = [x['args'] for x in records if x['args'][0] == 'build']
                    assert len(builds) == 1
                    tags = [builds[0][i+1] for i, arg in enumerate(builds[0]) if arg == '-t']
                    assert tags == [image, f'{image_repo}:{version}'], tags
            records, _ = run('install.sh', '--agent', agent, extra={'FAKE_MISSING_VOLUME': '1'})
            assert (base / f'installed/{agent}-isolated').is_file()
            builds = [x['args'] for x in records if x['args'][0] == 'build']
            assert len(builds) == 1 and f'{agent.upper()}_VERSION=' in ' '.join(builds[0])
            assert any(x['args'] == ['volume', 'create', f'{agent}-state'] for x in records)
            records, _ = run('install.sh', '--agent', agent)
            assert not any(x['args'][:2] == ['volume', 'create'] for x in records)
            records, _ = run('uninstall.sh', '--agent', agent)
            assert not any(x['args'][:2] in (['volume', 'rm'], ['secret', 'rm']) for x in records)
            assert [x['args'] for x in records if x['args'][0] == 'rmi'] == [
                ['rmi', f'localhost/{agent}-one-folder:latest', f'localhost/{agent}-one-folder:0.0.1-old']]
            assert not (base / f'installed/{agent}-isolated').exists()
            records, _ = run('uninstall.sh', '--purge-state', '--agent', agent)
            assert any(x['args'] == ['volume', 'rm', f'{agent}-state'] for x in records)
            assert not any(x['args'][:2] == ['secret', 'rm'] for x in records)
            run('uninstall.sh', '--agent', agent, extra={'FAKE_CONTAINER': f'{agent}-isolated'}, ok=False)
        records, _ = run('uninstall.sh', '--agent', 'codex',
                         extra={'FAKE_ACTIVE': codex_name, 'FAKE_CONTAINER': codex_name, 'FAKE_RUNNING': 'false'})
        assert any(x['args'] == ['rm', codex_name] for x in records)
        records, _ = run('install.sh')
        assert len([x for x in records if x['args'][0] == 'build']) == 2
        records, _ = run('uninstall.sh')
        assert not any(x['args'][:2] in (['volume', 'rm'], ['secret', 'rm']) for x in records)
        # Different selected tags may share one image repository; remove it once.
        records, _ = run('uninstall.sh', extra={
            'CODEX_ISOLATED_IMAGE': 'localhost/codex-one-folder:codex',
            'CLAUDE_ISOLATED_IMAGE': 'localhost/codex-one-folder:claude'})
        assert [x['args'] for x in records if x['args'][0] == 'rmi'] == [
            ['rmi', 'localhost/codex-one-folder:latest', 'localhost/codex-one-folder:0.0.1-old']]
        launch('claude', extra={'CLAUDE_ISOLATED_STATE_VOLUME': 'codex-state'}, ok=False)
        run('install.sh', '--agent', 'claude', extra={'CLAUDE_ISOLATED_STATE_VOLUME': 'codex-state'}, ok=False)
        run('install.sh', '--agent', 'bogus', ok=False)
        run('uninstall.sh', '--agent', 'bogus', ok=False)
    print('Agent launcher, workspace locking, verification, and lifecycle tests passed.')


if __name__ == '__main__':
    main()
