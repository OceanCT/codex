"""Release-by-release upstream integration; standard library only, Python >=3.11."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import tomllib

STATE = '.codexy-upstream.json'
TAG = re.compile(r'rust-v(\d+)\.(\d+)\.(\d+)$')
UPSTREAM = 'https://github.com/openai/codex.git'


def run(*args, cwd=None, check=True, input=None):
    return subprocess.run(args, cwd=cwd, text=True, input=input, capture_output=True, check=check)


def git(repo, *args, check=True, input=None):
    return run('git', *args, cwd=repo, check=check, input=input)


def api(path, method='GET', body=None):
    args = ['gh', 'api', path, '--method', method]
    if body is not None:
        args += ['--input', '-']
    value = run(*args, input=json.dumps(body) if body is not None else None).stdout
    return json.loads(value) if value.strip() else None


def version(tag):
    match = TAG.fullmatch(tag)
    if not match:
        raise ValueError(f'Not a stable Codex release tag: {tag}')
    return tuple(map(int, match.groups()))


def next_release(current):
    """Use tags for ordering, then verify publication; ignore alpha/draft releases."""
    refs = run('git', 'ls-remote', '--tags', '--refs', UPSTREAM, 'rust-v*').stdout
    tags = {line.split()[1].removeprefix('refs/tags/') for line in refs.splitlines()}
    for tag in sorted((t for t in tags if TAG.fullmatch(t) and version(t) > version(current)), key=version):
        result = run('gh', 'api', f'repos/openai/codex/releases/tags/{tag}', check=False)
        if result.returncode:
            if 'HTTP 404' in result.stderr:
                continue
            raise RuntimeError(result.stderr)
        release = json.loads(result.stdout)
        if not release['draft'] and not release['prerelease']:
            return release
    return None


def changed(repo, before, after):
    return git(repo, 'diff', '--name-only', '--no-renames', before, after).stdout.splitlines()


def preserve_fork_workflows(repo, tree, head):
    """Keep fork-owned Actions definitions outside upstream product updates."""
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / 'index'))
        def index(*args, input=None):
            return subprocess.run(['git', *args], cwd=repo, env=env, input=input,
                                  text=True, capture_output=True, check=True).stdout.strip()
        index('read-tree', tree)
        paths = git(repo, 'ls-tree', '-r', '--name-only', tree, '--', '.github/workflows').stdout
        for path in paths.splitlines():
            index('update-index', '--force-remove', '--', path)
        entries = git(repo, 'ls-tree', '-r', head, '--', '.github/workflows').stdout
        if entries:
            index('update-index', '--index-info', input=entries)
        return index('write-tree')


def resolve_module_additions(repo, tree, paths):
    """Resolve only independent bare Rust module declarations at an empty base hunk."""
    unresolved, resolved = [], []
    pattern = re.compile(r'^<<<<<<<[^\n]*\n(.*?)^\|\|\|\|\|\|\|[^\n]*\n(.*?)^=======\n(.*?)^>>>>>>>[^\n]*\n', re.MULTILINE | re.DOTALL)
    with tempfile.TemporaryDirectory() as temp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(temp) / 'index'))
        def index(*args, input=None):
            return subprocess.run(['git', *args], cwd=repo, env=env, input=input, text=True, capture_output=True, check=True).stdout.strip()
        index('read-tree', tree)
        for path in paths:
            if not path.endswith('.rs'):
                unresolved.append(path)
                continue
            result = git(repo, 'show', f'{tree}:{path}', check=False)
            text = result.stdout
            matches = list(pattern.finditer(text)) if path.endswith('.rs') and result.returncode == 0 else []
            safe = bool(matches)
            for match in matches:
                ours, base, theirs = match.groups()
                lines = (ours + theirs).splitlines()
                before = text[:match.start()].rstrip().splitlines()
                safe = safe and not base.strip() and bool(ours.strip()) and bool(theirs.strip())
                safe = safe and all(re.fullmatch(r'mod [a-zA-Z_][a-zA-Z_0-9]*;', line) for line in lines)
                safe = safe and len(lines) == len(set(lines)) and not (before and before[-1].lstrip().startswith('#['))
            if not safe:
                unresolved.append(path)
                continue
            text = pattern.sub(lambda m: ''.join(sorted((m.group(1) + m.group(3)).splitlines(keepends=True))), text)
            if any(marker in text for marker in ('<<<<<<<', '=======', '>>>>>>>')):
                unresolved.append(path)
                continue
            blob = index('hash-object', '-w', '--stdin', input=text)
            index('update-index', '--add', '--cacheinfo', '100644', blob, path)
            resolved.append(path)
        return index('write-tree'), unresolved, resolved


def stamp_version(repo, tag):
    manifest = repo / 'codex-rs/Cargo.toml'
    if not manifest.exists():
        return  # Small repository fixtures; real candidates must pass the build.
    text = manifest.read_text()
    pattern = r'(\[workspace\.package\]\s*\nversion = ")([^"\n]+)(")'
    match = re.search(pattern, text)
    if not match:
        raise ValueError('Cannot locate workspace version; manual review required')
    new = tag.removeprefix('rust-v')
    manifest.write_text(re.sub(pattern, lambda m: m.group(1) + new + m.group(3), text, count=1))
    workspace = tomllib.loads(text)['workspace']
    inherited, visited = set(), set()
    manifests = [manifest]
    for member in workspace['members']:
        manifests.extend(p / 'Cargo.toml' for p in manifest.parent.glob(member))
    while manifests:
        current = manifests.pop().resolve()
        if current in visited or not current.is_file() or not current.is_relative_to(manifest.parent.resolve()):
            continue
        visited.add(current)
        data = tomllib.loads(current.read_text())
        package = data.get('package', {})
        if package.get('version') == {'workspace': True}:
            inherited.add(package['name'])
        tables = [data]
        while tables:
            table = tables.pop()
            for key, value in table.items():
                if key in ('dependencies', 'dev-dependencies', 'build-dependencies') and isinstance(value, dict):
                    manifests.extend(current.parent / dep['path'] / 'Cargo.toml'
                                     for dep in value.values() if isinstance(dep, dict) and 'path' in dep)
                if isinstance(value, dict):
                    tables.append(value)
    lock = repo / 'codex-rs/Cargo.lock'
    parts = lock.read_text().split('\n[[package]]\n')
    old_ids = {}
    for i, block in enumerate(parts[1:], 1):
        package = tomllib.loads(block)
        if 'source' not in package and package['name'] in inherited:
            old_ids[f"{package['name']} {package['version']}"] = f"{package['name']} {new}"
            parts[i] = re.sub(r'^version = "[^"\n]+"$', f'version = "{new}"', block, count=1, flags=re.MULTILINE)
    # Cargo qualifies dependency entries when a package name is ambiguous.
    for i, block in enumerate(parts[1:], 1):
        for old_id, new_id in old_ids.items():
            block = block.replace(f'"{old_id}"', f'"{new_id}"')
        parts[i] = block
    lock.write_text('\n[[package]]\n'.join(parts))
    git(repo, 'add', 'codex-rs/Cargo.toml', 'codex-rs/Cargo.lock')


def prepare(repo, tag, policy):
    if git(repo, 'status', '--porcelain').stdout.strip():
        raise ValueError('Candidate checkout must be clean; use an isolated worktree.')
    state = json.loads((repo / STATE).read_text())
    base = state['upstream_sha']
    head = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    if not re.fullmatch(r'[0-9a-f]{40}', base):
        raise ValueError('Invalid pinned upstream SHA')
    if tag:
        version(tag)
        release = api(f'repos/openai/codex/releases/tags/{tag}')
        if release['draft'] or release['prerelease']:
            raise ValueError('Only published stable releases may be integrated')
    else:
        release = next_release(state['upstream_tag'])
    if release is None:
        return {'status': 'noop', 'base_head': head}
    tag = release['tag_name']
    if version(tag) <= version(state['upstream_tag']):
        raise ValueError('Release must be newer than the integrated baseline')
    git(repo, 'fetch', '--no-tags', UPSTREAM, f'refs/tags/{tag}')
    upstream = git(repo, 'rev-parse', 'FETCH_HEAD^{commit}').stdout.strip()
    files = changed(repo, base, upstream)
    report = {
        'status': 'ready', 'base_head': head, 'previous': state['upstream_tag'],
        'tag': tag, 'upstream_sha': upstream, 'release_url': release['html_url'],
        'release_notes': release.get('body') or '', 'changed_files': files,
        'features_touched': {}, 'conflicts': [], 'reasons': [],
    }
    for name, feature in policy['features'].items():
        touched = [p for p in files if any(p.startswith(prefix) for prefix in feature['paths'])]
        if touched:
            report['features_touched'][name] = touched
    # Stable release branches contain backports: use the explicitly pinned previous
    # release tree, not an older graph merge-base that invents backport conflicts.
    merged = git(repo, '-c', 'merge.conflictStyle=diff3', 'merge-tree', '--write-tree', '--name-only', f'--merge-base={base}', head, upstream, check=False)
    if merged.returncode not in (0, 1):
        raise RuntimeError(merged.stderr)
    report['merge_details'] = merged.stdout
    tree = merged.stdout.splitlines()[0]
    tree = preserve_fork_workflows(repo, tree, head)
    report['preserved_fork_workflows'] = [p for p in files if p.startswith('.github/workflows/')]
    if merged.returncode == 1:
        conflicts = merged.stdout.split('\n\n', 1)[0].splitlines()[1:]
        conflicts = [p for p in conflicts if not p.startswith('.github/workflows/')]
        tree, conflicts, resolved = resolve_module_additions(repo, tree, conflicts)
        report['auto_resolved_module_additions'] = resolved
        if conflicts:
            report['status'] = 'conflict'
            report['conflicts'] = conflicts
            report['reasons'].append('Git 三方合并存在冲突；未覆盖 Codexy 的改动。具体冲突见下表及附件。')
            report['conflict_excerpts'] = {}
            for path in [p for p in conflicts if p.endswith(('.rs', '.toml', '.md', '.ts', '.py'))][:8]:
                blob = git(repo, 'show', f'{tree}:{path}', check=False).stdout
                pos = blob.find('<<<<<<<')
                if pos >= 0:
                    report['conflict_excerpts'][path] = blob[max(0, pos-150):pos+1800]
            return report
    # These are known local WIP features not yet in the published target branch.
    for prefix in policy.get('pending_local_features', []):
        diff = git(repo, 'diff', '--unified=0', base, upstream, '--', prefix).stdout
        changed_lines = '\n'.join(line for line in diff.splitlines() if line[:1] in ('+', '-') and line[:3] not in ('+++', '---'))
        if any(token in changed_lines for token in policy.get('pending_local_tokens', [])):
            report['reasons'].append(f'尚未提交的本地模型供应商功能依赖 {prefix}，兼容性需确认。')
    for name, feature in policy['features'].items():
        for path in feature.get('required_files', []):
            if git(repo, 'cat-file', '-e', f'{tree}:{path}', check=False).returncode:
                report['reasons'].append(f'{name}: 必需文件消失: {path}')
        # Touching the same file is not itself a conflict. The CI behavioral
        # regressions must pass before a clean candidate can be published.
    if report['reasons']:
        report['status'] = 'review_required'
        return report
    commit = git(repo, '-c', 'user.name=Codexy Upstream', '-c', 'user.email=codexy-upstream@users.noreply.github.com',
                 'commit-tree', tree, '-p', head, '-p', upstream,
                 input=f'Merge Codex {tag} into Codexy\n').stdout.strip()
    git(repo, 'checkout', '--detach', commit)
    stamp_version(repo, tag)
    (repo / STATE).write_text(json.dumps({'upstream_tag': tag, 'upstream_sha': upstream}, indent=2) + '\n')
    git(repo, 'add', STATE)
    git(repo, '-c', 'user.name=Codexy Upstream', '-c', 'user.email=codexy-upstream@users.noreply.github.com',
        'commit', '--amend', '--no-edit')
    report['candidate_sha'] = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    return report


def markdown(report):
    lines = [f"# Codexy upstream: {report.get('previous', '')} → {report.get('tag', '')}",
             f"状态：**{report['status']}**", '',
             '修复和新增功能自动进入候选；修改不得覆盖 Codexy 独立功能。只有兼容检查与回归验证均通过才推进分支。',
             '变更类型以官方 release notes 为依据，标题含 fix/feat 不会绕过保护和验证。', '',
             f"上游：{report.get('release_url', '')}",
             f"Codexy 基线：`{report['base_head']}`", '']
    lines.extend('- ' + reason for reason in report.get('reasons', []))
    if report.get('functional_conflict'):
        evidence = report['functional_conflict']
        lines += ['', '### 需要功能取舍',
                  f"Codexy 功能：{evidence.get('feature', '')}",
                  f"上游修改：{evidence.get('upstream_change', '')}",
                  f"无法兼容的原因：{evidence.get('incompatibility', '')}",
                  f"尝试过的兼容方案：{evidence.get('attempted_resolution', '')}",
                  *['- ' + item for item in evidence.get('evidence', [])]]
    for name, paths in report.get('features_touched', {}).items():
        lines += ['', f'### {name}', *[f'- `{p}`' for p in paths]]
    if report.get('conflicts'):
        lines += ['', '### 合并冲突', *[f'- `{p}`' for p in report['conflicts']]]
    for path, excerpt in report.get('conflict_excerpts', {}).items():
        lines += ['', f'### 冲突片段：{path}', '````text', excerpt, '````']
    lines += ['', '### 官方发布说明', report.get('release_notes', '')]
    return '\n'.join(lines) + '\n'


def save(report, path):
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    path.with_suffix('.md').write_text(markdown(report))


def publish(repo, report, branch):
    if report['status'] != 'validated':
        raise ValueError('Only a validated candidate can be published')
    head = git(repo, 'rev-parse', 'HEAD').stdout.strip()
    if head != report['candidate_sha'] or git(repo, 'status', '--porcelain', '--untracked-files=no').stdout.strip():
        raise ValueError('Candidate changed after validation')
    remote = git(repo, 'ls-remote', 'origin', f'refs/heads/{branch}').stdout.split()[0]
    if remote != report['base_head']:
        raise ValueError('Target advanced during validation; rerun from the new baseline')
    # No force: a concurrent writer must never be overwritten.
    result = git(repo, 'push', 'origin', f'{head}:refs/heads/{branch}', check=False)
    if result.returncode:
        raise ValueError(f'Git publication rejected: {result.stderr.strip()}')
    report['status'] = 'published'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['prepare', 'finish'])
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--tag', default='')
    parser.add_argument('--validation', default='failure')
    parser.add_argument('--publish', action='store_true')
    args = parser.parse_args()
    policy = json.loads(Path(__file__).with_name('policy.json').read_text())
    if args.command == 'prepare':
        report = prepare(args.repo.resolve(), args.tag, policy)
    else:
        report = json.loads(args.report.read_text())
        if report['status'] == 'ready':
            if args.validation == 'success':
                report['status'] = 'validated'
                if args.publish:
                    try:
                        publish(args.repo, report, policy['target_branch'])
                    except (ValueError, subprocess.CalledProcessError) as error:
                        report['status'] = 'publication_failed'
                        report['reasons'].append(str(error))
                        save(report, args.report)
                        raise
            else:
                report['status'] = 'validation_failed'
                report['reasons'].append('候选构建/回归失败，或验证被中断。不能据此认定一定存在功能冲突；详见运行日志。')
    save(report, args.report)
    if os.environ.get('GITHUB_OUTPUT'):
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            output.write(f"status={report['status']}\n")
    print(f"{report['status']}: {report.get('tag', '')}")


if __name__ == '__main__':
    main()
