"""Install the local watcher without copying GitHub or SMTP credentials."""
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys


def main():
    source = Path(__file__).resolve().parents[2]
    home = Path.home()
    root = home / '.local/share/codexy-upstream'
    controller = root / 'controller'
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
    label = 'com.oceanct.codexy-upstream'
    plist = home / 'Library/LaunchAgents' / (label + '.plist')
    plist.parent.mkdir(parents=True, exist_ok=True)
    config = {
        'Label': label,
        'ProgramArguments': [sys.executable, str(controller / 'scripts/codexy_sync/local_watch.py'), '--state-dir', str(root / 'state'), '--dispatch'],
        'WorkingDirectory': str(controller),
        'RunAtLoad': True,
        # A heartbeat allows incomplete work to resume; the durable 24h gate
        # prevents release API checks on idle heartbeats.
        'StartInterval': 900,
        'ProcessType': 'Background',
        'Nice': 10,
        'EnvironmentVariables': {'PATH': f'{home}/.local/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'LANG': 'en_US.UTF-8'},
        'StandardOutPath': str(root / 'logs/stdout.log'),
        'StandardErrorPath': str(root / 'logs/stderr.log'),
    }
    plist.write_bytes(plistlib.dumps(config))
    domain = f'gui/{os.getuid()}'
    subprocess.run(['launchctl', 'bootout', f'{domain}/{label}'], capture_output=True)
    subprocess.run(['launchctl', 'bootstrap', domain, str(plist)], check=True)
    print(plist)


if __name__ == '__main__':
    main()
