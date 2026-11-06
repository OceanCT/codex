#!/usr/bin/env bash
# Run inside the disposable candidate checkout, with no repository write token.
set -euo pipefail
export CARGO_BUILD_JOBS="${CARGO_BUILD_JOBS:-1}"
for tool in just cargo pdflatex pdftocairo pdfinfo; do command -v "$tool" >/dev/null; done
python3 scripts/test_render_codex_y_latex.py
runtime_builder="$(cd "$(dirname "$0")" && pwd)/build_runtime.py"
cd codex-rs
python3 "$runtime_builder"
# Separate filters fail on an empty selection rather than silently skipping a feature.
just test --locked -p codex-app-server-protocol schema_fixtures_tests
just test --locked -p codex-app-server thread_trash
just test --locked -p codex-app-server thread_empty_lifecycle
just test --locked -p codex-app-server thread_resume_empty
just test --locked -p codex-tui agents_overview
just test --locked -p codex-tui model_picker_profiles
just test --locked -p codex-tui provider_selection
just test --locked -p codex-tui rollout_history
just test --locked -p codex-tui image_generation
just test --locked -p codex-tui rich_media
just test --locked -p codex-tui agents_opens_unloaded_history_on_the_cli_stack
python3 ../scripts/smoke_codex_y_host.py "${CARGO_TARGET_DIR:-target}/debug/codex-code-mode-host"
