import base64
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import local_watch


class LocalWatchTests(unittest.TestCase):
    def test_daily_gate_survives_restarts_without_network(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(local_watch.preflight, 'check', return_value={'proceed':'false','reason':'No pending stable release.'}) as check, patch.object(local_watch, 'api') as api:
            state = Path(temp)
            self.assertEqual(local_watch.check_once(state, dispatch=True, now=100)['status'], 'idle')
            self.assertEqual(local_watch.check_once(state, dispatch=True, now=86499)['status'], 'not_due')
            self.assertEqual(check.call_count, 1)
            self.assertEqual(local_watch.check_once(state, dispatch=True, now=86500)['status'], 'idle')
            self.assertEqual(check.call_count, 2)
            api.assert_not_called()

    def test_failed_check_is_retried_before_24_hours(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(local_watch.preflight, 'check', side_effect=[TimeoutError(), {'proceed':'false','reason':'No release'}]) as check:
            with self.assertRaises(TimeoutError):
                local_watch.check_once(Path(temp), dispatch=True, now=100)
            self.assertEqual(local_watch.check_once(Path(temp), dispatch=True, now=200)['status'], 'idle')
            self.assertEqual(check.call_count, 2)

    def test_unfinished_task_resumes_without_new_discovery_or_dispatch(self):
        state_response = {'content':base64.b64encode(json.dumps({'upstream_tag':'rust-v1.0.0'}).encode()).decode()}
        run = {'id':42,'display_title':'Codexy upstream rust-v1.1.0','created_at':'2026-01-01T00:00:00Z','status':'in_progress','html_url':'https://example.test/run'}
        with tempfile.TemporaryDirectory() as temp, patch.object(local_watch.preflight, 'check', return_value={'proceed':'true','tag':'rust-v1.1.0'}) as check, patch.object(local_watch, 'api', side_effect=[None,state_response,{'workflow_runs':[run]}]) as api:
            state = Path(temp)
            self.assertEqual(local_watch.check_once(state, dispatch=True, now=100)['status'], 'dispatched')
            self.assertEqual(local_watch.check_once(state, dispatch=True, now=101)['status'], 'running')
            self.assertEqual(check.call_count, 1)
            self.assertEqual(sum(len(c.args)>1 and c.args[1]=='POST' for c in api.call_args_list), 1)

    def test_ambiguous_dispatch_is_reconciled_not_repeated(self):
        published = {'content':base64.b64encode(json.dumps({'upstream_tag':'rust-v1.1.0'}).encode()).decode()}
        with tempfile.TemporaryDirectory() as temp, patch.object(local_watch.preflight, 'check', return_value={'proceed':'true','tag':'rust-v1.1.0'}), patch.object(local_watch, 'api', side_effect=[TimeoutError(),published]) as api:
            with self.assertRaises(TimeoutError):
                local_watch.check_once(Path(temp), dispatch=True, now=100)
            self.assertEqual(local_watch.check_once(Path(temp), dispatch=True, now=101)['status'], 'completed')
            self.assertEqual(sum(len(c.args)>1 and c.args[1]=='POST' for c in api.call_args_list), 1)

    def test_preview_does_not_consume_daily_check(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(local_watch.preflight, 'check', return_value={'proceed':'false','reason':'No release'}) as check:
            self.assertEqual(local_watch.check_once(Path(temp), now=100)['status'], 'preview')
            self.assertEqual(local_watch.check_once(Path(temp), dispatch=True, now=101)['status'], 'idle')
            self.assertEqual(check.call_count, 2)

class IdleResourceTests(unittest.TestCase):
    def test_idle_scheduled_process_preserves_state_and_history(self):
        import subprocess
        import sys
        import time
        with tempfile.TemporaryDirectory() as temp:
            state = Path(temp)
            with patch.object(local_watch.preflight, 'check', return_value={'proceed':'false','reason':'No release'}):
                local_watch.check_once(state,dispatch=True)
            before = {p.name:p.read_bytes() for p in state.iterdir() if p.is_file()}
            for _ in range(2):
                subprocess.run([sys.executable,str(Path(local_watch.__file__)),'--state-dir',str(state),'--dispatch','--scheduled'],check=True,capture_output=True)
            after = {p.name:p.read_bytes() for p in state.iterdir() if p.is_file()}
            self.assertEqual(before,after)
            self.assertFalse((state/'history.sqlite3').exists())
            self.assertFalse(local_watch.scheduled_idle(state,now=time.time()+86401))
            import sqlite3
            from contextlib import closing
            with closing(sqlite3.connect(state/'releases.sqlite3')) as db:
                db.execute('INSERT INTO releases VALUES (?,?,?,?,?)',('OceanCT/codex','rust-v1.1.0','running','2026-01-01T00:00:00Z',''))
                db.commit()
            self.assertFalse(local_watch.scheduled_idle(state))
