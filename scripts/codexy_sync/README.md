# Codexy local-only updates

All integration, compilation, tests and installation run on this Mac. GitHub is
used only to fetch upstream source and save validated commits. Never dispatch
Actions, use hosted runners, or fall back to cloud execution when local resources
are insufficient. This is an explicit user policy, not a temporary workaround.

`OceanCT/codex` Actions is disabled at repository level. The two legacy workflow
files also have unconditional false job guards. The shared API wrapper rejects
Actions mutations. The watcher has no remote-dispatch path; its legacy
`--dispatch` flag means execute the local pipeline.

## Execution

Install with `python3 scripts/codexy_sync/install_local_watch.py`.
The LaunchAgent `com.oceanct.codexy-upstream` starts at login and checks every
15 minutes. No process remains resident between checks unless local work is
running. `codexy-daemon run` invokes the same local pipeline immediately.

1. Reconcile any installed runtime and its Memory v2 transition.
2. Check disk space and other compiler processes. Require 20 GiB free and no
   competing Cargo/Rust compilation before starting heavy work. If blocked,
   retain a visible `waiting_disk` or `waiting_other_build` state; never use CI.
3. Fetch the target branch into the existing `~/codex` Git repository and create
   a separate disposable `local-candidate` worktree. Never change the user's
   working branch or discard edits in a candidate.
4. First bring the local binary up to the already-integrated target. Thereafter
   discover the next published stable upstream version at most once daily,
   merge with the pinned previous release as base, and preserve Codexy features.
5. Compile with one Cargo job, low process priority and the existing
   `~/codex/codex-rs/target` cache. Run the focused protocol, recycle-bin,
   empty-thread, provider, native-media and Code Mode Host regressions locally,
   with the single-test `local` Nextest profile. Logs are in `validation.log`.
6. Only a validated unchanged candidate may fast-forward the remote target.
   Reconcile an already-published candidate after interruption; never force push.
7. Package the locally built CLI, matching Code Mode Host and, when the source
   provides it, standalone executor. Verify exact source/version and file hashes,
   run isolated host/version probes, and atomically select the immutable package.
8. Use the existing daemon's SIGHUP graceful-only drain, after verifying its PID,
   start time and executable. Active turns finish; no forced kill or deadline is
   used. New turns are refused while draining. The next heartbeat starts the
   selected runtime if clients have not already reconnected.

Old packages remain available. A failed activation restores the previous selected
package and records `activation_failed`. A failed/interrupted validation or merge
conflict retains its worktree, report and logs for repair rather than repeatedly
rebuilding or pretending completion. Source publication, installed binaries and
running daemon versions are separate states.

Memory transitions respect explicit opt-outs/version choices. On a supported
running runtime, default existing v1 profiles enable dual-write, retain v1 reads,
and select v2 only after 20 consolidated threads and a valid v2 summary meet the
upstream readiness boundary. The controller never rewrites/deletes memory files
or manufactures chats. Config backups are private. New-session configuration
changes are distinct from existing sessions already using v1.

## State and commands

State is under `~/.local/share/codexy-upstream/state`. `local-pipeline.json` tracks
current progress; `local-report.json` and `.md` retain candidate evidence.
`local-pipeline.lock` prevents concurrent local update workflows. The installer
backs up and retires the old remote-dispatch records, preserving their history.

- `codexy-daemon status codexy-upstream`: current local progress and last executions.
- `codexy-daemon status --watch`: observe without starting extra work.
- `codexy-daemon history codexy-upstream`: compressed execution history.
- `codexy-daemon logs codexy-upstream <id>`: inspect one recorded execution.
- `codexy-daemon changes codexy-upstream`: project changes and state transitions.
- `codexy-daemon run`: advance locally with the same resource and validation gates.
- `codexy-daemon start` / `stop`: load/unload the watcher, not the user's Codex daemon.

A Mac that is asleep or powered off cannot build. Missing local dependencies,
insufficient disk and real merge conflicts remain visible unfinished work. There
is no hosted fallback and no automatic paid service enrollment.

## Verification

Run `python3 -m unittest discover -s scripts/codexy_sync -p 'test_*.py' -v` locally.
Temporary real Git repositories exercise merge/publication invariants; local
fixtures exercise staging, checksum failures and isolated executable probes.
Mocked build tests check state ordering and do not prove a Rust build succeeded.
Only the actual local native validation and installed runtime probes establish
that a particular new release is working.
