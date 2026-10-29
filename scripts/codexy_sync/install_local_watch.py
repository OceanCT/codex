"""Install the local watcher without copying GitHub or SMTP credentials."""
import os
from pathlib import Path
import plistlib
import shutil
import shlex
import execution_history as history
import subprocess
import sys


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
            shutil.copy2(source / relative / 'codexy-upstream.yml', dest / 'codexy-upstream.yml')
    (root / 'state').mkdir(mode=0o700, exist_ok=True)
    (root / 'logs').mkdir(mode=0o700, exist_ok=True)
    history.import_legacy(root / 'state', root / 'logs')
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
        'StartInterval': 900,
        'ProcessType': 'Background',
        'Nice': 10,
        'LowPriorityIO': True,
        'EnvironmentVariables': {'PATH': f'{home}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8'},
        'StandardOutPath': '/dev/null',
        'StandardErrorPath': str(root / 'logs/stderr.log'),
    }
    plist.write_bytes(plistlib.dumps(config))
    subprocess.run(['launchctl', 'enable', f'{domain}/{label}'], check=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    print(plist)


if __name__ == '__main__':
    main()
