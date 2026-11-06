"""Deliver verified native artifacts; keep source, disk and running versions distinct."""
import base64
import hashlib
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from package_runtime import FILES
from sync import api, ApiError, version

WORKFLOW = 'codexy-package.yml'


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def verify(package, sha, tag):
    manifest = json.loads((package / 'manifest.json').read_text())
    if (manifest['source_sha'], manifest['upstream_tag'], manifest['system'], manifest['machine']) != (sha, tag, platform.system(), platform.machine()):
        raise ValueError('Runtime package source/version/platform mismatch')
    if set(manifest['files']) != set(FILES) or {p.name for p in package.iterdir()} != set(FILES) | {'manifest.json'}:
        raise ValueError('Unexpected runtime package contents')
    for name in FILES:
        path = package / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Runtime must contain regular files')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != manifest['files'][name]:
                raise ValueError('Runtime checksum mismatch: ' + name)
    return manifest


def install(package, home, sha, tag):
    verify(package, sha, tag)
    for name in ('codex', 'codex-code-mode-host'):
        (package / name).chmod(0o755)
    # Probes cannot touch the user's sessions or credentials.
    with tempfile.TemporaryDirectory() as isolated:
        env = dict(os.environ, CODEX_HOME=isolated)
        reported = subprocess.check_output([str(package / 'codex'), '--version'], text=True, env=env, timeout=20).strip()
        if reported != 'codex-cli ' + tag.removeprefix('rust-v'):
            raise ValueError('Executable version does not match manifest')
        subprocess.run([sys.executable, str(package / 'smoke_codex_y_host.py'), str(package / 'codex-code-mode-host')], env=env, check=True, timeout=45, capture_output=True)
    destination = home / '.local/share/codex-y' / ('auto-' + sha)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.stage-', dir=destination.parent) as stage:
            staged = Path(stage) / 'runtime'
            staged.mkdir()
            shutil.copytree(package, staged / 'bin')
            staged.rename(destination)
    else:
        verify(destination / 'bin', sha, tag)
    selected = home / '.codex-y/packages/app-server-daemon/current'
    selected.parent.mkdir(parents=True, exist_ok=True)
    previous = str(selected.resolve()) if selected.exists() else None
    temporary = selected.with_name('current.delivery')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(destination, target_is_directory=True)
    temporary.replace(selected)
    return previous


def runtime_status(home, state):
    binary = home / '.codex-y/packages/app-server-daemon/current/bin/codex'
    env = dict(os.environ, CODEX_HOME=str(home / '.codex-y'))
    result = subprocess.run([str(binary), 'app-server', 'daemon', 'version'], env=env, text=True, capture_output=True, timeout=15, check=True)
    info = json.loads(result.stdout)
    desired = state['tag'].removeprefix('rust-v')
    if info.get('status') in ('notRunning', 'not_running', 'stopped'):
        subprocess.run([str(binary), 'app-server', 'daemon', 'start'], env=env, check=True, timeout=30, capture_output=True)
        return {'status': 'starting'}
    # SIGHUP is GracefulOnly in the installed 0.159.2+ daemon. Never SIGTERM/KILL:
    # running turns drain without a deadline; repeated HUP cannot force exit.
    pid_file = home / '.codex-y/app-server-daemon/daemon.pid'
    if not pid_file.exists():
        return {'status': 'awaiting_runtime', 'observed': info}
    record = json.loads(pid_file.read_text())
    pid = record['pid']
    actual = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart=', '-o', 'command='], text=True, capture_output=True, check=True).stdout.strip()
    prefix = record['processStartTime']
    command = actual.removeprefix(prefix).strip()
    executable = command.split(' ', 1)[0]
    allowed = home / '.local/share/codex-y'
    if not actual.startswith(prefix) or not Path(executable).is_relative_to(allowed) or ' app-server --listen unix:// --managed-daemon' not in command:
        return {'status': 'restart_blocked', 'reason': 'Daemon process identity does not match the managed Codexy installation'}
    if info.get('appServerVersion') == desired and Path(executable).resolve() == binary.resolve():
        return {'status': 'active', 'version': desired}
    if version('rust-v' + info['appServerVersion']) < (0, 159, 2):
        return {'status': 'restart_blocked', 'reason': 'Old runtime has no verified graceful-only drain'}
    if state.get('draining_pid') != [pid, prefix]:
        state['draining_pid'] = [pid, prefix]
        # Repeated HUP is safe even if the watcher exits before saving this receipt.
        os.kill(pid, signal.SIGHUP)
    return {'status': 'draining', 'version': info.get('appServerVersion'), 'pid': pid}


