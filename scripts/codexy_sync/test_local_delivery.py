import hashlib
from contextlib import closing
import json
import os
from pathlib import Path
import platform
import signal
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import local_delivery as delivery
import memory_transition as memory
from package_runtime import FILES


class DeliveryTests(unittest.TestCase):
    def package(self, root, *, bad_host=False):
        package = root / 'package'
        package.mkdir()
        code = '#!' + sys.executable + '\nprint("codex-cli 0.165.0")\n'
        (package / 'codex').write_text(code)
        (package / 'codex').chmod(0o755)
        (package / 'codex-code-mode-host').write_text(code)
        (package / 'codex-code-mode-host').chmod(0o755)
        (package / 'render_codex_y_latex.py').write_text('# renderer fixture\n')
        (package / 'smoke_codex_y_host.py').write_text('raise SystemExit(' + ('1' if bad_host else '0') + ')\n')
        manifest = dict(source_sha='a' * 40, upstream_tag='rust-v0.165.0', system=platform.system(), machine=platform.machine(), files={name: hashlib.sha256((package/name).read_bytes()).hexdigest() for name in FILES})
        (package / 'manifest.json').write_text(json.dumps(manifest))
        return package

    def test_real_staging_and_atomic_selection_preserve_previous_runtime(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            old = home/'old/bin'; old.mkdir(parents=True)
            (old/'codex').write_text('old runtime')
            selected = home/'.codex-y/packages/app-server-daemon/current'
            selected.parent.mkdir(parents=True); selected.symlink_to(old.parent)
            previous = delivery.install(self.package(home), home, 'a'*40, 'rust-v0.165.0')
            self.assertEqual(previous, str(old.parent.resolve()))
            self.assertEqual((old/'codex').read_text(), 'old runtime')
            self.assertIn('auto-'+'a'*40, str(selected.resolve()))
            self.assertEqual(subprocess.check_output([str(selected/'bin/codex'), '--version'], text=True).strip(), 'codex-cli 0.165.0')

    def test_failed_host_probe_does_not_select_a_package(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with self.assertRaises(subprocess.CalledProcessError):
                delivery.install(self.package(home, bad_host=True), home, 'a'*40, 'rust-v0.165.0')
            self.assertFalse((home/'.codex-y/packages/app-server-daemon/current').exists())

    def test_corrupt_or_wrong_source_package_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            package = self.package(Path(temp))
            with self.assertRaises(ValueError):
                delivery.verify(package, 'b'*40, 'rust-v0.165.0')
            (package/'codex').write_text('corruption')
            with self.assertRaisesRegex(ValueError, 'checksum'):
                delivery.verify(package, 'a'*40, 'rust-v0.165.0')

    def test_only_graceful_signal_once_to_matching_process(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            pid_file = home/'.codex-y/app-server-daemon/daemon.pid'
            pid_file.parent.mkdir(parents=True)
            pid_file.write_text(json.dumps({'pid': 42, 'processStartTime': 'Fri Nov  6 14:15:01 2026'}))
            state = {'tag': 'rust-v0.165.0'}
            info = subprocess.CompletedProcess([], 0, json.dumps({'status':'running','appServerVersion':'0.159.2'}))
            ps = subprocess.CompletedProcess([], 0, f'Fri Nov  6 14:15:01 2026 {home}/.local/share/codex-y/old/bin/codex app-server --listen unix:// --managed-daemon\n')
            with patch.object(delivery.subprocess, 'run', side_effect=[info, ps, info, ps]), patch.object(delivery.os, 'kill') as kill:
                self.assertEqual(delivery.runtime_status(home, state)['status'], 'draining')
                self.assertEqual(delivery.runtime_status(home, state)['status'], 'draining')
                kill.assert_called_once_with(42, signal.SIGHUP)

    def test_pid_reuse_never_signals_other_process(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); path=home/'.codex-y/app-server-daemon/daemon.pid'
            path.parent.mkdir(parents=True); path.write_text(json.dumps({'pid':42,'processStartTime':'old'}))
            results=[subprocess.CompletedProcess([],0,'{"status":"running","appServerVersion":"0.159.2"}'),subprocess.CompletedProcess([],0,'new /unrelated/process')]
            with patch.object(delivery.subprocess,'run',side_effect=results), patch.object(delivery.os,'kill') as kill:
                self.assertEqual(delivery.runtime_status(home,{'tag':'rust-v0.165.0'})['status'],'restart_blocked')
                kill.assert_not_called()


class MemoryTests(unittest.TestCase):
    def test_existing_store_warms_then_switches_without_modifying_memories(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp); root=home/'.codex-y'; root.mkdir()
            state=home/'state'; state.mkdir()
            (root/'memories').mkdir(); note=root/'memories/MEMORY.md'; note.write_text('keep forever')
            config=root/'config.toml'; config.write_text('model = "example"\n[features]\nmemory_tool = true\n')
            self.assertEqual(memory.advance_memory(home,state,'rust-v0.165.0')['status'],'warming_v2')
            self.assertIn('version = "v1"',config.read_text())
            v2=root/'memories_v2'; v2.mkdir()
            (v2/'memory_summary.md').write_text("v1\n## User Profile\n## User preferences\n## General Tips\n## What's in Memory\n")
            with closing(sqlite3.connect(root/'memories_v2_1.sqlite')) as db:
                db.execute('CREATE TABLE consolidation_progress(singleton INTEGER,max_thread_count INTEGER)')
                db.execute('INSERT INTO consolidation_progress VALUES(1,19)')
                db.commit()
            self.assertEqual(memory.advance_memory(home,state,'rust-v0.165.0')['status'],'warming_v2')
            with closing(sqlite3.connect(root/'memories_v2_1.sqlite')) as db:
                db.execute('UPDATE consolidation_progress SET max_thread_count=20')
                db.commit()
            self.assertEqual(memory.advance_memory(home,state,'rust-v0.165.0')['status'],'v2_selected')
            self.assertIn('version = "v2"',config.read_text())
            self.assertEqual(note.read_text(),'keep forever')
            self.assertEqual(len(list(state.glob('config-before-memory-*.toml'))),2)

    def test_opt_out_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); root=home/'.codex-y'; root.mkdir(); (root/'memories').mkdir()
            original='[memories]\ndual_write = false\n'
            (root/'config.toml').write_text(original)
            self.assertEqual(memory.advance_memory(home,home,'rust-v0.165.0')['status'],'opted_out')
            self.assertEqual((root/'config.toml').read_text(),original)

class DeliveryStateTests(unittest.TestCase):
    package = DeliveryTests.package

    def test_dispatch_then_successful_download_installs_matching_source(self):
        import base64
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); state_dir=home/'state'; state_dir.mkdir()
            fixture=self.package(home)
            target={'object':{'sha':'a'*40}}
            pin={'content':base64.b64encode(b'{"upstream_tag":"rust-v0.165.0"}').decode()}
            with patch.object(delivery,'api',side_effect=[target,pin,{'workflow_runs':[]},None]) as api:
                result=delivery.advance(state_dir,home=home,now=100)
                self.assertEqual(result['status'],'dispatched')
                self.assertEqual(api.call_args.args[2]['inputs']['source_sha'],'a'*40)
            run={'id':123,'display_title':f'Codexy package {"a"*40} {platform.machine()}','status':'completed','conclusion':'success','html_url':'https://example.test/run'}
            real_run=subprocess.run
            def execute(args, **kwargs):
                if args[:3]==['gh','run','download']:
                    import shutil
                    shutil.copytree(fixture,Path(args[-1]),dirs_exist_ok=True)
                    return subprocess.CompletedProcess(args,0)
                return real_run(args,**kwargs)
            with patch.object(delivery,'api',side_effect=[target,{'workflow_runs':[run]}]),patch.object(delivery.subprocess,'run',side_effect=execute):
                result=delivery.advance(state_dir,home=home,now=101)
                self.assertTrue(result['installed'])
                self.assertEqual(result['runtime']['status'],'awaiting_activation')
            with patch.object(delivery,'runtime_status',return_value={'status':'active','version':'0.165.0'}),patch.object(delivery,'api',return_value=target):
                result=delivery.advance(state_dir,home=home,now=102)
                self.assertEqual(result['runtime']['status'],'active')
                self.assertEqual(result['next_source_check'],86502)

    def test_failed_activation_restores_previous_package(self):
        with tempfile.TemporaryDirectory() as temp:
            home=Path(temp); state_dir=home/'state'; state_dir.mkdir()
            old=home/'old'; old.mkdir()
            selected=home/'.codex-y/packages/app-server-daemon/current'
            selected.parent.mkdir(parents=True); selected.symlink_to(home/'new')
            delivery.save(state_dir/'delivery.json',dict(installed=True,sha='a'*40,tag='rust-v0.165.0',previous_package=str(old)))
            with patch.object(delivery,'runtime_status',side_effect=ValueError('unhealthy new runtime')):
                result=delivery.advance(state_dir,home=home,now=1)
                self.assertEqual(result['status'],'activation_failed')
                self.assertEqual(selected.resolve(),old.resolve())
            with patch.object(delivery,'api') as api:
                self.assertEqual(delivery.advance(state_dir,home=home,now=2),result)
                api.assert_not_called()
