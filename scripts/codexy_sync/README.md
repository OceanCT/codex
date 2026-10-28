# Codexy upstream synchronization

The controller lives on `OceanCT/codex`'s default branch. It checks published
stable `openai/codex` releases once daily (01:17 UTC / 09:17 China time) and can be
started manually in Actions. GitHub may delay scheduled runs. An upstream
repository's release does not trigger this fork's `release` event.

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
Reports expire after seven days; Issues retain the reasons. Scheduled checks can
be delayed by GitHub; integration latency is up to a day plus queue/build time.

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

## Notification

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
