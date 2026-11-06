import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import local_watch
import sync


class DispatchErrors(unittest.TestCase):
    def test_api_preserves_http_rejection_without_request_body(self):
        failure = subprocess.CompletedProcess([], 1, '{"message":"Actions disabled"}', 'gh: Actions disabled (HTTP 422)')
        with patch.object(sync, 'run', return_value=failure):
            with self.assertRaises(sync.ApiError) as caught:
                sync.api('repos/example/dispatches', 'POST', {'secret': 'never-print'})
            self.assertEqual(caught.exception.status, 422)
            self.assertIn('Actions disabled', str(caught.exception))
            self.assertNotIn('never-print', str(caught.exception))
