"""Inspect and control the local Codexy release service."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from contextlib import closing

import execution_history as history

LABEL = 'com.oceanct.codexy-upstream'
DEFAULT_ROOT = Path.home()/'.local/share/codexy-upstream'


def service_state():
    result = subprocess.run(['launchctl','print',f'gui/{os.getuid()}/{LABEL}'], text=True, capture_output=True)
    if result.returncode:
        return 'not loaded'
    state = next((line.strip().split(' = ',1)[1] for line in result.stdout.splitlines() if line.strip().startswith('state =')), 'loaded')
    return 'loaded (idle between checks)' if state == 'not running' else 'loaded (' + state + ')'


def print_rows(rows):
    print('ID     TASK                              STARTED                    STATUS                 DURATION   SIZE gzip/raw')
    for row in rows:
        duration = f"{row['finished']-row['started']:.2f}s" if row['finished'] else 'active'
        print(f"{row['id']:<6} {row['task']:<33} {history.timestamp(row['started']):<26} {row['status']:<22} {duration:<10} {row['stored_bytes'] or 0}/{row['raw_bytes'] or 0}")
    if not rows:
        print('(no executions recorded)')


def status(root):
    print('Service:', service_state())
    path = root/'state/releases.sqlite3'
    if path.exists():
        with closing(sqlite3.connect(path)) as db:
            for repository, stamp in db.execute('SELECT repository,successful_at FROM checks'):
                print(f'{repository}: last successful discovery {history.timestamp(stamp)}; next due {history.timestamp(stamp+86400)}')
            for tag, state, detail in db.execute("SELECT tag,status,detail FROM releases WHERE status!='completed'"):
                print(f'Pending {tag}: {state} {detail}')
    print_rows(history.rows(root/'state',latest=True))


def main():
    parser = argparse.ArgumentParser(prog='codexy-daemon')
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest='command',required=True)
    view = commands.add_parser('status')
    view.add_argument('--watch',action='store_true')
    view.add_argument('--interval',type=float,default=2)
    commands.add_parser('tasks')
    records = commands.add_parser('history')
    records.add_argument('--task')
    records.add_argument('--limit',type=int,default=30)
    show = commands.add_parser('show')
    show.add_argument('id',type=int)
    for name in ('run','start','stop'):
        commands.add_parser(name)
    args = parser.parse_args()
    state = args.root/'state'
    try:
        if args.command == 'status':
            if args.interval < 0.5:
                parser.error('--interval must be at least 0.5 seconds')
            while True:
                if args.watch and sys.stdout.isatty():
                    print('\033[2J\033[H',end='')
                status(args.root)
                if not args.watch:
                    break
                time.sleep(args.interval)
        elif args.command == 'tasks':
            print_rows(history.rows(state,latest=True,limit=10000))
        elif args.command == 'history':
            if not 1 <= args.limit <= 10000:
                parser.error('--limit must be between 1 and 10000')
            print_rows(history.rows(state,args.task,args.limit))
        elif args.command == 'show':
            print(json.dumps(history.detail(state,args.id),ensure_ascii=False,indent=2))
        elif args.command == 'run':
            raise SystemExit(subprocess.call([sys.executable,str(Path(__file__).with_name('local_watch.py')),'--state-dir',str(state),'--dispatch']))
        elif args.command == 'stop':
            subprocess.run(['launchctl','bootout',f'gui/{os.getuid()}/{LABEL}'],check=True)
        elif args.command == 'start':
            if service_state() != 'not loaded':
                print('Service already loaded.')
            else:
                subprocess.run(['launchctl','bootstrap',f'gui/{os.getuid()}',str(Path.home()/'Library/LaunchAgents'/f'{LABEL}.plist')],check=True)
    except KeyboardInterrupt:
        pass
    except (ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1,str(error)+'\n')


if __name__ == '__main__':
    main()
