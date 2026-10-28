"""Validate a manually reconciled candidate through the usual publication gate."""
import argparse
import json
import os
from pathlib import Path
from sync import api, git, save, version


def prepare(repo, base, policy):
    head = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    if git(repo, 'status', '--porcelain').stdout.strip():
        raise ValueError('Reviewed candidate must be clean')
    git(repo, 'merge-base', '--is-ancestor', base, head)
    previous = json.loads(git(repo, 'show', f'{base}:.codexy-upstream.json').stdout)
    state = json.loads((repo / '.codexy-upstream.json').read_text())
    tag = state['upstream_tag']
    if version(tag) <= version(previous['upstream_tag']):
        raise ValueError('Reviewed candidate must advance the upstream version')
    release = api(f'repos/openai/codex/releases/tags/{tag}')
    if release['draft'] or release['prerelease']:
        raise ValueError('Reviewed candidate must use a published stable release')
    git(repo, 'fetch', '--no-tags', 'https://github.com/openai/codex.git', f'refs/tags/{tag}')
    upstream = git(repo, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
    if state['upstream_sha'] != upstream:
        raise ValueError('Reviewed candidate upstream pin does not match release')
    git(repo, 'merge-base', '--is-ancestor', upstream, head)
    for feature in policy['features'].values():
        for path in feature.get('required_files', []):
            if not (repo / path).is_file():
                raise ValueError(f'Reviewed candidate lost required feature file: {path}')
    return {'status': 'ready', 'base_head': base, 'candidate_sha': head,
            'previous': previous['upstream_tag'], 'tag': tag, 'upstream_sha': upstream,
            'release_url': release['html_url'], 'release_notes': release.get('body', ''),
            'reasons': [], 'conflicts': [], 'features_touched': {},
            'changed_files': git(repo, 'diff', '--name-only', base, head).stdout.splitlines()}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    policy = json.loads(Path(__file__).with_name('policy.json').read_text())
    report = prepare(args.repo.resolve(), os.environ['BASE_HEAD'], policy)
    save(report, args.report)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write('status=ready\n')
