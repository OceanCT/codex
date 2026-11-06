"""Package a validated native build with an exact source and file manifest."""
import hashlib
import json
from pathlib import Path
import platform
import shutil
import sys
from sync import git

OPTIONAL_FILES = ('codex-exec-server',)
FILES = ('codex', 'codex-code-mode-host', 'render_codex_y_latex.py', 'smoke_codex_y_host.py')


def package(repo, destination, binary_dir=None):
    binary_dir = binary_dir or repo / "codex-rs/target/debug"
    if git(repo, 'status', '--porcelain').stdout.strip():
        raise ValueError('Refusing to package a dirty source tree')
    shutdown = (repo / 'codex-rs/app-server/src/lib.rs').read_text()
    if 'SignalKind::hangup()' not in shutdown or 'ShutdownSignal::GracefulOnly' not in shutdown:
        raise ValueError('Runtime lacks the verified graceful-only update signal')
    destination.mkdir(parents=True)
    names = FILES + tuple(name for name in OPTIONAL_FILES if (binary_dir / name).is_file())
    for name in names:
        source = (repo / 'scripts' if name.endswith('.py') else binary_dir) / name
        shutil.copy2(source, destination / name)
    manifest = {
        'source_sha': git(repo, 'rev-parse', 'HEAD').stdout.strip(),
        'upstream_tag': json.loads((repo / '.codexy-upstream.json').read_text())['upstream_tag'],
        'system': platform.system(), 'machine': platform.machine(),
        'files': {name: hashlib.sha256((destination / name).read_bytes()).hexdigest() for name in names},
    }
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    package(*map(Path, sys.argv[1:]))
