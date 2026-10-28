import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import sync
import notify


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name) / 'fork'
        self.repo.mkdir()
        self.git('init', '-b', 'main')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test@example.com')
        self.write('shared.txt', 'original\n')
        self.write('feature.rs', 'protected behavior\n')
        self.commit('upstream baseline')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('tag', 'rust-v1.0.0')
        self.git('checkout', '-b', 'fork')
        self.write('custom.txt', 'fork-only content\n')
        self.write(sync.STATE, json.dumps({'upstream_tag': 'rust-v1.0.0', 'upstream_sha': self.base}))
        self.commit('fork feature')
        self.policy = {'features': {'custom feature': {'paths': ['feature.rs'], 'required_files': ['custom.txt']}}, 'pending_local_features': []}
        self.release = {'tag_name': 'rust-v1.1.0', 'draft': False, 'prerelease': False, 'html_url': 'https://example.test/release', 'body': '## New Features\nAn addition'}

    def git(self, *args):
        return sync.git(self.repo, *args).stdout.strip()

    def write(self, path, content):
        (self.repo / path).write_text(content)

    def commit(self, message):
        self.git('add', '.')
        self.git('commit', '-m', message)

    def upstream(self, path='new.txt', content='new upstream feature\n'):
        self.git('checkout', '-b', 'upstream-release', self.base)
        self.write(path, content)
        self.commit('upstream update')
        self.git('tag', self.release['tag_name'])
        self.git('checkout', 'fork')

    def prepare(self):
        with patch.object(sync, 'UPSTREAM', str(self.repo)), patch.object(sync, 'api', return_value=self.release):
            return sync.prepare(self.repo, self.release['tag_name'], self.policy)

    def test_version_stamp_updates_workspace_packages_only(self):
        (self.repo / 'codex-rs').mkdir()
        self.write('codex-rs/Cargo.toml', '[workspace]\nmembers = ["local"]\n[workspace.package]\nversion = "1.1.0"\n')
        (self.repo / 'codex-rs/local').mkdir()
        self.write('codex-rs/local/Cargo.toml', '[package]\nname = "local"\nversion.workspace = true\n[dev-dependencies]\nhelper = {path = "../helper"}\n')
        (self.repo / 'codex-rs/helper').mkdir()
        self.write('codex-rs/helper/Cargo.toml', '[package]\nname = "helper"\nversion.workspace = true\n')
        self.write('codex-rs/Cargo.lock', 'version = 4\n[[package]]\nname = "helper"\nversion = "1.0.0"\n[[package]]\nname = "local"\nversion = "1.0.0"\n[[package]]\nname = "external"\nversion = "1.0.0"\nsource = "registry+example"\n')
        sync.stamp_version(self.repo, 'rust-v1.1.0')
        lock = (self.repo / 'codex-rs/Cargo.lock').read_text()
        self.assertIn('name = "local"\nversion = "1.1.0"', lock)
        self.assertIn('name = "helper"\nversion = "1.1.0"', lock)
        self.assertIn('name = "external"\nversion = "1.0.0"', lock)

    def test_new_feature_merges_and_preserves_fork(self):
        self.upstream()
        report = self.prepare()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual((self.repo / 'custom.txt').read_text(), 'fork-only content\n')
        self.assertTrue((self.repo / 'new.txt').exists())
        self.assertEqual(json.loads((self.repo / sync.STATE).read_text())['upstream_tag'], 'rust-v1.1.0')
        self.assertEqual(len(self.git('rev-list', '--parents', '-n', '1', 'HEAD').split()), 3)

    def test_independent_module_additions_are_combined(self):
        self.git('checkout', 'main')
        self.write('lib.rs', 'mod first;\nmod last;\n')
        self.commit('base modules')
        self.base = self.git('rev-parse', 'HEAD')
        self.git('checkout', 'fork')
        self.git('merge', 'main', '--no-edit')
        self.write(sync.STATE, json.dumps({'upstream_tag': 'rust-v1.0.0', 'upstream_sha': self.base}))
        self.write('lib.rs', 'mod first;\nmod rich_media;\nmod last;\n')
        self.commit('fork module')
        self.upstream('lib.rs', 'mod first;\nmod security_setup;\nmod last;\n')
        report = self.prepare()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['auto_resolved_module_additions'], ['lib.rs'])
        self.assertIn('mod rich_media;\nmod security_setup;', (self.repo / 'lib.rs').read_text())

    def test_actual_conflict_leaves_fork_untouched(self):
        self.write('shared.txt', 'fork behavior\n')
        self.commit('custom behavior')
        before = self.git('rev-parse', 'HEAD')
        self.upstream('shared.txt', 'upstream behavior\n')
        report = self.prepare()
        self.assertEqual(report['status'], 'conflict')
        self.assertIn('shared.txt', report['conflicts'])
        self.assertEqual(self.git('rev-parse', 'HEAD'), before)
        self.assertEqual(self.git('status', '--porcelain'), '')

    def test_upstream_workflows_do_not_replace_fork_automation(self):
        (self.repo / '.github/workflows').mkdir(parents=True)
        self.write('.github/workflows/build.yml', 'fork build\n')
        self.write('.github/workflows/sync.yml', 'fork controller\n')
        self.commit('fork workflows')
        self.git('checkout', '-b', 'upstream-release', self.base)
        (self.repo / '.github/workflows').mkdir(parents=True, exist_ok=True)
        self.write('.github/workflows/build.yml', 'upstream build\n')
        self.write('.github/workflows/release.yml', 'upstream release\n')
        self.write('new.txt', 'product feature\n')
        self.commit('upstream workflows and product')
        self.git('tag', self.release['tag_name'])
        self.git('checkout', 'fork')
        report = self.prepare()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual((self.repo / '.github/workflows/build.yml').read_text(), 'fork build\n')
        self.assertEqual((self.repo / '.github/workflows/sync.yml').read_text(), 'fork controller\n')
        self.assertFalse((self.repo / '.github/workflows/release.yml').exists())
        self.assertEqual((self.repo / 'new.txt').read_text(), 'product feature\n')

    def test_clean_boundary_change_is_candidate_pending_behavioral_tests(self):
        self.upstream('feature.rs', 'different behavior\n')
        report = self.prepare()
        self.assertEqual(report['status'], 'ready')
        self.assertEqual(report['conflicts'], [])
        self.assertIn('custom feature', report['features_touched'])

    def test_unprotected_bug_fix_is_candidate(self):
        self.upstream('shared.txt', 'bug fixed\n')
        self.release['body'] = '## Bug Fixes\nFix parser'
        self.assertEqual(self.prepare()['status'], 'ready')

    def test_dirty_checkout_is_rejected(self):
        self.write('untracked', 'work in progress')
        with self.assertRaisesRegex(ValueError, 'clean'):
            self.prepare()

    def test_prerelease_rejected(self):
        self.release['prerelease'] = True
        with self.assertRaisesRegex(ValueError, 'stable'):
            self.prepare()

    def test_pending_local_feature_is_protected(self):
        self.policy['pending_local_features'] = ['shared.txt']
        self.policy['pending_local_tokens'] = ['provider']
        self.upstream('shared.txt', 'new provider behavior')
        self.assertEqual(self.prepare()['status'], 'review_required')

    def test_publish_requires_validation_and_rejects_concurrent_writer(self):
        self.upstream()
        original = self.git('rev-parse', 'HEAD')
        report = self.prepare()
        remote = Path(self.temp.name) / 'remote.git'
        sync.run('git', 'init', '--bare', str(remote))
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', 'origin', f'{original}:refs/heads/stable')
        with self.assertRaisesRegex(ValueError, 'validated'):
            sync.publish(self.repo, report, 'stable')
        report['status'] = 'validated'
        self.git('push', 'origin', 'HEAD:refs/heads/stable')
        with self.assertRaisesRegex(ValueError, 'advanced'):
            sync.publish(self.repo, report, 'stable')

    def test_publish_fast_forwards_validated_candidate(self):
        self.upstream()
        original = self.git('rev-parse', 'HEAD')
        report = self.prepare()
        remote = Path(self.temp.name) / 'remote.git'
        sync.run('git', 'init', '--bare', str(remote))
        self.git('remote', 'add', 'origin', str(remote))
        self.git('push', 'origin', f'{original}:refs/heads/stable')
        report['status'] = 'validated'
        sync.publish(self.repo, report, 'stable')
        self.assertEqual(report['status'], 'published')
        self.assertEqual(self.git('ls-remote', 'origin', 'refs/heads/stable').split()[0], report['candidate_sha'])

    def test_release_selection_orders_versions_and_skips_alpha_and_draft(self):
        refs = '\n'.join('a refs/tags/' + tag for tag in ['rust-v1.2.0', 'rust-v1.1.0-alpha.1', 'rust-v1.1.0', 'rust-v1.0.0'])
        responses = [subprocess.CompletedProcess([], 0, refs, ''),
                     subprocess.CompletedProcess([], 0, json.dumps(dict(self.release, draft=True)), ''),
                     subprocess.CompletedProcess([], 0, json.dumps(dict(self.release, tag_name='rust-v1.2.0')), '')]
        with patch.object(sync, 'run', side_effect=responses):
            self.assertEqual(sync.next_release('rust-v1.0.0')['tag_name'], 'rust-v1.2.0')


