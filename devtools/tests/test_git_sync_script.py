import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'git-sync.sh'


class GitSyncScriptTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='agentscient git sync ')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        home = self.root / 'home'
        home.mkdir()
        self.environment = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
        self.environment['HOME'] = str(home)
        self.environment['XDG_CONFIG_HOME'] = str(home / '.config')
        self.remote = self.root / 'remote.git'
        self.seed = self.root / 'seed'
        self.client = self.root / 'client with spaces'
        self.git(self.root, 'init', '--bare', '--initial-branch=main', str(self.remote))
        self.git(self.root, 'init', '--initial-branch=main', str(self.seed))
        self.identity(self.seed)
        self.commit(self.seed, 'baseline')
        self.git(self.seed, 'remote', 'add', 'origin', str(self.remote))
        self.git(self.seed, 'push', '--set-upstream', 'origin', 'main')
        self.git(self.root, 'clone', str(self.remote), str(self.client))
        self.identity(self.client)

    def git(self, repo, *arguments):
        return subprocess.run(
            ['git', '-C', str(repo), *arguments],
            env=self.environment, capture_output=True, text=True, check=True, timeout=10,
        ).stdout.strip()

    def identity(self, repo):
        self.git(repo, 'config', 'user.name', 'Fixture')
        self.git(repo, 'config', 'user.email', 'fixture@example.invalid')
        self.git(repo, 'config', 'commit.gpgsign', 'false')

    def commit(self, repo, name):
        (repo / name).write_text(name)
        self.git(repo, 'add', name)
        self.git(repo, 'commit', '-m', name)
        return self.git(repo, 'rev-parse', 'HEAD')

    def invoke(self, *arguments, environment=None, script=SCRIPT, explicit_repo=True):
        command = ['bash', str(script)]
        if explicit_repo:
            command.extend(['--repo', str(self.client)])
        return subprocess.run(
            [*command, *arguments], cwd=self.root, env=environment or self.environment,
            capture_output=True, text=True, timeout=10,
        )

    def advance_remote(self):
        revision = self.commit(self.seed, 'remote-change')
        self.git(self.seed, 'push')
        return revision

    def test_preview_does_not_fetch_and_apply_fast_forwards(self):
        old = self.git(self.client, 'rev-parse', 'HEAD')
        new = self.advance_remote()
        preview = self.invoke()
        self.assertEqual(0, preview.returncode, preview.stderr)
        self.assertIn('cached remote state', preview.stdout)
        self.assertEqual(old, self.git(self.client, 'rev-parse', 'origin/main'))
        applied = self.invoke('--apply')
        self.assertEqual(0, applied.returncode, applied.stderr)
        self.assertEqual(new, self.git(self.client, 'rev-parse', 'HEAD'))
        self.assertEqual('', self.git(self.client, 'status', '--porcelain'))

    def test_ahead_commits_only_publish_with_explicit_push(self):
        old = self.git(self.remote, 'rev-parse', 'main')
        new = self.commit(self.client, 'local-change')
        applied = self.invoke('--apply')
        self.assertEqual(0, applied.returncode, applied.stderr)
        self.assertIn('remain local', applied.stdout)
        self.assertEqual(old, self.git(self.remote, 'rev-parse', 'main'))
        pushed = self.invoke('--apply', '--push')
        self.assertEqual(0, pushed.returncode, pushed.stderr)
        self.assertEqual(new, self.git(self.remote, 'rev-parse', 'main'))

    def test_dirty_checkout_blocks_fetch_without_stashing(self):
        old = self.git(self.client, 'rev-parse', 'origin/main')
        self.advance_remote()
        (self.client / 'precious').write_text('keep me')
        result = self.invoke('--apply')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('local changes or untracked files', result.stderr)
        self.assertEqual(old, self.git(self.client, 'rev-parse', 'origin/main'))
        self.assertEqual('keep me', (self.client / 'precious').read_text())
        self.assertEqual('', self.git(self.client, 'stash', 'list'))

    def test_divergence_preserves_local_and_remote_commits(self):
        local = self.commit(self.client, 'local-change')
        remote = self.advance_remote()
        result = self.invoke('--apply', '--push')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('diverged', result.stderr)
        self.assertEqual(local, self.git(self.client, 'rev-parse', 'HEAD'))
        self.assertEqual(remote, self.git(self.remote, 'rev-parse', 'main'))
        self.assertEqual('', self.git(self.client, 'status', '--porcelain'))

    def test_source_guard_preserves_errexit_and_parent_shell(self):
        result = subprocess.run(
            ['bash', '-c', 'set -e; source "$1"; [[ $- == *e* ]]; printf survived', 'test', str(SCRIPT)],
            env=self.environment, capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('survived', result.stdout)

    def test_default_repo_comes_from_script_location_not_cwd(self):
        copied = self.client / 'devtools' / 'scripts' / SCRIPT.name
        copied.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, copied)
        self.git(self.client, 'add', '.')
        self.git(self.client, 'commit', '-m', 'Install helper')
        result = self.invoke(script=copied, explicit_repo=False)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f'Repository: {self.client}', result.stdout)
        self.assertIn('Local commits ahead: 1', result.stdout)

    def test_default_repo_ignores_cdpath_when_called_by_relative_path(self):
        copied = self.client / 'devtools' / 'scripts' / SCRIPT.name
        copied.parent.mkdir(parents=True)
        shutil.copyfile(SCRIPT, copied)
        self.git(self.client, 'add', '.')
        self.git(self.client, 'commit', '-m', 'Install helper')
        result = subprocess.run(
            ['bash', 'devtools/scripts/git-sync.sh'], cwd=self.client,
            env=dict(self.environment, CDPATH='.'), capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f'Repository: {self.client}', result.stdout)
        self.assertIn('Preview only', result.stdout)
        relative = self.invoke('--repo', self.client.name, environment=dict(self.environment, CDPATH='.'),
                               explicit_repo=False)
        self.assertEqual(0, relative.returncode, relative.stderr)
        self.assertIn(f'Repository: {self.client}', relative.stdout)

    def test_git_environment_cannot_redirect_checkout_or_config(self):
        remote = self.advance_remote()
        environment = dict(self.environment, GIT_DIR=str(self.seed / '.git'), GIT_WORK_TREE=str(self.seed))
        environment.update(GIT_CONFIG_COUNT='1', GIT_CONFIG_KEY_0='remote.origin.url', GIT_CONFIG_VALUE_0='/missing')
        result = self.invoke('--apply', environment=environment)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(remote, self.git(self.client, 'rev-parse', 'HEAD'))

    def test_credential_bearing_remote_urls_are_refused_without_echoing(self):
        sentinel = 'secret-sentinel-do-not-print'
        for key, arguments in [('url', []), ('pushurl', ['--apply', '--push'])]:
            with self.subTest(key=key):
                self.git(self.client, 'config', f'remote.origin.{key}', f'https://{sentinel}@example.invalid/repo')
                result = self.invoke(*arguments)
                self.assertNotEqual(0, result.returncode)
                self.assertIn('Remote URL contains', result.stderr)
                self.assertNotIn(sentinel, result.stdout + result.stderr)
                if key == 'url':
                    self.git(self.client, 'config', 'remote.origin.url', str(self.remote))

    def test_standard_ssh_urls_are_accepted_without_connecting_in_preview(self):
        for url in ['ssh://git@github.com/owner/repo.git', 'git@github.com:owner/repo.git']:
            with self.subTest(url=url):
                self.git(self.client, 'config', 'remote.origin.url', url)
                result = self.invoke()
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn('Preview only', result.stdout)
                self.assertNotIn(url, result.stdout + result.stderr)

    def test_invalid_options_and_detached_head_are_refused(self):
        for arguments in [('--push',), ('--repo',), ('--unknown',)]:
            with self.subTest(arguments=arguments):
                self.assertNotEqual(0, self.invoke(*arguments).returncode)
        old = self.git(self.client, 'rev-parse', 'HEAD')
        self.git(self.client, 'checkout', '--detach')
        result = self.invoke('--apply')
        self.assertNotEqual(0, result.returncode)
        self.assertIn('Detached HEAD', result.stderr)
        self.assertEqual(old, self.git(self.client, 'rev-parse', 'HEAD'))
