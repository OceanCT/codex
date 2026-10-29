# Codexy upstream synchronization

The controller lives on `OceanCT/codex`'s default branch. Release discovery runs
on the user's Mac, not on an Actions cron. Install with
`python3 scripts/codexy_sync/install_local_watch.py`. The launchd service wakes
every 15 minutes, but checks for new stable releases only when 24 hours have
elapsed since the last successful check. Idle wakeups only read local SQLite.
An unfinished update takes priority over the daily interval. Sleep/offline time
is caught up after the Mac wakes and the service runs again.

The target is `oceanct/agents-recycle-bin`. `.codexy-upstream.json` on that
branch pins the last integrated release and commit. Each run selects the next
published stable version numerically; alpha/draft/unpublished tags are skipped.
A blocked version is never marked integrated or silently skipped. Subsequent
releases wait for it. Run manually with `retry=true` after resolving the blocker. `dry_run` still writes
conflict reports and attempts notification, but never builds or publishes.

## Resource limits

A sparse controller checkout checks the target SHA and next release before fetching
candidate history. Blocked attempts are recorded on their Issues, keyed by target
SHA, release metadata, policy, integration/validation scripts and workflow. The
same attempt is skipped even if its Issue is closed; a changed key or explicit
`retry=true` permits another evaluation. No repeated builds or mail attempts occur
for an unchanged blocked version. Configuring SMTP alone does not trigger a retry.
Reports expire after seven days; Issues retain the reasons. Discovery latency is
up to a day plus local wakeup, queue and build time. The Mac must be logged in
and online. GitHub workflow_dispatch remains available for explicit retries.

A maintainer can reconcile textual conflicts on a separate branch and dispatch
with `reviewed_candidate=<full commit SHA>`. The controller checks target ancestry,
the published stable release pin, upstream ancestry and required feature files,
then runs the same build/regression/publication gate. This does not authorize
publishing unvalidated code or overwriting a concurrently advanced target.

## Decision and validation

- Fixes, additions and changes all enter the same compatibility gate. Release
  note headings describe the change; a `fix` label does not bypass validation.
- Three-way merging uses the pinned previous release as the base, avoiding false
  conflicts caused by backports on upstream release branches.
- Independent bare Rust `mod` insertions at the same empty base hunk are
  combined without discarding either module, then still require validation.
- Other merge conflicts produce a report with paths, affected Codexy features,
  official release notes and a link to the Action. Codexy is not overwritten.
- Missing required feature files stop for review. Provider selection is part of
  the published feature registry and behavioral regressions, including switching
  providers and recovering the selection after a server restart.
- Sharing a source file is not automatically a conflict. A clean merge must
  build the CLI and matching Code Mode Host, then pass the existing recycle-bin,
  empty-thread lifecycle, native-media, Agents stack, renderer and host protocol
  regressions. Failures block publication, including missing tools or build
  dependencies. Infrastructure failures are not called confirmed conflicts.
- `.github/workflows/` remains fork-owned: upstream release/build automation is
  not product behavior and is not imported by automatic updates. This also keeps
  normal publishing within the built-in token's contents permission. Git push
  rejection details are included in publication-failure reports.
- Only an unchanged validated candidate can advance the target, using a normal
  fast-forward push. Concurrent target updates reject publication and require a
  fresh run. Validation happens within this workflow; it does not rely on another
  workflow being triggered by a `GITHUB_TOKEN` push.

These checks cannot prove absence of every semantic conflict. Expand the feature
registry and behavioral regression coverage when adding Codexy-specific behavior.
The controller must remain separate from upstream-controlled candidate code.
The candidate build receives no repository token or SMTP credentials.

## Local continuation state

State lives in `~/.local/share/codexy-upstream/state/releases.sqlite3`; health and
logs are alongside it. The service is `com.oceanct.codexy-upstream`. A release is
persisted before dispatch, so concurrent wakeups and restarts cannot dispatch it
twice. Failed discovery does not advance the daily timestamp. A dispatch timeout
is reconciled against the published version and matching run title, never blindly
resent. Only publication of the requested version completes a task.

Queued/running jobs are followed on subsequent wakeups. Failed or successful-but-
unpublished runs remain `needs_repair`, with the run URL; this watcher does not yet
implement autonomous code repair. Ambiguous dispatches remain pending until a
unique run or published version is found. These are unfinished tasks, not success.
Do not delete their database records without checking GitHub for an existing run.

## Notification

