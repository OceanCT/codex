# Codex Y

This fork keeps the original `codex` installation available. Build with
`CARGO_BUILD_JOBS=1 nice -n 10 cargo build --locked -p codex-cli -p codex-code-mode-host --bin codex --bin codex-code-mode-host`
in `codex-rs`, then run
`python3 scripts/install_codex_y.py` from the repository root.

Both binaries must come from the same source checkout and build profile. The
installer requires an executable `codex-code-mode-host` beside `codex` and
checks both before changing the installation. With `--binary`, place the matching
host beside the specified binary. The host executes code-mode tool calls;
opening the command center alone does not verify that those calls work.
After installation, run `python3 scripts/smoke_codex_y_host.py` to verify the
installed host's protocol handshake, JavaScript execution and shell callback.
This local check does not require a model request or account credentials.

If the host build cannot obtain its V8 archive, an explicit
`--code-mode-host /path/to/codex-code-mode-host` override can use an existing
host. The installer runs the smoke check before installing and records the
source in `runtime-components.json`. This checks the exercised IPC/tool path,
not full compatibility across releases; prefer a host built from the same
checkout when its dependencies are available. Do not disable V8 sandbox
features to work around a missing archive.

Run `codexy agents` for the command center, or `codexy` for a conversation.
The installer gives Y its own configuration, session database and daemon under
`~/.codex-y`. It copies model preferences and file-based authentication once.
Existing original sessions stay in `~/.codex`; importing them is a separate task.
If authentication needs refreshing, run `codexy login`.
The original `codex` command and installation remain untouched.

When resume overrides require rebuilding an indexed, idle thread, the server
persists its rollout before shutting down the old instance. This also covers
empty threads whose rollout files have not been created yet. If persistence
fails, the old instance stays loaded so the request can be retried.

## Recycle Bin

- Ctrl+K moves a task and its child agents to the bin without confirmation.
- Ctrl+B opens the bin; Enter restores a selected task and its newly archived children.
- Ctrl+E archives without confirmation. Ordinary archives do not expire.
- Bin entries expire after 30 days. Cleanup runs when the server handles a thread
  list refresh, including the first refresh after reopening Agents. It cannot run
  while the computer is off. No separate system scheduler is installed.
- If any member was restored, modified or is loaded, cleanup cancels expiry for
  that entry. Existing permanent-delete RPC behavior is unchanged.

The bin also accepts newly created tasks with no messages: it persists loaded
members before archiving them. Idle unload likewise persists empty conversations
before releasing their runtime. Storage failures keep the runtime available for
retry. Empty entries receive an `Empty task` display preview, and the bin reads
the archive index so message-based filesystem discovery does not hide them.
The label does not add a user message. These changes prevent future missing-rollout
errors; they cannot reconstruct an empty conversation that an older server already
discarded.

## Math and Images

The Y launcher enables native iTerm2 images with `CODEXY_RICH_MEDIA=1`.
Math inside `$...$`, `\(...\)`, `$$...$$`, or `\[...\]` is compiled by local
pdfLaTeX with AMS math, `mathtools`, `bm`, and `xcolor`, then rasterized to PNG.
Matrices, aligned equations, integrals and TeX math macros use the real TeX
engine. This is a math-fragment renderer, not an arbitrary document compiler.
The original Unicode renderer remains the fallback when dependencies are absent
or a fragment fails to compile. Math inside code fences stays literal.

Install TeX with `standalone`, AMS packages, `mathtools`, `bm`, `xcolor`, and
Poppler (`pdftocairo`, `pdfinfo`) on PATH. The installer bundles the Python
renderer beside `codex`; it does not download TeX packages automatically.

Local Markdown images, local user attachments and saved generated images display
through iTerm2 OSC 1337. Row-sized image slices preserve terminal scrolling and
clipping. Remote URLs remain labels; rendering does not fetch arbitrary URLs.
Native media is disabled outside iTerm2 and inside tmux/Zellij. Set
`CODEXY_RICH_MEDIA=0 codexy` to use text rendering.

