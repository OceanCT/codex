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


PROJECT = "codexy-upstream"


@contextmanager
def database(state_dir, *, readonly=False):
    if not readonly:
        state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state_dir / 'history.sqlite3'
    db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10) if readonly else sqlite3.connect(path, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        if not readonly:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS executions (id INTEGER PRIMARY KEY, task TEXT NOT NULL, started REAL NOT NULL, finished REAL, status TEXT NOT NULL, pid INTEGER, payload BLOB, raw_bytes INTEGER, source TEXT UNIQUE)')
            if 'project' not in {r[1] for r in db.execute('PRAGMA table_info(executions)')}:
                db.execute("ALTER TABLE executions ADD COLUMN project TEXT NOT NULL DEFAULT 'codexy-upstream'")
            db.execute('CREATE INDEX IF NOT EXISTS executions_project_task ON executions(project,task,id)')
            db.execute('CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, goal TEXT NOT NULL, created REAL NOT NULL)')
            db.execute('INSERT OR IGNORE INTO projects VALUES (?,?,?,?)', (PROJECT,'Codexy 自动更新','每天检查上游发布，持续跟踪未完成更新，保留独立功能。',time.time()))
            db.execute('CREATE TABLE IF NOT EXISTS changes (id INTEGER PRIMARY KEY, project TEXT NOT NULL, occurred REAL NOT NULL, kind TEXT NOT NULL, summary TEXT NOT NULL, execution_id INTEGER, payload BLOB, source TEXT UNIQUE)')
            db.execute('CREATE INDEX IF NOT EXISTS changes_project ON changes(project,id)')
        yield db
        db.commit()
    finally:
        db.close()


def start(state_dir, task, project=PROJECT):
    with database(state_dir) as db:
        return db.execute('INSERT INTO executions(task,started,status,pid,project) VALUES (?,?,?,?,?)', (task,time.time(),'running',os.getpid(),project)).lastrowid


def finish(state_dir, execution_id, result, stdout='', stderr=''):
    raw = json.dumps({'result':result,'stdout':stdout,'stderr':stderr}, ensure_ascii=False).encode()
    with database(state_dir) as db:
        db.execute('UPDATE executions SET task=COALESCE(?,task), finished=?, status=?, payload=?, raw_bytes=? WHERE id=?',
                   ('release:'+result['tag'] if result.get('tag') else None, time.time(), result['status'], gzip.compress(raw), len(raw), execution_id))
        if result.get('tag') and result['status'] in ('dispatched','completed','needs_repair'):
            project = db.execute('SELECT project FROM executions WHERE id=?',(execution_id,)).fetchone()[0]
            source = project + ':' + result['tag'] + ':' + result['status']
            db.execute('INSERT OR IGNORE INTO changes(project,occurred,kind,summary,execution_id,payload,source) VALUES (?,?,?,?,?,?,?)',
                       (project,time.time(),'update',result['tag'] + ': ' + result['status'],execution_id,gzip.compress(raw),source))


def rows(state_dir, task=None, limit=30, latest=False, project=PROJECT):
    if not (state_dir / 'history.sqlite3').exists():
        return []
    with database(state_dir, readonly=True) as db:
        sql = 'SELECT id,project,task,started,finished,status,pid,raw_bytes,length(payload) AS stored_bytes FROM executions WHERE project=?'
        args = [project]
        if latest:
            sql += ' AND id IN (SELECT MAX(id) FROM executions WHERE project=? GROUP BY task)'
            args.append(project)
        elif task:
            sql += ' AND task=?'
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
    if not (state_dir / 'history.sqlite3').exists():
        raise ValueError(f'Unknown execution: {execution_id}')
    with database(state_dir, readonly=True) as db:
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


def projects(state_dir):
    if not (state_dir/'history.sqlite3').exists():
        return []
    with database(state_dir, readonly=True) as db:
        return [dict(r) for r in db.execute('SELECT p.*, (SELECT COUNT(*) FROM executions e WHERE e.project=p.id) AS executions FROM projects p ORDER BY p.id')]


def change(state_dir, summary, *, kind='configuration', project=PROJECT, execution_id=None, detail=None, source=None):
    raw=json.dumps(detail or {},ensure_ascii=False).encode()
    with database(state_dir) as db:
        db.execute('INSERT OR IGNORE INTO changes(project,occurred,kind,summary,execution_id,payload,source) VALUES (?,?,?,?,?,?,?)',
                   (project,time.time(),kind,summary,execution_id,gzip.compress(raw),source))


def changes(state_dir, project=PROJECT, limit=30):
    if not (state_dir/'history.sqlite3').exists():
        return []
    with database(state_dir, readonly=True) as db:
        result=[]
        for row in db.execute('SELECT * FROM changes WHERE project=? ORDER BY id DESC LIMIT ?', (project,limit)):
            value=dict(row)
            value['detail']=json.loads(gzip.decompress(value.pop('payload')))
            result.append(value)
        return result