Operational failures and textual merge conflicts create diagnostic Issues without
email. Only `functional_conflict` reports with the affected feature, upstream
change, incompatibility, attempted resolution and concrete evidence send email.
The watcher itself does not infer or manufacture these findings.

Enable repository Issues for durable conflict reports. Configure these Actions
secrets: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM`.
Port 465 uses implicit TLS; other ports require STARTTLS. The fixed recipient is
`oceanct17@gmail.com`. Credentials never appear in reports or stdout.

A sent marker is saved on the issue only after SMTP accepts the message; repeated
runs do not resend identical reports. Missing SMTP configuration produces an explicit warning, not a failed sync job.
Configured SMTP delivery errors still fail visibly; retry manually after repair. SMTP acceptance is not proof of inbox delivery. A
crash after sending but before saving the marker can cause a duplicate delivery.
Without SMTP secrets, reports still exist but email is **not configured**.

## Local verification

Run `python3 -m unittest discover -s scripts/codexy_sync -p 'test_*.py' -v`.
Tests use temporary real Git repositories and bare remotes, including actual
conflicts, version ordering, clean merges, fast-forward publication and concurrent
writers. SMTP tests use a mock and do not establish real mail delivery.

For a read-only evaluation, create a clean disposable worktree of the target and
run `python3 /path/to/controller/scripts/codexy_sync/sync.py prepare --repo
/path/to/candidate --report /tmp/report.json`. A conflict does not mutate the
candidate. A clean result creates a detached candidate commit, never a remote
push. JSON and Markdown reports contain the full evidence.

GitHub source updates do not install binaries on the user's Mac or restart a live
Codexy daemon. Local installation remains the explicit workflow in `CODEX-Y.md`.

## Daemon CLI and compressed execution history

The installer also installs `~/.local/bin/codexy-daemon`:

- `codexy-daemon status`: service state, last successful discovery, next due time,
  pending updates and each task's latest execution.
- `codexy-daemon status --watch`: refresh the local view every two seconds; Ctrl-C
  exits the viewer without stopping the service or triggering GitHub calls.
- `codexy-daemon projects`: list long-lived projects (`tasks` is an alias).
  `codexy-upstream` owns the discovery and release-update executions.
- `codexy-daemon status codexy-upstream`: project goal, schedule, pending work
  and recent executions.
- `codexy-daemon history codexy-upstream`: all executions in this project.
- `codexy-daemon changes codexy-upstream`: configuration/install changes and
  update state transitions, with commit or execution references.
- `codexy-daemon logs codexy-upstream 12`: details of execution 12, checked
  against its owning project. Existing `show 12` remains supported.
- `codexy-daemon history --limit 30`: execution index; optionally filter with
  `--task release-check` or `--task release:rust-vX.Y.Z`.
- `codexy-daemon show 12`: decompress and inspect execution 12, including result,
  captured output and exception details.
- `codexy-daemon run`: check now, still respecting the daily gate and existing
  unfinished task. `start`/`stop` load/unload this launchd service only.

`state/history.sqlite3` stores the searchable index and gzip-compressed JSON
payloads atomically. Start time, finish time, status, task and PID stay queryable
without decompressing history. No history is automatically deleted. Previously
available last-check and log files are imported losslessly as `legacy` records;
old records overwritten before this feature existed cannot be reconstructed.
The daemon's normal output is stored in the compressed history rather than an
unbounded plain-text stdout log. Startup failures before history initialization
can still appear in `logs/stderr.log`.

## Startup and idle resource use

The LaunchAgent is explicitly enabled and starts when this user logs in after
boot. It does not run before login or while the Mac is powered off. launchd owns
the timer; no Python process remains resident between checks. Background process
priority, nice level 10 and low-priority I/O reduce interference with interactive
work. There is no KeepAlive busy-restart loop.

Scheduled idle wakeups use a read-only daily/pending-task check and return without
network access, new execution records, compression or health-file writes. Manual
`run` still records the attempt. History inspection uses read-only SQLite access;
compressed payloads are only decompressed by `show`. The interactive status viewer
exists only while the user runs it and stops on Ctrl-C.

Project migration keeps execution IDs and gzip payloads unchanged and creates
`state/history.before-projects.sqlite3` before the first installed migration.
Imported legacy data belongs to the existing project; it is never listed as a
separate project. Changelogs start when this feature is installed; earlier code
changes are not inferred from execution logs. Repeated observations of the same
release state are retained as executions but do not duplicate changelog entries.