Resource bounds: one compiler child at a time, three CPU seconds and five wall
seconds per TeX/raster process, 8 MiB output files, 1600-pixel raster bounds,
8-megapixel input decode limit, 20 displayed rows per image, and a per-process
128-entry / 24 MiB encoded-image cache. Width or theme changes may compile a new
cached variant. Compilation runs at low priority without shell escape; failed
results are cached too. Original Markdown/TeX remains in the session history.

Reference: https://iterm2.com/documentation-images.html

## Upstream Updates

The `rust-v0.159.2` upgrade (`ff6aec9694`) retained Y's recycle bin, empty-thread
persistence, and native math/image rendering. The upgrade baseline is preserved on
`backup/codex-y-before-0.159.2-20260930`.

For that upgrade, the Code Mode Host was the official `0.159.2` Apple Silicon
release component. Its source is unchanged by Y. Install it with the explicit
host override and run the installed-host smoke check described above.

Installing selects the new binaries but does not restart an active daemon.
Check `codexy app-server daemon version`; after active tasks finish, run
`codexy app-server daemon restart` and reopen the terminal client to use the
new runtime throughout.

Keep personal changes on `oceanct/agents-recycle-bin`. The default branch of
`OceanCT/codex` hosts the **Codexy upstream sync** GitHub Actions workflow. It
checks published stable releases once daily (01:17 UTC; manual retries are available), in version order. Compatible
candidates advance this branch only after the CLI/host build and focused Codexy
regressions pass. `.codexy-upstream.json` records the integrated upstream release.

Conflicts and failed validation leave the branch unchanged and produce an Issue
with affected features, evidence and run logs. Email to `oceanct17@gmail.com`
requires the repository's SMTP Secrets. See
[synchronization configuration](https://github.com/OceanCT/codex/blob/main/scripts/codexy_sync/README.md).

Source synchronization does not install local binaries or restart active tasks.
The local daemon package uses a `local-` directory, so it does not qualify for the
public stable-release updater.

## Validation

The 0.159.2 upgrade passed 4 protocol/schema checks, 92 selected TUI tests,
9 selected app-server lifecycle tests, and 4 real LaTeX renderer tests. Both
precomputed protocol bundles were regenerated. The native-image event consumer
was adapted to the upstream Markdown parser refactor. Repository formatting and
the Bazel lock update passed; the latter required no lockfile changes. The
official 0.159.2 Code Mode Host passed its handshake, JavaScript, shell callback,
and result-delivery smoke check. Cargo used one build job and Nextest ran one
test at a time; these are focused checks, not a full workspace test run.

The 0.157.1 upgrade passed 6 protocol/schema checks, 16 selected app-server
lifecycle tests, 92 selected TUI tests, and 4 real LaTeX renderer tests. Both
precomputed protocol bundles were regenerated after merging. Repository
formatting and the CLI build passed with one Cargo build job. The official
Code Mode Host archive matched the release SHA-256 and passed its protocol,
JavaScript, and shell-callback smoke check.

Empty-task lifecycle validation includes bin/restore across restart, idle unload
followed by resume (both history modes), and storage failure followed by retry.
The 22 selected app-server tests also cover ordinary archive/unarchive, existing
bin behavior, unsubscribe during a turn, and the earlier resume fix.

Native media validation: 34 focused TUI tests and four real LaTeX renderer tests
passed. An iTerm2 tab was used to inspect inline math, matrices, fractions,
integrals, aligned equations and a local PNG. Run the renderer checks with
`python3 scripts/test_render_codex_y_latex.py`.

Focused local checks passed with one Cargo build job and one test at a time:

- 4 app-server tests: restart/restore, expiry protection and the two lifecycle tests
  that previously timed out under concurrent load.
- 92 TUI tests: Agents, Unicode math and the selected earlier timeout cases.
- Targeted Clippy checks and repository formatting passed.
- The isolated launcher starts the command center and opens the bin with Ctrl+B.
  Its missing-binary fallback points to the original `codex` command.

The earlier whole-TUI run was interrupted for resource usage and is not a full
suite pass. Future local checks follow the resource budget in `AGENTS.md`.
