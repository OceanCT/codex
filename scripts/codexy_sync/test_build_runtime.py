from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_runtime


class NativeComponents(unittest.TestCase):
    def test_older_source_builds_pair_and_newer_source_also_builds_executor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for directory, name in (('cli','codex-cli'),('exec-server','codex-exec-server')):
                (root/directory/'src').mkdir(parents=True)
                (root/directory/'Cargo.toml').write_text('[package]\nname = "'+name+'"\n')
            with patch.object(build_runtime.subprocess,'run') as run:
                build_runtime.build(root)
                self.assertNotIn('codex-exec-server',run.call_args.args[0])
                (root/'exec-server/src/main.rs').write_text('fn main() {}')
                build_runtime.build(root)
                self.assertEqual(run.call_args.args[0][-4:],['-p','codex-exec-server','--bin','codex-exec-server'])
