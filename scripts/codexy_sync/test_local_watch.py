import fcntl
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import local_watch
import local_pipeline


class LocalWatchTests(unittest.TestCase):
    def test_preview_does_not_run_build_or_access_github(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(local_pipeline, 'git') as git, patch.object(local_pipeline, 'validate') as validate:
            result = local_watch.check_once(Path(temp))
            self.assertEqual(result['status'], 'preview')
            git.assert_not_called(); validate.assert_not_called()

    def test_concurrent_watchers_do_not_start_second_local_pipeline(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)
            with (state/'local-pipeline.lock').open('a') as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                self.assertEqual(local_watch.check_once(state,dispatch=True)['status'],'busy')
