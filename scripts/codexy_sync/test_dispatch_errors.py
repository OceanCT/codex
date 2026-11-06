import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import local_watch
import sync


class DispatchErrors(unittest.TestCase):
    def test_rejection_keeps_reason_and_retries_after_backoff(self):
        import base64
        published = {'content': base64.b64encode(b'{"upstream_tag":"rust-v1.0.0"}').decode()}
        with tempfile.TemporaryDirectory() as temp, patch.object(local_watch.preflight, 'check', return_value={'proceed': 'true', 'tag': 'rust-v1.1.0'}), patch.object(local_watch, 'api', side_effect=[sync.ApiError(422, 'Actions has been disabled for this repository.'), None]) as api:
            state = Path(temp)
            first = local_watch.check_once(state, dispatch=True, now=100)
            self.assertEqual(first['status'], 'dispatch_rejected')
            self.assertIn('disabled', first['reason'])
            self.assertEqual(local_watch.check_once(state, dispatch=True, now=101), first)
            self.assertEqual(local_watch.check_once(state, dispatch=True, now=21700)['status'], 'dispatched')
            self.assertEqual(sum(len(c.args) > 1 and c.args[1] == 'POST' for c in api.call_args_list), 2)

    def test_api_preserves_http_rejection_without_request_body(self):
        failure = subprocess.CompletedProcess([], 1, '{"message":"Actions disabled"}', 'gh: Actions disabled (HTTP 422)')
        with patch.object(sync, 'run', return_value=failure):
            with self.assertRaises(sync.ApiError) as caught:
                sync.api('repos/example/dispatches', 'POST', {'secret': 'never-print'})
            self.assertEqual(caught.exception.status, 422)
            self.assertIn('Actions disabled', str(caught.exception))
            self.assertNotIn('never-print', str(caught.exception))
