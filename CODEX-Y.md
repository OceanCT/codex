# Codex Y

This fork keeps the original `codex` installation available. Build with
`CARGO_BUILD_JOBS=1 nice -n 10 cargo build -p codex-cli --bin codex`
in `codex-rs`, then run
`python3 scripts/install_codex_y.py` from the repository root.

Run `codex-y agents` for the command center, or `codex-y` for a conversation.
The installer gives Y its own configuration, session database and daemon under
`~/.codex-y`. It copies model preferences and file-based authentication once.
Existing original sessions stay in `~/.codex`; importing them is a separate task.
If authentication needs refreshing, run `codex-y login`.
The original `codex` command and installation remain untouched.

## Recycle Bin

- Ctrl+K moves a task and its child agents to the bin without confirmation.
- Ctrl+B opens the bin; Enter restores a selected task and its newly archived children.
- Ctrl+E archives without confirmation. Ordinary archives do not expire.
- Bin entries expire after 30 days. Cleanup runs when the server handles a thread
  list refresh, including the first refresh after reopening Agents. It cannot run
  while the computer is off. No separate system scheduler is installed.
- If any member was restored, modified or is loaded, cleanup cancels expiry for
  that entry. Existing permanent-delete RPC behavior is unchanged.

## Math and Images

The upstream renderer already enables Unicode math, including a bounded subset
of TeX fractions, superscripts and structured expressions. Unsupported TeX stays
as source. This is terminal text layout, not a complete LaTeX engine.

iTerm2 already supports inline images through OSC 1337 and, in current versions,
Kitty graphics. Codex's pet renderer uses graphics protocols, but conversation
images need separate layout, scrolling and redraw integration. This change does
not yet render conversation images or full LaTeX as graphics.

Reference: https://iterm2.com/documentation-images.html

## Upstream Updates

Keep personal changes on `oceanct/agents-recycle-bin`. Review upstream releases
and commits before merging. The local daemon package uses a `local-` directory
so it does not qualify for the public stable-release updater. Automated upstream
checks are not configured yet.

## Validation

Focused local checks passed with one Cargo build job and one test at a time:

- 4 app-server tests: restart/restore, expiry protection and the two lifecycle tests
  that previously timed out under concurrent load.
- 92 TUI tests: Agents, Unicode math and the selected earlier timeout cases.
- Targeted Clippy checks and repository formatting passed.
- The isolated launcher starts the command center and opens the bin with Ctrl+B.
  Its missing-binary fallback points to the original `codex` command.

The earlier whole-TUI run was interrupted for resource usage and is not a full
suite pass. Future local checks follow the resource budget in `AGENTS.md`.