def advance(state_dir, repository='OceanCT/codex', *, home=None, now=None):
    state_dir.mkdir(parents=True, exist_ok=True)
    with (state_dir / 'delivery.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy'}
        return _advance(state_dir, repository, home=home, now=now)


def _advance(state_dir, repository, *, home, now):
    home, now = home or Path.home(), time.time() if now is None else now
    path = state_dir / 'delivery.json'
    state = json.loads(path.read_text()) if path.exists() else {}
    if state.get('retry_at', 0) > now:
        return state
    if state.get('status') == 'activation_failed':
        return state
    if state.get('installed'):
        try:
            state['runtime'] = runtime_status(home, state)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            previous = state.get('previous_package')
            if previous and Path(previous).is_dir():
                current = home / '.codex-y/packages/app-server-daemon/current'
                link = current.with_name('current.rollback')
                link.unlink(missing_ok=True)
                link.symlink_to(previous, target_is_directory=True)
                link.replace(current)
            state.update(status='activation_failed', reason=str(error), installed=False)
            save(path, state)
            return state
        state['status'] = state['runtime']['status']
        from memory_transition import advance_memory
        if state['runtime']['status'] == 'active':
            state['memory'] = advance_memory(home, state_dir, state['tag'])
        save(path, state)
        # Finish activation before chasing another source version.
        if state['runtime']['status'] != 'active' or state.get('next_source_check', 0) > now:
            return state
    target = api(f'repos/{repository}/git/ref/heads/oceanct/agents-recycle-bin')['object']['sha']
    if state.get('sha') == target and state.get('installed'):
        state['next_source_check'] = now + 86400
        save(path, state)
        return state
    if state.get('sha') != target:
        pin = api(f'repos/{repository}/contents/.codexy-upstream.json?ref={target}')
        tag = json.loads(base64.b64decode(pin['content']))['upstream_tag']
        version(tag)
        state = {'sha': target, 'tag': tag, 'status': 'pending'}
    title = f'Codexy package {target} {platform.machine()}'
    # Only trust this workflow on main; exact run title binds the requested source.
    matches = []
    for page in range(1, 101):
        runs = api(f'repos/{repository}/actions/workflows/{WORKFLOW}/runs?event=workflow_dispatch&branch=main&per_page=100&page={page}')['workflow_runs']
        matches.extend(r for r in runs if r['display_title'] == title)
        if len(runs) < 100:
            break
    else:
        raise RuntimeError('Package workflow scan limit exceeded')
    if matches:
        latest = max(matches, key=lambda r: r['id'])
        state.update(run_id=latest['id'], run_url=latest['html_url'])
        if latest['status'] != 'completed':
            state['status'] = 'building'
        elif latest['conclusion'] != 'success':
            state.update(status='build_failed', reason=latest['conclusion'], retry_at=now + 6 * 3600)
        else:
            state['status'] = 'installing'
            selected = home / '.codex-y/packages/app-server-daemon/current'
            if 'previous_package' not in state:
                state['previous_package'] = str(selected.resolve()) if selected.exists() else None
            save(path, state)
            if shutil.disk_usage(home).free < 3 * 1024**3:
                raise RuntimeError('Need at least 3 GiB free to stage the native runtime')
            with tempfile.TemporaryDirectory(prefix='codexy-package-') as temp:
                package = Path(temp)
                subprocess.run(['gh', 'run', 'download', str(latest['id']), '--repo', repository, '--name', 'codexy-macos-' + platform.machine(), '--dir', temp], check=True, timeout=300, capture_output=True)
                install(package, home, target, state['tag'])
            state.update(installed=True, status='installed')
            save(path, state)
            # Activation is reconciled on the next heartbeat, after the install receipt is durable.
            state['runtime'] = {'status': 'awaiting_activation'}
    elif state.get('status') in ('dispatching', 'uncertain', 'dispatched'):
        state.update(status='uncertain', reason='No matching package run yet; no duplicate dispatch')
    else:
        state.update(status='dispatching')
        save(path, state)
        try:
            api(f'repos/{repository}/actions/workflows/{WORKFLOW}/dispatches', 'POST', {'ref': 'main', 'inputs': {'source_sha': target, 'machine': platform.machine()}})
            state['status'] = 'dispatched'
        except ApiError as error:
            rejected = error.status in (400, 401, 403, 404, 422, 429)
            state.update(status='dispatch_rejected' if rejected else 'uncertain', reason=str(error), retry_at=now + 6 * 3600)
    save(path, state)
    return state
