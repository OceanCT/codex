"""Install the local watcher without copying GitHub or SMTP credentials."""
import os
import json
from pathlib import Path
import plistlib
import shutil
import shlex
import execution_history as history
import subprocess
import sys
import sqlite3
from contextlib import closing


def main():
    source = Path(__file__).resolve().parents[2]
    home = Path.home()
    root = home / '.local/share/codexy-upstream'
    controller = root / 'controller'
    label = 'com.oceanct.codexy-upstream'
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', f'{domain}/{label}'], capture_output=True)
    for relative in ('scripts/codexy_sync', '.github/workflows'):
        dest = controller / relative
        dest.mkdir(parents=True, exist_ok=True)
        if relative.startswith('scripts'):
            for path in (source / relative).iterdir():
                if path.is_file() and path.suffix in ('.py', '.json', '.sh', '.md'):
                    shutil.copy2(path, dest / path.name)
        else:
            for workflow in ('codexy-upstream.yml', 'codexy-package.yml'):
                shutil.copy2(source / relative / workflow, dest / workflow)
    (root / 'state').mkdir(mode=0o700, exist_ok=True)
    (root / 'logs').mkdir(mode=0o700, exist_ok=True)
    delivery_policy = root / 'state/delivery-policy.json'
    # User policy: never fall back to GitHub execution.
    delivery_policy.write_text(json.dumps({'enabled': False, 'execution': 'local-only'}) + '\n')
    existing = root / 'state/history.sqlite3'
    backup = root / 'state/history.before-projects.sqlite3'
    if existing.exists() and not backup.exists():
        with closing(sqlite3.connect(existing)) as old, closing(sqlite3.connect(backup)) as saved:
            old.backup(saved)
    history.import_legacy(root / 'state', root / 'logs')
    history.change(root / 'state', '启用项目层级；既有执行与导入日志归入 Codexy 自动更新项目', source='project-migration-v1')
    revision = subprocess.run(['git','rev-parse','HEAD'],cwd=source,text=True,capture_output=True)
    if revision.returncode == 0:
        commit = revision.stdout.strip()
        history.change(root / 'state', '安装守护任务 CLI 版本 ' + commit[:10], kind='installation', detail={'commit':commit}, source='install:' + commit)
    releases = root / 'state/releases.sqlite3'
    retired_backup = root / 'state/releases.before-local-only.sqlite3'
    if releases.exists():
        with closing(sqlite3.connect(releases)) as db:
            if not retired_backup.exists():
                with closing(sqlite3.connect(retired_backup)) as backup:
                    db.backup(backup)
            db.execute("UPDATE releases SET status='retired_remote' WHERE status NOT IN ('completed','retired_remote')")
            db.commit()
    with history.database(root / 'state') as db:
        db.execute('UPDATE projects SET goal=? WHERE id=?', ('所有合并、编译、测试和安装只在本机推进；禁止 GitHub Actions 执行或云端回退。', history.PROJECT))
    history.change(root / 'state', '切换为永久本地执行；停用远程派发并保留原始记录', source='local-only-policy-v1')
    launcher = home / '.local/bin/codexy-daemon'
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' ' + shlex.quote(str(controller / 'scripts/codexy_sync/daemon_cli.py')) + ' "$@"\n')
    launcher.chmod(0o755)
    plist = home / 'Library/LaunchAgents' / (label + '.plist')
    plist.parent.mkdir(parents=True, exist_ok=True)
    config = {
        'Label': label,
        'ProgramArguments': [sys.executable, str(controller / 'scripts/codexy_sync/local_watch.py'), '--state-dir', str(root / 'state'), '--dispatch', '--scheduled'],
        'WorkingDirectory': str(controller),
        'RunAtLoad': True,
        # A heartbeat allows incomplete work to resume; the durable 24h gate
        # prevents release API checks on idle heartbeats.
        # Restart gaps must not outlast existing TUI reconnect windows.
        'StartInterval': 30,
        'ProcessType': 'Background',
        'Nice': 10,
        'LowPriorityIO': True,
        'EnvironmentVariables': {'PATH': f'{home}/.cargo/bin:{home}/.local/bin:/Library/TeX/texbin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8'},
        'StandardOutPath': '/dev/null',
        'StandardErrorPath': str(root / 'logs/stderr.log'),
    }
    plist.write_bytes(plistlib.dumps(config))
    subprocess.run(['launchctl', 'enable', f'{domain}/{label}'], check=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    print(plist)


if __name__ == '__main__':
    main()
