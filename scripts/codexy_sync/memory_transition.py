"""Opt existing memory stores into upstream dual-write, cut over only when ready.

Never rewrite, copy or delete memory content. Respect explicit opt-outs and edits.
"""
import hashlib
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import tomllib

from sync import version


def v2_ready(home):
    root = home / '.codex-y'
    database = root / 'memories_v2_1.sqlite'
    summary = root / 'memories_v2/memory_summary.md'
    if not database.exists() or not summary.is_file():
        return False
    with closing(sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        row = db.execute('SELECT max_thread_count FROM consolidation_progress WHERE singleton = 1').fetchone()
    text = summary.read_text()
    return bool(row and row[0] >= 20 and text.splitlines()[:1] == ['v1'] and len(text.encode()) < 10000 and
                all(heading in {line.strip() for line in text.splitlines()} for heading in
                    ('## User Profile', '## User preferences', '## General Tips', "## What's in Memory")))


def update_config(path, original, values, state_dir):
    # Only edit an ordinary [memories] table, preserving unrelated TOML byte-for-byte.
    table = re.search(r'^\[memories\][ \t]*(?:#.*)?$', original, re.M)
    if not table and 'memories' in tomllib.loads(original):
        raise ValueError('Nonstandard memories TOML requires manual migration')
    if table:
        start = table.end()
        following = re.search(r'^\[', original[start:], re.M)
        end = start + following.start() if following else len(original)
        body = original[start:end]
        for key, value in values.items():
            line = key + ' = ' + json.dumps(value)
            pattern = r'^' + key + r'\s*=.*$'
            if re.search(pattern, body, re.M):
                body = re.sub(pattern, line, body, flags=re.M)
            else:
                body += '\n' + line + '\n'
        updated = original[:start] + body + original[end:]
    else:
        updated = original + '\n[memories]\n' + ''.join(k + ' = ' + json.dumps(v) + '\n' for k, v in values.items())
    parsed = tomllib.loads(updated)
    if any(parsed['memories'].get(k) != v for k, v in values.items()):
        raise ValueError('Memory configuration verification failed')
    if path.read_text() != original:
        raise ValueError('Configuration changed concurrently; retry later')
    backup = state_dir / ('config-before-memory-' + hashlib.sha256(original.encode()).hexdigest()[:12] + '.toml')
    if not backup.exists():
        backup.write_text(original)
        backup.chmod(0o600)
    temp = path.with_name('config.memory-transition.tmp')
    temp.write_text(updated)
    temp.chmod(path.stat().st_mode & 0o777)
    temp.replace(path)


def advance_memory(home, state_dir, tag):
    if version(tag) < (0, 165, 0):
        return {'status': 'awaiting_supported_runtime'}
    config = home / '.codex-y/config.toml'
    if not config.is_file():
        return {'status': 'no_configuration'}
    original = config.read_text()
    settings = tomllib.loads(original)
    memories = settings.get('memories', {})
    marker = state_dir / 'memory-transition.json'
    managed = json.loads(marker.read_text()) if marker.exists() else {}
    if settings.get('features', {}).get('memory_tool') is False or any(memories.get(k) is False for k in ('use_memories', 'generate_memories', 'dual_write')):
        return {'status': 'opted_out'}
    if memories.get('version') == 'v2':
        return {'status': 'v2_selected'}
    if memories.get('version') and not managed:
        return {'status': 'explicit_version_preserved'}
    if not (home / '.codex-y/memories').exists():
        return {'status': 'fresh_profile_v2_default'}
    if managed and memories.get('version') != 'v1':
        return {'status': 'user_configuration_changed'}
    if not managed:
        update_config(config, original, {'version': 'v1', 'dual_write': True}, state_dir)
        marker.write_text(json.dumps({'managed': True}) + '\n')
        return {'status': 'warming_v2', 'reason': 'Keep reading v1 until 20 threads consolidate and summary passes upstream readiness checks'}
    try:
        ready = v2_ready(home)
    except (OSError, sqlite3.Error):
        ready = False
    if ready:
        update_config(config, original, {'version': 'v2'}, state_dir)
        return {'status': 'v2_selected', 'reason': 'Ready; takes effect in newly created sessions'}
    return {'status': 'warming_v2'}