class MailTests(unittest.TestCase):
    def test_missing_secrets_are_reported_not_claimed_as_sent(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, '邮件未发送'):
                notify.send_mail('subject', 'reason', 'oceanct17@gmail.com')

    def test_tls_mail_has_expected_recipient_and_body(self):
        env = dict(SMTP_HOST='smtp.test', SMTP_PORT='587', SMTP_USERNAME='user', SMTP_PASSWORD='secret', SMTP_FROM='sender@example.com')
        with patch.dict(os.environ, env), patch.object(notify.smtplib, 'SMTP') as smtp:
            notify.send_mail('conflict', 'file and reason', 'oceanct17@gmail.com')
            client = smtp.return_value.__enter__.return_value
            client.starttls.assert_called_once()
            message = client.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'oceanct17@gmail.com')
            self.assertEqual(message.get_content().strip(), 'file and reason')

    def test_notification_deduplicates_after_successful_delivery(self):
        report = {'status': 'conflict', 'tag': 'rust-v1.1.0', 'previous': 'rust-v1.0.0', 'base_head': 'a', 'conflicts': ['shared.txt']}
        body = sync.markdown(report)
        marker = '<!-- codexy-email-sent:' + notify.hashlib.sha256(body.encode()).hexdigest() + ' -->'
        issue = {'title': '[Codexy upstream] rust-v1.1.0: conflict', 'body': body + marker}
        with patch.object(notify, 'api', return_value=[issue]), patch.object(notify, 'send_mail') as send:
            notify.notify(report, 'OceanCT/codex')
            send.assert_not_called()

    def test_failed_delivery_does_not_set_sent_marker(self):
        report = {'status': 'conflict', 'tag': 'rust-v1.1.0', 'previous': 'rust-v1.0.0', 'base_head': 'a', 'conflicts': ['shared.txt']}
        issue = {'number': 1, 'html_url': 'https://example.test/1'}
        with patch.dict(os.environ, {k: 'test' for k in ('SMTP_HOST', 'SMTP_PORT', 'SMTP_USERNAME', 'SMTP_PASSWORD', 'SMTP_FROM')}), patch.object(notify, 'api', side_effect=[[], issue]) as api, patch.object(notify, 'send_mail', side_effect=RuntimeError('SMTP failed')):
            with self.assertRaisesRegex(RuntimeError, 'SMTP failed'):
                notify.notify(report, 'OceanCT/codex')
            self.assertEqual(api.call_count, 2)


if __name__ == '__main__':
    unittest.main()
