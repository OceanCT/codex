import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import reviewed
import sync


class ReviewedTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        sync.git(self.repo, 'init', '-b', 'main')
        sync.git(self.repo, 'config', 'user.name', 'Test')
        sync.git(self.repo, 'config', 'user.email', 'test@example.com')
        (self.repo / '.codexy-upstream.json').write_text(json.dumps({'upstream_tag':'rust-v1.0.0'}))
        self.base = self.commit()
        (self.repo / 'feature.rs').write_text('retained')
        self.upstream = self.commit()
        sync.git(self.repo, 'update-ref', 'refs/test-upstream', self.upstream)
        (self.repo / '.codexy-upstream.json').write_text(json.dumps({'upstream_tag':'rust-v1.1.0','upstream_sha':self.upstream}))
        self.head = self.commit()
        self.policy = {'features':{'feature':{'required_files':['feature.rs']}}}

    def commit(self):
        sync.git(self.repo, 'add', '-A')
        sync.git(self.repo, 'commit', '-m', 'fixture')
        return sync.git(self.repo, 'rev-parse', 'HEAD').stdout.strip()

    def prepare(self):
        def git(repo, *args):
            if args[0] == 'fetch':
                args = ('fetch', str(repo), 'refs/test-upstream')
            return sync.git(repo, *args)
        with patch.object(reviewed, 'git', side_effect=git), patch.object(reviewed, 'api', return_value={'draft':False,'prerelease':False,'html_url':'https://example.test/release'}):
            return reviewed.prepare(self.repo, self.base, self.policy)

    def test_reviewed_candidate_still_requires_validation(self):
        report = self.prepare()
        self.assertEqual((report['status'],report['candidate_sha'],report['base_head']),('ready',self.head,self.base))

    def test_reject_mismatched_upstream_pin(self):
        p=self.repo/'.codexy-upstream.json'
        state=json.loads(p.read_text());state['upstream_sha']=self.base;p.write_text(json.dumps(state));self.commit()
        with self.assertRaisesRegex(ValueError,'pin does not match'):
            self.prepare()

    def test_reject_lost_feature(self):
        (self.repo/'feature.rs').unlink();self.commit()
        with self.assertRaisesRegex(ValueError,'lost required feature'):
            self.prepare()
