import unittest
from unittest.mock import patch

import notify


class NotificationPolicyTests(unittest.TestCase):
    def test_operational_failures_and_text_conflicts_never_send_email(self):
        for status in ('conflict', 'review_required', 'validation_failed', 'publication_failed'):
            with self.subTest(status=status):
                report = {'status': status, 'tag': 'rust-v1.1.0', 'base_head': 'a'}
                with patch.object(notify, 'api', side_effect=[[], {'html_url': 'https://example.test/issue'}]) as api, patch.object(notify, 'send_mail') as send:
                    notify.notify(report, 'OceanCT/codex')
                    self.assertEqual(api.call_count, 2)
                    send.assert_not_called()

    def test_bare_functional_conflict_label_is_insufficient(self):
        self.assertFalse(notify.needs_feature_decision({'status': 'functional_conflict'}))
        report = {'status': 'functional_conflict', 'functional_conflict': {
            'feature': 'recovery', 'upstream_change': 'delete',
            'incompatibility': 'retention prohibited',
            'attempted_resolution': 'separate retention path', 'evidence': []}}
        self.assertFalse(notify.needs_feature_decision(report))
        report['functional_conflict']['evidence'] = ['API contract and regression reproduction']
        self.assertTrue(notify.needs_feature_decision(report))


if __name__ == '__main__':
    unittest.main()
