import gzip
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import execution_history as history
import local_watch


class ExecutionHistoryTests(unittest.TestCase):
    def test_process_exit_without_finish_is_visible_as_interrupted(self):
        with tempfile.TemporaryDirectory() as temp:
            script = 'from pathlib import Path; import execution_history as h; import sys; h.start(Path(sys.argv[1]), "interrupted-task")'
            subprocess.run([sys.executable,'-c',script,temp],cwd=Path(__file__).parent,check=True)
            self.assertEqual(history.rows(Path(temp))[0]['status'],'interrupted')

    def test_real_cli_reads_compressed_success_and_failure_history(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root/'state'
            with patch.object(local_watch,'check_once',return_value={'status':'idle','reason':'No pending release'}):
                first, _, failed = local_watch.record_check(state,dispatch=True)
                self.assertFalse(failed)
            with patch.object(local_watch,'check_once',side_effect=TimeoutError('network test failure')):
                second, _, failed = local_watch.record_check(state,dispatch=True)
                self.assertTrue(failed)
            cli = Path(__file__).with_name('daemon_cli.py')
            result = subprocess.run([sys.executable,str(cli),'--root',str(root),'show',str(second)],capture_output=True,text=True,check=True)
            value = json.loads(result.stdout)
            self.assertEqual(value['status'],'error')
            self.assertIn('network test failure',value['detail']['stderr'])
            result = subprocess.run([sys.executable,str(cli),'--root',str(root),'history','--task','release-check'],capture_output=True,text=True,check=True)
            self.assertIn('idle',result.stdout)
            self.assertIn('error',result.stdout)
            with history.database(state) as db:
                payload = db.execute('SELECT payload FROM executions WHERE id=?',(first,)).fetchone()[0]
            self.assertEqual(payload[:2],b'\x1f\x8b')
            self.assertEqual(json.loads(gzip.decompress(payload))['result']['status'],'idle')
            self.assertEqual(history.rows(state,latest=True)[0]['id'],second)

    def test_running_execution_visible_before_completion(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)
            execution=history.start(state,'release:rust-v1.0.0')
            self.assertEqual(history.rows(state)[0]['status'],'running')
            history.finish(state,execution,{'status':'completed','tag':'rust-v1.0.0'})
            self.assertEqual(history.rows(state)[0]['status'],'completed')

    def test_legacy_import_is_lossless_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); state=root/'state'; logs=root/'logs'
            state.mkdir(); logs.mkdir()
            text='原始记录\n'*200
            (logs/'stdout.log').write_text(text)
            history.import_legacy(state,logs)
            history.import_legacy(state,logs)
            records=history.rows(state)
            self.assertEqual(len(records),1)
            self.assertEqual(history.detail(state,records[0]['id'])['detail']['text'],text)
            self.assertLess(records[0]['stored_bytes'],records[0]['raw_bytes'])
            self.assertEqual((logs/'stdout.log').read_text(),'')
