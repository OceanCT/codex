"""Local-only update entrypoint; legacy --dispatch now means execute locally."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import traceback

import execution_history as history
import local_pipeline


def check_once(state_dir, *, dispatch=False, now=None):
    return local_pipeline.advance(state_dir, execute=dispatch, now=now)


def record_check(state_dir, *, dispatch=False):
    execution_id = history.start(state_dir, 'local-update')
    stdout, stderr = io.StringIO(), io.StringIO()
    failed = False
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            result = check_once(state_dir, dispatch=dispatch)
        except Exception as error:
            failed = True
            result = {'status': 'error', 'error_type': type(error).__name__}
            traceback.print_exc()
    history.finish(state_dir, execution_id, result, stdout.getvalue(), stderr.getvalue())
    return execution_id, result, failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--dispatch', action='store_true', help='Execute the local pipeline (never GitHub Actions)')
    parser.add_argument('--scheduled', action='store_true')
    args = parser.parse_args()
    result = local_pipeline.run_recorded(args.state_dir) if args.dispatch else check_once(args.state_dir)
    print(json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
