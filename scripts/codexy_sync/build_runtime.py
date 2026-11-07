"""Build paired runtime components, including the newer standalone executor."""

import json
from pathlib import Path
import subprocess
import sys
import tomllib


def v8_env(repo, env):
    """Use the candidate's pinned, checksum-verified V8 pair for build AND tests."""
    if not (repo / "scripts/codex_package/v8.py").is_file():
        return env
    script = """
import json, subprocess
from pathlib import Path
from codex_package.targets import TARGET_SPECS
from codex_package.v8 import resolve_codex_v8_cargo_env
host = next(line.removeprefix('host: ') for line in
            subprocess.check_output(['rustc', '-vV'], text=True).splitlines()
            if line.startswith('host: '))
print(json.dumps(resolve_codex_v8_cargo_env(
    TARGET_SPECS[host], cache_root=Path.home()/'.cache/codexy-v8')))
"""
    probe_env = dict(env, CODEX_REPO_ROOT=str(repo), PYTHONPATH=str(repo / "scripts"))
    overrides = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", script], cwd=repo, env=probe_env, text=True
        )
    )
    return {**env, **overrides}


def build(root):
    command = [
        "cargo",
        "build",
        "--locked",
        "-p",
        "codex-cli",
        "-p",
        "codex-code-mode-host",
        "--bin",
        "codex",
        "--bin",
        "codex-code-mode-host",
    ]
    for directory in ("cli", "exec-server"):
        manifest = tomllib.loads((root / directory / "Cargo.toml").read_text())
        bins = {entry["name"] for entry in manifest.get("bin", [])}
        if (
            directory == "exec-server"
            and (root / directory / "src/main.rs").is_file()
            and manifest["package"].get("autobins", True)
        ):
            bins.add("codex-exec-server")
        if "codex-exec-server" in bins:
            command += ["-p", manifest["package"]["name"], "--bin", "codex-exec-server"]
            break
    subprocess.run(command, cwd=root, check=True)


if __name__ == "__main__":
    build(Path.cwd())
