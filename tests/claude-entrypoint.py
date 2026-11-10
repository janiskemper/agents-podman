#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='claude-entrypoint-') as tmp:
    base = Path(tmp)
    (base / 'bin').mkdir()
    (base / 'home').mkdir()
    fake = base / 'bin/claude'
    fake.write_text('''#!/usr/bin/env python3
import json, os, subprocess, sys
state = os.environ['CLAUDE_CONFIG_DIR']
mask = os.umask(0)
print(json.dumps({'argv': sys.argv[1:], 'home': os.environ['HOME'], 'umask': mask,
                  'config': state, 'updates': os.environ['DISABLE_UPDATES'],
                  'auto': os.environ['DISABLE_AUTOUPDATER'],
                  'helper': subprocess.run(['git', 'config', '--get-all', 'credential.https://github.com.helper'], text=True, capture_output=True).stdout}))
''')
    fake.chmod(0o755)
    state = base / 'state'
    env = {'PATH': str(base / 'bin') + ':' + os.environ['PATH'], 'HOME': str(base / 'home'),
           'CLAUDE_CONFIG_DIR': str(state)}
    def run(**extra):
        # Claude must inherit the caller's umask; only seeded state is private.
        result = subprocess.run(['sh', '-c', 'umask 022; exec sh "$0" "$@"',
                                 str(ROOT / 'images/claude/entrypoint.sh'), 'a prompt with spaces'],
                                env=dict(env, **extra), text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    output = run()
    assert output['argv'] == ['a prompt with spaces'] and output['home'] == str(base / 'home')
    assert output['config'] == str(state) and output['updates'] == output['auto'] == '1'
    assert output['umask'] == 0o022
    assert not output['helper']
    settings = state / 'settings.json'
    assert settings.stat().st_mode & 0o777 == 0o600
    settings.write_text('{"user-setting":"preserved"}\n')
    output = run(GH_TOKEN='fake-test-token', DISABLE_UPDATES='0')
    assert settings.read_text() == '{"user-setting":"preserved"}\n'
    assert output['helper'] == '\n!/usr/bin/gh auth git-credential\n'
    assert not (base / 'home/.gitconfig').exists()
    assert 'fake-test-token' not in json.dumps(output)
    # Exercise actual Git credential-helper execution, without network requests.
    if Path('/usr/bin/gh').exists():
        fake.write_text('''#!/bin/sh
printf 'protocol=https\\nhost=github.com\\n\\n' | GIT_TERMINAL_PROMPT=0 git credential fill
''')
        result = subprocess.run(['sh', str(ROOT / 'images/claude/entrypoint.sh')],
                                env=dict(env, GH_TOKEN='fake-test-token'), text=True, capture_output=True)
        assert result.returncode == 0, result.stderr
        assert 'password=fake-test-token' in result.stdout
    else:
        print('SKIP: actual GitHub credential helper execution requires /usr/bin/gh')
print('Claude entrypoint defaults, state preservation, and credential tests passed.')
