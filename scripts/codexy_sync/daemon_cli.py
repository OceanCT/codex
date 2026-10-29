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
    print('ID     EXECUTION TYPE                              STARTED                    STATUS                 DURATION   SIZE gzip/raw')
    for row in rows:
        duration = f"{row['finished']-row['started']:.2f}s" if row['finished'] else 'active'
        print(f"{row['id']:<6} {('导入历史' if row['task']=='legacy' else row['task']):<33} {history.timestamp(row['started']):<26} {row['status']:<22} {duration:<10} {row['stored_bytes'] or 0}/{row['raw_bytes'] or 0}")
    if not rows:
        print('(no executions recorded)')


def status(root, project=history.PROJECT):
    info = next(p for p in history.projects(root/'state') if p['id']==project)
    print("Project:", project, "—", info["name"])
    print("Goal:", info["goal"])
    print('Service:', service_state())
    path = root/'state/releases.sqlite3'
    if project == history.PROJECT and path.exists():
        with closing(sqlite3.connect(path)) as db:
            for repository, stamp in db.execute('SELECT repository,successful_at FROM checks'):
                print(f'{repository}: last successful discovery {history.timestamp(stamp)}; next due {history.timestamp(stamp+86400)}')
            for tag, state, detail in db.execute("SELECT tag,status,detail FROM releases WHERE status!='completed'"):
                print(f'Pending {tag}: {state} {detail}')
    print('Latest executions:')
    print_rows(history.rows(root/'state',limit=5,project=project))


def main():
    parser = argparse.ArgumentParser(prog='codexy-daemon')
    parser.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    commands = parser.add_subparsers(dest='command',required=True)
    view = commands.add_parser('status')
    view.add_argument('project',nargs='?',default=history.PROJECT)
    view.add_argument('--watch',action='store_true')
    view.add_argument('--interval',type=float,default=2)
    commands.add_parser('tasks')
    commands.add_parser('projects')
    changes = commands.add_parser('changes')
    changes.add_argument('project',nargs='?',default=history.PROJECT)
    changes.add_argument('--limit',type=int,default=30)
    logs = commands.add_parser('logs')
    logs.add_argument('project')
    logs.add_argument('id',type=int)
    records = commands.add_parser('history')
    records.add_argument('project',nargs='?',default=history.PROJECT)
    records.add_argument('--task')
    records.add_argument('--limit',type=int,default=30)
    show = commands.add_parser('show')
    show.add_argument('id',type=int)
    run = commands.add_parser('run')
    run.add_argument('project',nargs='?',default=history.PROJECT)
    for name in ('start','stop'):
        commands.add_parser(name)
    args = parser.parse_args()
    state = args.root/'state'
    try:
        if hasattr(args,'project') and args.project not in {p['id'] for p in history.projects(state)}:
            raise ValueError(f'Unknown project: {args.project}')
        if hasattr(args,'limit') and not 1 <= args.limit <= 10000:
            raise ValueError('--limit must be between 1 and 10000')
        if args.command == 'status':
            if args.interval < 0.5:
                parser.error('--interval must be at least 0.5 seconds')
            while True:
                if args.watch and sys.stdout.isatty():
                    print('\033[2J\033[H',end='')
                status(args.root,args.project)
                if not args.watch:
                    break
                time.sleep(args.interval)
        elif args.command in ('projects','tasks'):
            print('PROJECT                 NAME                   EXECUTIONS')
            for project in history.projects(state):
                print(f"{project['id']:<23} {project['name']:<22} {project['executions']}")
        elif args.command == 'history':
            if not 1 <= args.limit <= 10000:
                parser.error('--limit must be between 1 and 10000')
            print_rows(history.rows(state,args.task,args.limit,project=args.project))
        elif args.command in ('show','logs'):
            record = history.detail(state,args.id)
            if args.command == 'logs' and record['project'] != args.project:
                raise ValueError('Execution does not belong to this project')
            print(json.dumps(record,ensure_ascii=False,indent=2))
        elif args.command == 'changes':
            records = history.changes(state,args.project,args.limit)
            for record in records:
                print(f"#{record['id']} {history.timestamp(record['occurred'])} [{record['kind']}] {record['summary']}")
                if record['execution_id']:
                    print(f"  execution: {record['execution_id']}")
                if record['detail']:
                    print('  ' + json.dumps(record['detail'],ensure_ascii=False))
            if not records:
                print('(no changes recorded)')
        elif args.command == 'run':
            if args.project != history.PROJECT:
                raise ValueError('No executor registered for this project')
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
