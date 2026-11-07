from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import build_runtime


class NativeComponents(unittest.TestCase):
    def test_older_source_builds_pair_and_newer_source_also_builds_executor(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for directory, name in (
                ("cli", "codex-cli"),
                ("exec-server", "codex-exec-server"),
            ):
                (root / directory / "src").mkdir(parents=True)
                (root / directory / "Cargo.toml").write_text(
                    '[package]\nname = "' + name + '"\n'
                )
            with patch.object(build_runtime.subprocess, "run") as run:
                build_runtime.build(root)
                self.assertNotIn("codex-exec-server", run.call_args.args[0])
                (root / "exec-server/src/main.rs").write_text("fn main() {}")
                build_runtime.build(root)
                self.assertEqual(
                    run.call_args.args[0][-4:],
                    ["-p", "codex-exec-server", "--bin", "codex-exec-server"],
                )


class V8Environment(unittest.TestCase):
    def test_verified_pair_is_available_to_build_and_tests(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = Path(temp)
            (repo / "scripts/codex_package").mkdir(parents=True)
            (repo / "scripts/codex_package/v8.py").touch()
            env = {"PATH": "/tools", "CARGO_BUILD_JOBS": "1"}
            with patch.object(
                build_runtime.subprocess,
                "check_output",
                return_value='{"RUSTY_V8_ARCHIVE":"/cache/v8.a.gz","RUSTY_V8_SRC_BINDING_PATH":"/cache/binding.rs"}',
            ) as resolve:
                result = build_runtime.v8_env(repo, env)
            self.assertEqual(
                result,
                {
                    **env,
                    "RUSTY_V8_ARCHIVE": "/cache/v8.a.gz",
                    "RUSTY_V8_SRC_BINDING_PATH": "/cache/binding.rs",
                },
            )
            self.assertEqual(
                resolve.call_args.kwargs["env"]["CODEX_REPO_ROOT"], str(repo)
            )
            self.assertEqual(
                resolve.call_args.kwargs["env"]["PYTHONPATH"], str(repo / "scripts")
            )

    def test_older_candidate_without_helper_keeps_environment(self):
        with tempfile.TemporaryDirectory() as temp:
            env = {"CARGO_BUILD_JOBS": "1"}
            with patch.object(build_runtime.subprocess, "check_output") as resolve:
                self.assertEqual(build_runtime.v8_env(Path(temp), env), env)
            resolve.assert_not_called()
