import gzip
import json
from pathlib import Path
import sqlite3
from contextlib import closing
import subprocess
import sys
import tempfile
import unittest

import execution_history as history


class ProjectsTests(unittest.TestCase):
    def cli(self,root,*args,success=True):
        result=subprocess.run([sys.executable,str(Path(__file__).with_name('daemon_cli.py')),'--root',str(root),*args],capture_output=True,text=True)
        self.assertEqual(result.returncode==0,success,result.stdout+result.stderr)
        return result.stdout+result.stderr

    def test_old_schema_migrates_without_losing_ids_or_compressed_payloads(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)
            payload=gzip.compress(b'{"original":true}')
            with closing(sqlite3.connect(state/'history.sqlite3')) as db:
                db.execute('CREATE TABLE executions (id INTEGER PRIMARY KEY, task TEXT NOT NULL, started REAL NOT NULL, finished REAL, status TEXT NOT NULL, pid INTEGER, payload BLOB, raw_bytes INTEGER, source TEXT UNIQUE)')
                db.execute('INSERT INTO executions VALUES (7,?,?,?, ?,NULL,?,17,NULL)',('legacy',1,2,'imported',payload))
                db.commit()
            with history.database(state) as db:
                row=db.execute('SELECT id,project,payload FROM executions').fetchone()
                self.assertEqual(tuple(row),(7,'codexy-upstream',payload))
            self.assertEqual(history.detail(state,7)['detail'],{'original':True})

    def test_real_project_commands_and_change_links(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp); state=root/'state'
            execution=history.start(state,'release-check')
            history.finish(state,execution,{'status':'completed','tag':'rust-v1.1.0'})
            legacy=history.start(state,'legacy')
            history.finish(state,legacy,{'status':'imported'})
            output=self.cli(root,'projects')
            self.assertIn('codexy-upstream',output)
            self.assertNotIn('legacy',output)
            self.assertIn('Project: codexy-upstream',self.cli(root,'status','codexy-upstream'))
            self.assertIn('completed',self.cli(root,'history','codexy-upstream'))
            self.assertIn('rust-v1.1.0',self.cli(root,'changes','codexy-upstream'))
            result=json.loads(self.cli(root,'logs','codexy-upstream',str(execution)))
            self.assertEqual(result['id'],execution)
            self.assertEqual(history.changes(state)[0]['execution_id'],execution)
            self.assertIn('Unknown project',self.cli(root,'status','missing',success=False))
            with history.database(state) as db:
                db.execute('INSERT INTO projects VALUES (?,?,?,?)',('other','Other','Other project',1))
            self.assertIn('does not belong',self.cli(root,'logs','other',str(execution),success=False))
            self.assertNotIn('completed',self.cli(root,'history','other'))

    def test_repeat_state_observation_does_not_duplicate_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)
            for _ in range(2):
                execution=history.start(state,'release:rust-v1.1.0')
                history.finish(state,execution,{'status':'needs_repair','tag':'rust-v1.1.0'})
            self.assertEqual(len(history.changes(state)),1)
            self.assertEqual(len(history.rows(state)),2)
