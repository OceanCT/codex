"""Build paired runtime components, including the newer standalone executor."""
from pathlib import Path
import subprocess
import tomllib


def build(root):
    command = ['cargo', 'build', '--locked', '-p', 'codex-cli', '-p', 'codex-code-mode-host', '--bin', 'codex', '--bin', 'codex-code-mode-host']
    for directory in ('cli', 'exec-server'):
        manifest = tomllib.loads((root / directory / 'Cargo.toml').read_text())
        bins = {entry['name'] for entry in manifest.get('bin', [])}
        if directory == 'exec-server' and (root / directory / 'src/main.rs').is_file() and manifest['package'].get('autobins', True):
            bins.add('codex-exec-server')
        if 'codex-exec-server' in bins:
            command += ['-p', manifest['package']['name'], '--bin', 'codex-exec-server']
            break
    subprocess.run(command, cwd=root, check=True)


if __name__ == '__main__':
    build(Path.cwd())
