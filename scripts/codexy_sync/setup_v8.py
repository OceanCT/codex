"""Fetch the exact sandbox-enabled V8 artifacts pinned by the candidate source."""
import hashlib
import os
from pathlib import Path
import re
import sys
import tomllib
import urllib.request


def fetch(url, destination, expected=None):
    digest = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=60) as response, destination.open('wb') as output:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    if expected and digest.hexdigest() != expected:
        raise ValueError(f'Checksum mismatch: {destination.name}')
    return digest.hexdigest()


def main():
    repo, destination = map(Path, sys.argv[1:])
    lock = tomllib.loads((repo / 'codex-rs/Cargo.lock').read_text())
    versions = {p['version'] for p in lock['package'] if p['name'] == 'v8'}
    if len(versions) != 1:
        raise ValueError('Expected exactly one V8 version')
    version = versions.pop()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Unsupported V8 version')
    target = 'x86_64-unknown-linux-gnu'
    profile = 'ptrcomp_sandbox_release'
    manifest_name = f'rusty_v8_{profile}_{target}.sha256'
    archive = f'librusty_v8_{profile}_{target}.a.gz'
    binding = f'src_binding_{profile}_{target}.rs'
    trust_file = repo / f"third_party/v8/rusty_v8_{version.replace('.', '_')}_release_manifests.sha256"
    trusted = dict((name.lstrip('*'), sha) for sha, name in (line.split() for line in trust_file.read_text().splitlines() if line.strip()))
    destination.mkdir(parents=True, exist_ok=True)
    root = f'https://github.com/openai/codex/releases/download/rusty-v8-v{version}/'
    manifest = destination / manifest_name
    fetch(root + manifest_name, manifest, trusted[manifest_name])
    entries = [line.split() for line in manifest.read_text().splitlines() if line.strip()]
    if len(entries) != 2 or {name.lstrip('*') for _, name in entries} != {archive, binding}:
        raise ValueError('Unexpected V8 artifact manifest')
    for checksum, name in entries:
        name = name.lstrip('*')
        fetch(root + name, destination / name, checksum)
    with open(os.environ['GITHUB_ENV'], 'a') as output:
        output.write(f'RUSTY_V8_ARCHIVE={destination / archive}\n')
        output.write(f'RUSTY_V8_SRC_BINDING_PATH={destination / binding}\n')
    print(f'Verified V8 {version} archive and bindings for {target}')


if __name__ == '__main__':
    main()
