import base64
import json
import os
import unittest
from unittest.mock import patch
import preflight
import notify


class PreflightTests(unittest.TestCase):
    def check(self, release, issues=(), retry=False, head='a'):
        state = {'content': base64.b64encode(json.dumps({'upstream_tag': 'rust-v1.0.0'}).encode()).decode()}
        with patch.object(preflight, 'api', side_effect=[{'object': {'sha': head}}, state, list(issues)]) as api, patch.object(preflight, 'next_release', return_value=release):
            result = preflight.check('OceanCT/codex', retry)
            return result, api.call_count

    def test_no_release_does_not_scan_issues(self):
        result, calls = self.check(None)
        self.assertEqual(result['proceed'], 'false')
        self.assertEqual(calls, 2)

    def test_identical_blocked_attempt_skips_even_closed_issue(self):
        release = {'tag_name': 'rust-v1.1.0', 'updated_at': 'today'}
        issue = {'state': 'closed', 'body': preflight.marker(preflight.attempt_key('a', release)), 'html_url': 'https://example.test/1'}
        result, _ = self.check(release, [issue])
        self.assertEqual(result['proceed'], 'false')
        self.assertEqual(self.check(release, [issue], head='b')[0]['proceed'], 'true')
        self.assertEqual(self.check(release, [issue], retry=True)[0]['proceed'], 'true')
        self.assertEqual(self.check(dict(release, updated_at='tomorrow'), [issue])[0]['proceed'], 'true')

    def test_missing_smtp_records_attempt_without_sent_marker(self):
        report = {'status': 'review_required', 'tag': 'rust-v1.1.0', 'previous': 'rust-v1.0.0', 'base_head': 'a'}
        with patch('builtins.print'), patch.dict(os.environ, {'CODEXY_ATTEMPT_KEY': 'test-key'}, clear=True), patch.object(notify, 'api', side_effect=[[], {'html_url': 'https://example.test/1'}]) as api, patch.object(notify, 'send_mail') as mail:
            notify.notify(report, 'OceanCT/codex')
            body = api.call_args.args[2]['body']
            self.assertIn(preflight.marker('test-key'), body)
            self.assertNotIn('codexy-email-sent:', body)
            mail.assert_not_called()
