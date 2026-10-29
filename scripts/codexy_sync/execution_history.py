"""Queryable execution index with gzip-compressed payloads in one transaction."""
from contextlib import contextmanager
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time


@contextmanager
def database(state_dir):
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(state_dir / 'history.sqlite3', timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute('PRAGMA journal_mode=WAL')
        db.execute('CREATE TABLE IF NOT EXISTS executions (id INTEGER PRIMARY KEY, task TEXT NOT NULL, started REAL NOT NULL, finished REAL, status TEXT NOT NULL, pid INTEGER, payload BLOB, raw_bytes INTEGER, source TEXT UNIQUE)')
        db.execute('CREATE INDEX IF NOT EXISTS executions_task_id ON executions(task, id)')
        yield db
        db.commit()
    finally:
        db.close()


def start(state_dir, task):
    with database(state_dir) as db:
        return db.execute('INSERT INTO executions(task,started,status,pid) VALUES (?,?,?,?)', (task,time.time(),'running',os.getpid())).lastrowid


def finish(state_dir, execution_id, result, stdout='', stderr=''):
    raw = json.dumps({'result':result,'stdout':stdout,'stderr':stderr}, ensure_ascii=False).encode()
    with database(state_dir) as db:
        db.execute('UPDATE executions SET task=COALESCE(?,task), finished=?, status=?, payload=?, raw_bytes=? WHERE id=?',
                   ('release:'+result['tag'] if result.get('tag') else None, time.time(), result['status'], gzip.compress(raw), len(raw), execution_id))


def rows(state_dir, task=None, limit=30, latest=False):
    with database(state_dir) as db:
        sql = 'SELECT id,task,started,finished,status,pid,raw_bytes,length(payload) AS stored_bytes FROM executions'
        args = []
        if latest:
            sql += ' WHERE id IN (SELECT MAX(id) FROM executions GROUP BY task)'
        elif task:
            sql += ' WHERE task=?'
            args.append(task)
        sql += ' ORDER BY id DESC LIMIT ?'
        return [observed(dict(row)) for row in db.execute(sql, [*args, limit])]


def observed(value):
    if value['status'] == 'running' and value.get('pid'):
        try:
            os.kill(value['pid'], 0)
        except ProcessLookupError:
            value['status'] = 'interrupted'
        except PermissionError:
            pass
    return value


def detail(state_dir, execution_id):
    with database(state_dir) as db:
        row = db.execute('SELECT * FROM executions WHERE id=?',(execution_id,)).fetchone()
        if row is None:
            raise ValueError(f'Unknown execution: {execution_id}')
        value = dict(row)
        payload = value.pop('payload')
        value['detail'] = json.loads(gzip.decompress(payload)) if payload else None
        return observed(value)


def import_legacy(state_dir, logs_dir):
    for path in [state_dir/'last-check.json', logs_dir/'stdout.log', logs_dir/'stderr.log']:
        if not path.exists() or not path.stat().st_size:
            continue
        data = path.read_bytes()
        source = str(path)+':'+hashlib.sha256(data).hexdigest()
        raw = json.dumps({'legacy_path':str(path),'text':data.decode(errors='replace')}, ensure_ascii=False).encode()
        with database(state_dir) as db:
            db.execute('INSERT OR IGNORE INTO executions(task,started,finished,status,payload,raw_bytes,source) VALUES (?,?,?,?,?,?,?)',
                       ('legacy',path.stat().st_mtime,path.stat().st_mtime,'imported',gzip.compress(raw),len(raw),source))
        # LaunchAgent is stopped by installer during migration.
        if path.parent == logs_dir:
            path.write_text('')


def timestamp(value):
    return datetime.fromtimestamp(value).astimezone().isoformat(timespec='seconds') if value else '-'
