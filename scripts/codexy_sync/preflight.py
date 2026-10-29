"""Cheap release/attempt check before downloading the candidate history."""
import base64
import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import quote
from sync import api, next_release, STATE


def attempt_key(head, release):
    digest = hashlib.sha256()
    digest.update(json.dumps([head, release['tag_name'], release.get('updated_at')]).encode())
    root = Path(__file__).parent
    for name in ('policy.json', 'sync.py', 'validate.sh', 'setup_v8.py', 'preflight.py'):
        digest.update((root / name).read_bytes())
    digest.update((root.parent.parent / '.github/workflows/codexy-upstream.yml').read_bytes())
    return digest.hexdigest()


def marker(key):
    return f'<!-- codexy-attempt:{key} -->'


def check(repository, retry=False):
    policy = json.loads(Path(__file__).with_name('policy.json').read_text())
    head = api(f"repos/{repository}/git/ref/heads/{policy['target_branch']}")['object']['sha']
    candidate = os.environ.get('REVIEWED_CANDIDATE', '')
    if candidate:
        if not re.fullmatch(r'[0-9a-f]{40}', candidate):
            raise ValueError('Reviewed candidate must be an exact full commit SHA')
        api(f'repos/{repository}/commits/{candidate}')
        return {'proceed': 'true', 'head': candidate, 'base': head,
                'reason': 'Explicitly reviewed candidate; full validation required before publication.'}
    state = api(f'repos/{repository}/contents/{STATE}?ref={quote(head)}')
    current = json.loads(base64.b64decode(state['content']))['upstream_tag']
    release = next_release(current)
    if release is None:
        return {'proceed': 'false', 'reason': 'No pending stable release.'}
    requested = os.environ.get('REQUESTED_RELEASE_TAG', '')
    if requested and requested != release['tag_name']:
        return {'proceed': 'false', 'reason': 'Stale release dispatch; next pending release has changed.'}
    key = attempt_key(head, release)
    result = {'proceed': 'true', 'key': key, 'head': head, 'tag': release['tag_name'], 'reason': 'New attempt or explicit manual retry.'}
    if retry:
        return result
    for page in range(1, 101):
        issues = api(f'repos/{repository}/issues?state=all&per_page=100&page={page}')
        for issue in issues:
            if 'pull_request' not in issue and marker(key) in (issue.get('body') or ''):
                result.update(proceed='false', reason=f"Unchanged blocked attempt; no build or integration. See {issue['html_url']}. Email may still be pending; configure SMTP and manually retry.")
                return result
        if len(issues) < 100:
            return result
    raise RuntimeError('Issue scan limit exceeded')


if __name__ == '__main__':
    result = check(os.environ['GITHUB_REPOSITORY'], os.environ.get('RETRY') == 'true')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in result.items():
            if key != 'reason':
                output.write(f'{key}={value}\n')
    with open(os.environ['GITHUB_STEP_SUMMARY'], 'a') as summary:
        summary.write(result['reason'] + '\n')
    print(result['reason'])
