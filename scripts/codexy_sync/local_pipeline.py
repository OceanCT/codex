"""Local-only source integration, validation and installation. No Actions calls."""
from contextlib import closing
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import execution_history as history
from local_delivery import install, runtime_status, save
from memory_transition import advance_memory
from package_runtime import package
from sync import git, next_release, prepare, publish, save as save_report


def resources(home, minimum_gib=20):
    free = shutil.disk_usage(home).free / 1024**3
    if free < minimum_gib:
        return {'status': 'waiting_disk', 'free_gib': round(free, 1), 'required_gib': minimum_gib}
    processes = subprocess.check_output(['ps', '-axo', 'command='], text=True)
    if any(line.lstrip().startswith(('cargo build', 'cargo nextest', 'rustc ')) or
           ('/cargo build ' in line or '/cargo nextest ' in line or '/rustc ' in line)
           for line in processes.splitlines()):
        return {'status': 'waiting_other_build'}
    return None


def validate(repo, state_dir, cache):
    env = dict(os.environ, CARGO_BUILD_JOBS='1', CARGO_TARGET_DIR=str(cache), NEXTEST_PROFILE='local')
    # No repository token is passed to build/test processes.
    for key in ('GH_TOKEN', 'GITHUB_TOKEN', 'SMTP_PASSWORD'):
        env.pop(key, None)
    env['PATH'] = ':'.join((str(Path.home()/'.cargo/bin'), '/Library/TeX/texbin', env.get('PATH', '')))
    for tool in ('cargo', 'rustc', 'just', 'cargo-nextest', 'pdflatex', 'pdftocairo', 'pdfinfo'):
        if not shutil.which(tool, path=env['PATH']):
            raise RuntimeError('Missing local validation dependency: ' + tool)
    with (state_dir/'validation.log').open('w') as log:
        subprocess.run(['nice', '-n', '10', 'bash', str(Path(__file__).with_name('validate.sh'))],
                       cwd=repo, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)


def advance(state_dir, *, execute=True, now=None, home=None):
    home, now = home or Path.home(), time.time() if now is None else now
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir/'local-pipeline.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy', 'execution': 'local-only'}
        return advance_locked(state_dir, execute=execute, now=now, home=home)


def advance_locked(state_dir, *, execute, now, home):
    path = state_dir/'local-pipeline.json'
    state = json.loads(path.read_text()) if path.exists() else {'execution': 'local-only'}
    if not execute:
        return {'status': 'preview', 'state': state, 'execution': 'local-only'}
    if state.get('status') in ('conflict', 'review_required', 'validation_failed', 'validating', 'activation_failed'):
        # An interrupted/failed build is not silently declared valid or rebuilt.
        return state
    if state.get('installed'):
        try:
            state['runtime'] = runtime_status(home, state)
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            previous = state.get('previous_package')
            if previous and Path(previous).is_dir():
                selected=home/'.codex-y/packages/app-server-daemon/current'
                temporary=selected.with_name('current.rollback')
                temporary.unlink(missing_ok=True); temporary.symlink_to(previous)
                temporary.replace(selected)
            state.update(status='activation_failed', reason=str(error))
            save(path,state)
            return state
        state['status'] = state['runtime']['status']
        if state['status'] != 'active':
            save(path,state)
            return state
        state['memory'] = advance_memory(home,state_dir,state['tag'])
        save(path,state)
        if state.get('next_check_at',0)>now:
            return state
    gate = resources(home)
    if gate:
        state.setdefault('resume_phase',state.get('status'))
        state.update(gate)
        save(path,state)
        return state
    if state.get('resume_phase'):
        state['status']=state.pop('resume_phase')
    policy=json.loads(Path(__file__).with_name('policy.json').read_text())
    source=home/'codex'
    if not (source/'.git').exists():
        raise RuntimeError('Local Codexy source repository missing: '+str(source))
    report_path=state_dir/'local-report.json'
    repo=state_dir.parent/'local-candidate'
    if state.get('status') not in ('prepared','validated','publication_failed','installing'):
        git(source,'fetch','origin',policy['target_branch'])
        target=git(source,'rev-parse','FETCH_HEAD').stdout.strip()
        if repo.exists():
            if git(repo,'status','--porcelain').stdout.strip():
                raise RuntimeError('Local candidate has edits; preserve them for review')
            git(source,'worktree','remove',str(repo))
        git(source,'worktree','add','--detach',str(repo),target)
        pin=json.loads((repo/'.codexy-upstream.json').read_text())
        if state.get('installed'):
            release=next_release(pin['upstream_tag'])
            if release is None:
                state.update(status='active',next_check_at=now+86400)
                save(path,state)
                return state
            report=prepare(repo,release['tag_name'],policy)
        else:
            # Bring the installed runtime up to the already-integrated source first.
            report={'status':'ready','candidate_sha':target,'base_head':target,'tag':pin['upstream_tag'],'reasons':[]}
        save_report(report,report_path)
        state={'execution':'local-only','status':'prepared' if report['status']=='ready' else report['status'],
               'sha':report.get('candidate_sha'),'tag':report['tag'],'report':str(report_path)}
        save(path,state)
        if state['status']!='prepared':
            return state
    report=json.loads(report_path.read_text())
    cache=source/'codex-rs/target'
    if state['status']=='prepared':
        state['status']='validating'; save(path,state)
        try:
            validate(repo,state_dir,cache)
        except Exception as error:
            state.update(status='validation_failed',reason=str(error),log=str(state_dir/'validation.log'))
            save(path,state)
            return state
        state['status']='validated'; save(path,state)
    if state['status'] in ('validated','publication_failed'):
        if report['candidate_sha']!=report['base_head']:
            report['status']='validated'
            try:
                remote=git(repo,'ls-remote','origin',f"refs/heads/{policy['target_branch']}").stdout.split()[0]
                if remote!=report['candidate_sha']:
                    publish(repo,report,policy['target_branch'])
            except Exception as error:
                state.update(status='publication_failed',reason=str(error)); save(path,state)
                return state
        state['status']='installing'; save(path,state)
    package_dir=state_dir.parent/('local-package-'+state['sha'])
    if not package_dir.exists():
        package(repo,package_dir,binary_dir=cache/'debug')
    selected=home/'.codex-y/packages/app-server-daemon/current'
    if 'previous_package' not in state:
        state['previous_package']=str(selected.resolve()) if selected.exists() else None
        save(path,state)
    install(package_dir,home,state['sha'],state['tag'])
    state.update(installed=True,status='installed',next_check_at=now+86400)
    save(path,state)
    return state


def run_recorded(state_dir):
    execution=history.start(state_dir,'local-update')
    try:
        result=advance(state_dir)
    except Exception as error:
        result={'status':'local_error','reason':str(error),'execution':'local-only'}
    history.finish(state_dir,execution,result)
    save(state_dir/'last-check.json',{'execution_id':execution,**result})
    return result
