#!/usr/bin/env python3
"""Install an already-built Codex Y on macOS/Linux alongside the original Codex."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path)
    parser.add_argument(
        "--code-mode-host",
        type=Path,
        help="Explicit compatible host override when a source build is unavailable",
    )
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    binary = args.binary or repo / "codex-rs/target/debug/codex"
    host = args.code_mode_host or binary.with_name("codex-code-mode-host")
    if host.name != "codex-code-mode-host":
        raise SystemExit("Host executable must be named codex-code-mode-host")
    for component in (binary, host):
        if not component.is_file() or not os.access(component, os.X_OK):
            raise SystemExit(
                f"Missing executable: {component}\n"
                "Build both runtime binaries in codex-rs with:\n"
                "CARGO_BUILD_JOBS=1 nice -n 10 cargo build --locked "
                "-p codex-cli -p codex-code-mode-host "
                "--bin codex --bin codex-code-mode-host"
            )
    subprocess.run([str(binary), "--version"], check=True)
    subprocess.run([str(host), "--help"], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(
        [os.sys.executable, str(repo / "scripts/smoke_codex_y_host.py"), str(host)],
        check=True,
        timeout=40,
    )
    user_home = Path.home()
    original = user_home / ".codex"
    isolated = user_home / ".codex-y"
    isolated.mkdir(mode=0o700, exist_ok=True)
    if isolated.resolve() == original.resolve():
        raise SystemExit("Codex Y requires its own data directory")
    revision = subprocess.check_output(
        ["git", "rev-parse", "--short", "HEAD"], cwd=repo, text=True
    ).strip()
    destination = user_home / ".local/share/codex-y" / f"local-{revision}" / "bin"
    destination.mkdir(parents=True, exist_ok=True)
    # Stage both components before replacing files used by the running daemon.
    with tempfile.TemporaryDirectory(prefix=".install-", dir=destination) as staging:
        for component in (host, binary):
            shutil.copy2(component, Path(staging) / component.name)
        for component in (host, binary):
            (Path(staging) / component.name).replace(destination / component.name)
    shutil.copy2(
        repo / "scripts/render_codex_y_latex.py",
        destination / "render_codex_y_latex.py",
    )
    (destination.parent / "runtime-components.json").write_text(
        json.dumps(
            {
                "codex_source": str(binary.resolve()),
                "code_mode_host_source": str(host.resolve()),
                "host_override": args.code_mode_host is not None,
                "source_revision": revision,
            },
            indent=2,
        )
        + "\n"
    )
    # Local release names deliberately do not qualify for the upstream auto-updater.
    package = isolated / "packages/app-server-daemon"
    package.mkdir(parents=True, exist_ok=True)
    selected = package / "current.new"
    selected.unlink(missing_ok=True)
    selected.symlink_to(destination.parent, target_is_directory=True)
    selected.replace(package / "current")
    config = isolated / "config.toml"
    if not config.exists():
        source = original / "config.toml"
        settings = tomllib.loads(source.read_text()) if source.exists() else {}
        # Carry over model preferences only; no shared databases, hooks or daemon paths.
        text = ""
        for key in ("model", "model_reasoning_effort", "service_tier", "personality"):
            if key in settings:
                text += f"{key} = {json.dumps(settings[key])}\n"
        text += '\n[tui.keymap.agents]\ndelete = "ctrl-k"\narchive = "ctrl-e"\n'
        text += "\n[tui.rendering]\nmath = true\ntables = true\nmermaid = true\n"
        config.write_text(text)
        config.chmod(0o600)
    auth = isolated / "auth.json"
    if not auth.exists() and (original / "auth.json").is_file():
        # Separate copies prevent token refresh or logout from modifying the original file.
        shutil.copyfile(original / "auth.json", auth)
        auth.chmod(0o600)
    launcher = user_home / ".local/bin/codexy"
    launcher.parent.mkdir(parents=True, exist_ok=True)
    if launcher.exists() and "Codex Y isolated launcher" not in launcher.read_text():
        raise SystemExit(f"Refusing to replace an unrelated command: {launcher}")
    launcher.write_text(
        "#!/usr/bin/env python3\n"
        '"""Codex Y isolated launcher."""\n'
        "import os, sys\nfrom pathlib import Path\n"
        "home = Path.home() / '.codex-y'\n"
        "if home.resolve() == (Path.home() / '.codex').resolve():\n"
        "    sys.exit('Codex Y data must be separate from .codex')\n"
        "binary = home / 'packages/app-server-daemon/current/bin/codex'\n"
        "env = dict(os.environ, CODEX_HOME=str(home))\n"
        "env.setdefault('CODEXY_RICH_MEDIA', '1')\n"
        "env['CODEXY_LATEX_RENDERER'] = str(binary.with_name('render_codex_y_latex.py'))\n"
        "for key in ('CODEX_MANAGED_BY_NPM', 'CODEX_MANAGED_BY_BUN',\n"
        "            'CODEX_DAEMON_SHUTDOWN_SOCKET', 'CODEX_DAEMON_SHUTDOWN_FILE'):\n"
        "    env.pop(key, None)\n"
        "if not binary.is_file():\n"
        "    sys.exit('Codex Y is unavailable. Use the original codex command.')\n"
        "host = binary.with_name('codex-code-mode-host')\n"
        "if not host.is_file() or not os.access(host, os.X_OK):\n"
        "    sys.exit('Codex Y is incomplete: reinstall with codex-code-mode-host.')\n"
        "os.execve(binary, ['codexy', *sys.argv[1:]], env)\n"
    )
    launcher.chmod(0o755)
    print(f"Installed: {launcher}\nData: {isolated}\nOriginal command: codex")


if __name__ == "__main__":
    main()
