"""Daily local discovery with durable continuation of unfinished updates."""
import argparse
import base64
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import time

import preflight
from sync import api, version

DAY = 24 * 60 * 60


def resume_task(db, repository, row):
    tag, status, created_at, detail = row
    policy = json.loads(Path(__file__).with_name('policy.json').read_text())
    state = api(f"repos/{repository}/contents/.codexy-upstream.json?ref={policy['target_branch']}")
    published = json.loads(base64.b64decode(state['content']))['upstream_tag']
    if version(published) >= version(tag):
        db.execute('UPDATE releases SET status=? WHERE repository=? AND tag=?', ('completed', repository, tag))
        db.commit()
        return {'status': 'completed', 'tag': tag}
    runs = api(f'repos/{repository}/actions/workflows/codexy-upstream.yml/runs?event=workflow_dispatch&per_page=100')['workflow_runs']
    matches = [r for r in runs if r.get('display_title') == f'Codexy upstream {tag}' and r['created_at'] >= created_at]
    if len(matches) != 1:
        return {'status': 'awaiting_dispatch_reconciliation', 'tag': tag,
                'reason': 'No unique matching run; retain task without duplicate dispatch.'}
    run = matches[0]
    status = 'running' if run['status'] != 'completed' else 'needs_repair'
    detail = json.dumps({'run_id': run['id'], 'url': run['html_url'], 'conclusion': run.get('conclusion')})
    db.execute('UPDATE releases SET status=?, detail=? WHERE repository=? AND tag=?', (status, detail, repository, tag))
    db.commit()
    return {'status': status, 'tag': tag, 'run_url': run['html_url'],
            'reason': 'Waiting for the existing run.' if status == 'running' else 'Run finished without publication; retain unfinished task for repair, do not blindly rebuild.'}


def check_once(state_dir, repository='OceanCT/codex', *, dispatch=False, now=None):
    now = time.time() if now is None else now
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state_dir / 'watch.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {'status': 'busy'}
        with closing(sqlite3.connect(state_dir / 'releases.sqlite3')) as db:
            db.execute('CREATE TABLE IF NOT EXISTS releases (repository TEXT, tag TEXT, status TEXT, created_at TEXT, detail TEXT, PRIMARY KEY(repository, tag))')
            db.execute('CREATE TABLE IF NOT EXISTS checks (repository TEXT PRIMARY KEY, successful_at REAL)')
            pending = db.execute("SELECT tag, status, created_at, detail FROM releases WHERE repository=? AND status!='completed' ORDER BY created_at LIMIT 1", (repository,)).fetchone()
            if pending:
                return resume_task(db, repository, pending)
            previous = db.execute('SELECT successful_at FROM checks WHERE repository=?', (repository,)).fetchone()
            if previous and 0 <= now - previous[0] < DAY:
                return {'status': 'not_due', 'next_check_at': previous[0] + DAY}
            result = preflight.check(repository)
            if not dispatch:
                return {'status': 'preview', 'result': result}
            # Record only successful checks, never heartbeat wakeups or failures.
            db.execute('INSERT OR REPLACE INTO checks VALUES (?, ?)', (repository, now))
            if result['proceed'] != 'true':
                db.commit()
                return {'status': 'idle', 'reason': result['reason']}
            tag = result['tag']
            created = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            db.execute('INSERT INTO releases VALUES (?, ?, ?, ?, ?)', (repository, tag, 'dispatching', created, ''))
            db.commit()
            try:
                api(f'repos/{repository}/actions/workflows/codexy-upstream.yml/dispatches', 'POST',
                    {'ref': 'main', 'inputs': {'release_tag': tag}})
            except Exception as error:
                db.execute('UPDATE releases SET status=?, detail=? WHERE repository=? AND tag=?',
                           ('uncertain', type(error).__name__, repository, tag))
                db.commit()
                raise
            db.execute('UPDATE releases SET status=? WHERE repository=? AND tag=?', ('dispatched', repository, tag))
            db.commit()
            return {'status': 'dispatched', 'tag': tag}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--dispatch', action='store_true')
    args = parser.parse_args()
    os.environ.pop('REVIEWED_CANDIDATE', None)
    os.environ.pop('REQUESTED_RELEASE_TAG', None)
    now = datetime.now(timezone.utc).isoformat()
    try:
        result = check_once(args.state_dir, dispatch=args.dispatch)
    except Exception as error:
        result = {'status': 'error', 'error_type': type(error).__name__}
        health = args.state_dir / 'last-check.json'
        health.write_text(json.dumps({'checked_at': now, **result}, indent=2) + '\n')
        print(json.dumps({'checked_at': now, **result}), flush=True)
        raise
    health = args.state_dir / 'last-check.json'
    temp = health.with_suffix('.tmp')
    temp.write_text(json.dumps({'checked_at': now, **result}, indent=2) + '\n')
    temp.replace(health)
    print(json.dumps({'checked_at': now, **result}), flush=True)


if __name__ == '__main__':
    main()
