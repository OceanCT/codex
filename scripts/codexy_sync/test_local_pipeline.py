import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import local_pipeline as pipeline
import sync


class LocalPipelineTests(unittest.TestCase):
    def test_disk_gate_has_no_network_or_build_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); state=home/'state'
            with patch.object(pipeline,'resources',return_value={'status':'waiting_disk','free_gib':12,'required_gib':20}),patch.object(pipeline,'git') as git,patch.object(pipeline,'validate') as validate:
                first=pipeline.advance(state,home=home)
                self.assertEqual(first['status'],'waiting_disk')
                self.assertEqual(pipeline.advance(state,home=home),first)
                git.assert_not_called(); validate.assert_not_called()

    def test_local_validation_failure_cannot_install_or_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); state=home/'state'; state.mkdir()
            repo=home/'local-candidate'; repo.mkdir()
            (home/'codex/.git').mkdir(parents=True)
            pipeline.save(state/'local-pipeline.json',{'execution':'local-only','status':'prepared','sha':'a'*40,'tag':'rust-v0.165.0'})
            pipeline.save(state/'local-report.json',{'status':'ready','candidate_sha':'a'*40,'base_head':'b'*40,'tag':'rust-v0.165.0'})
            with patch.object(pipeline,'resources',return_value=None),patch.object(pipeline,'validate',side_effect=RuntimeError('local compiler failed')) as validate,patch.object(pipeline,'publish') as publish,patch.object(pipeline,'install') as install:
                result=pipeline.advance(state,home=home)
                self.assertEqual(result['status'],'validation_failed')
                self.assertEqual(pipeline.advance(state,home=home),result)
                self.assertEqual(validate.call_count,1)
                publish.assert_not_called(); install.assert_not_called()

    def test_local_success_orders_validation_publication_installation(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); state=home/'state'; state.mkdir()
            (home/'codex/.git').mkdir(parents=True)
            pipeline.save(state/'local-pipeline.json',{'execution':'local-only','status':'prepared','sha':'a'*40,'tag':'rust-v0.165.0'})
            pipeline.save(state/'local-report.json',{'status':'ready','candidate_sha':'a'*40,'base_head':'b'*40,'tag':'rust-v0.165.0'})
            calls=[]
            with patch.object(pipeline,'resources',return_value=None),patch.object(pipeline,'validate',side_effect=lambda *a:calls.append('validate')),patch.object(pipeline,'git',return_value=subprocess.CompletedProcess([],0,'b'*40+' refs/heads/test')),patch.object(pipeline,'publish',side_effect=lambda *a:calls.append('publish')),patch.object(pipeline,'package',side_effect=lambda *a,**kw:calls.append('package')),patch.object(pipeline,'install',side_effect=lambda *a:calls.append('install')):
                result=pipeline.advance(state,home=home)
            self.assertEqual(calls,['validate','publish','package','install'])
            self.assertEqual(result['status'],'installed')
            self.assertTrue(result['installed'])

    def test_installed_watcher_routes_old_dispatch_flag_to_local_executor(self):
        import local_watch
        with tempfile.TemporaryDirectory() as temp,patch.object(sys,'argv',['local_watch.py','--state-dir',temp,'--dispatch']),patch.object(pipeline,'run_recorded',return_value={'status':'waiting_disk','execution':'local-only'}) as run:
            local_watch.main()
            run.assert_called_once_with(Path(temp))


class LocalOnlyPolicyTests(unittest.TestCase):
    def test_actions_mutations_are_blocked_before_network_execution(self):
        with patch.object(sync,'run') as run:
            with self.assertRaisesRegex(RuntimeError,'local-only'):
                sync.api('repos/OceanCT/codex/actions/workflows/example.yml/dispatches','POST',{'ref':'main'})
            run.assert_not_called()
