"""Deliver verified native artifacts; keep source, disk and running versions distinct."""
import base64
import hashlib
import fcntl
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

from package_runtime import FILES, OPTIONAL_FILES
from sync import version



def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def verify(package, sha, tag):
    manifest = json.loads((package / 'manifest.json').read_text())
    if (manifest['source_sha'], manifest['upstream_tag'], manifest['system'], manifest['machine']) != (sha, tag, platform.system(), platform.machine()):
        raise ValueError('Runtime package source/version/platform mismatch')
    names = set(manifest['files'])
    if not set(FILES) <= names <= set(FILES + OPTIONAL_FILES) or {p.name for p in package.iterdir()} != names | {'manifest.json'}:
        raise ValueError('Unexpected runtime package contents')
    for name in names:
        path = package / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Runtime must contain regular files')
        with path.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != manifest['files'][name]:
                raise ValueError('Runtime checksum mismatch: ' + name)
    return manifest


def install(package, home, sha, tag):
    verify(package, sha, tag)
    for name in ('codex', 'codex-code-mode-host') + OPTIONAL_FILES:
        if (package / name).exists():
            (package / name).chmod(0o755)
    # Probes cannot touch the user's sessions or credentials.
    with tempfile.TemporaryDirectory() as isolated:
        env = dict(os.environ, CODEX_HOME=isolated)
        reported = subprocess.check_output([str(package / 'codex'), '--version'], text=True, env=env, timeout=20).strip()
        if reported != 'codex-cli ' + tag.removeprefix('rust-v'):
            raise ValueError('Executable version does not match manifest')
        subprocess.run([sys.executable, str(package / 'smoke_codex_y_host.py'), str(package / 'codex-code-mode-host')], env=env, check=True, timeout=45, capture_output=True)
    destination = home / '.local/share/codex-y' / ('auto-' + sha)
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.stage-', dir=destination.parent) as stage:
            staged = Path(stage) / 'runtime'
            staged.mkdir()
            shutil.copytree(package, staged / 'bin')
            staged.rename(destination)
    else:
        verify(destination / 'bin', sha, tag)
    selected = home / '.codex-y/packages/app-server-daemon/current'
    selected.parent.mkdir(parents=True, exist_ok=True)
    previous = str(selected.resolve()) if selected.exists() else None
    temporary = selected.with_name('current.delivery')
    temporary.unlink(missing_ok=True)
    temporary.symlink_to(destination, target_is_directory=True)
    temporary.replace(selected)
    return previous


def runtime_status(home, state):
    binary = home / ".codex-y/packages/app-server-daemon/current/bin/codex"
    env = dict(os.environ, CODEX_HOME=str(home / ".codex-y"))
    try:
        result = subprocess.run(
            [str(binary), "app-server", "daemon", "version"],
            env=env,
            text=True,
            capture_output=True,
            timeout=15,
        )
    except subprocess.TimeoutExpired:
        return {
            "status": "awaiting_runtime",
            "reason": "Daemon version probe timed out; retrying",
        }
    if result.returncode:
        # `daemon version` exits nonzero while the old daemon has exited and no
        # replacement owns the socket. `start` is idempotent and never forces a
        # running daemon to stop; this gap is not evidence of a bad package.
        try:
            started = subprocess.run(
                [str(binary), "app-server", "daemon", "start"],
                env=env,
                text=True,
                capture_output=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return {
                "status": "awaiting_runtime",
                "reason": "Daemon startup is not ready; retrying",
            }
        if started.returncode:
            return {
                "status": "awaiting_runtime",
                "reason": "Daemon startup did not complete; retrying",
            }
        return {"status": "starting"}
    info = json.loads(result.stdout)
    desired = state["tag"].removeprefix("rust-v")
    if info.get("status") in ("notRunning", "not_running", "stopped"):
        subprocess.run(
            [str(binary), "app-server", "daemon", "start"],
            env=env,
            check=True,
            timeout=30,
            capture_output=True,
        )
        return {"status": "starting"}
    # SIGHUP is GracefulOnly in the installed 0.159.2+ daemon. Never SIGTERM/KILL:
    # running turns drain without a deadline; repeated HUP cannot force exit.
    pid_file = home / ".codex-y/app-server-daemon/daemon.pid"
    if not pid_file.exists():
        return {"status": "awaiting_runtime", "observed": info}
    record = json.loads(pid_file.read_text())
    pid = record["pid"]
    process = subprocess.run(
        ["ps", "-p", str(pid), "-o", "lstart=", "-o", "command="],
        text=True,
        capture_output=True,
    )
    if process.returncode:
        return {
            "status": "awaiting_runtime",
            "reason": "Daemon exited during the version check",
        }
    actual = process.stdout.strip()
    prefix = record["processStartTime"]
    command = actual.removeprefix(prefix).strip()
    executable = command.split(" ", 1)[0]
    allowed = home / ".local/share/codex-y"
    if (
        not actual.startswith(prefix)
        or not Path(executable).is_relative_to(allowed)
        or " app-server --listen unix:// --managed-daemon" not in command
    ):
        return {
            "status": "restart_blocked",
            "reason": "Daemon process identity does not match the managed Codexy installation",
        }
    if (
        info.get("appServerVersion") == desired
        and Path(executable).resolve() == binary.resolve()
    ):
        return {"status": "active", "version": desired}
    if version("rust-v" + info["appServerVersion"]) < (0, 159, 2):
        return {
            "status": "restart_blocked",
            "reason": "Old runtime has no verified graceful-only drain",
        }
    if state.get("draining_pid") != [pid, prefix]:
        state["draining_pid"] = [pid, prefix]
        # Repeated HUP is safe even if the watcher exits before saving this receipt.
        try:
            os.kill(pid, signal.SIGHUP)
        except ProcessLookupError:
            return {
                "status": "awaiting_runtime",
                "reason": "Daemon exited before the drain signal",
            }
    return {"status": "draining", "version": info.get("appServerVersion"), "pid": pid}
