---
name: gzh-bump-from-issues
description: "Triage and process multiple nvchecker bump-reminder issues for the gentoo-zh overlay. Use for the open bump queue, maintainer-filtered batch work, issue comments, persistent skip decisions, or several package bumps that must be evaluated together. Delegate each viable package to gzh-version-bump and stop at local commits. Do not use for a single ordinary bump, unrelated issues, automatic pushes, or automatic pull requests."
---

# Process the gentoo-zh bump issue queue

Coordinate queue discovery and one-package bump workflows. Produce evidence-based triage records, verified local commits for viable packages, and a batch summary. Do not push branches or create or edit PRs.

## Start from current policy and complete issue data

1. Run `gzh repo` and read the complete live overlay `AGENTS.md`. Its repository, CI, commit, and PR rules override this skill and all references.
   If `gzh` is unavailable, install it from a checked-out `gentoo-zh/skills` root with `./install.sh --gzh-only`. Then run inside the overlay or set `GZH_OVERLAY_DIR` to its absolute path.
2. Use the evidence order required by `gzh-version-bump`: current repository policy,
   official Gentoo sources and tools, and upstream primary evidence before GURU practice
   or derived cases.
3. Load each issue's complete body and comments before deciding whether to process, skip, or escalate it. Do not decide from the title, a keyword, version distance, package type, or maintainer field alone.
4. Apply the complete `gzh-version-bump` workflow to every viable package. Batch processing never weakens its evidence, validation, commit, retry, or PR confirmation requirements.

## Preserve package and branch boundaries

- Use testing keywords only and do not add AI attribution.
- Use one package, topic branch, and commit by default. Combine packages only under an exception in the live overlay `AGENTS.md`, such as a real dependency chain, and keep one commit per package in dependency order.
- Preserve unrelated work. Resolve the canonical remote and current GitHub account dynamically.
- Make at most three total attempts at a failed operation. Stop earlier when the identical error is observed for the second time, then report the failed step and evidence.

## Process the queue

### 1. Discover work

1. Run `gzh bump-issues --limit 1000`, adding its supported maintainer or package filters when requested. Use `--autobump any|off|on|manual-required` for the requested scope and repeat `--issue <number>` for explicitly included issues. Non-`any` selection binds to a fetched canonical remote, reads `.github/workflows/overlay.toml` at its exact base OID, and stores typed configuration evidence. `manual-required` additionally requires the current repository-owned status marker, expected bot identity, exact issue revision, and complete comments. Require `truncated` to be false before claiming the queue is complete; otherwise report the uncovered count and stop batch-wide conclusions.
2. Run `gzh triage list --kind skip` and `gzh triage list --kind escalate`. Records are exact to issue, package, and target version. Exclude a current item only when its `updated_at` exactly equals the record's `issue_updated_at`. For a legacy record without `issue_updated_at`, use `recorded_at` only as a fallback and reassess when the issue is newer; legacy `skipped_at` is exposed as `recorded_at`. Otherwise reassess the complete issue and supersede obsolete state with `gzh triage resolve` or a new skip/escalation event. Pass the complete issue snapshot's `updated_at` as `--issue-updated-at` and the listed record's `event_id` as `--expected-event-id`; use `none` only when no record exists. The command checks the live GitHub revision before every write and rechecks an active event afterward. A revision or local-state conflict means the evidence changed, so reload the complete issue and triage log instead of overwriting it.
3. Keep the issue body and all paginated comments with each remaining queue item. Require `comments_truncated` to be false before classifying that item; stop the item and retrieve the missing comments when it is true.
4. Read prior reports under `$(gzh state-dir)/batches/` and inspect matching topic branches and commits. Reconstruct the selected set from the versioned snapshot instead of reparsing issue prose. Verify every referenced executor evidence digest and report a missing or changed artifact as incomplete. Resume owned work when its base and state are unambiguous; do not create duplicate branches or commits for an open issue.

### 2. Triage each issue

Classify each item as:

- **Process:** The package is in scope, the requested release is supported by current evidence, and no maintainer or upstream fact blocks the bump.
- **Skip:** Current issue evidence or repository policy establishes that this queue item should not be attempted. Record the concrete reason with `gzh triage skip --issue-updated-at <updated_at> --expected-event-id <event_id-or-none>`.
- **Escalate:** A maintainer decision, generated metadata, unpublished artifact, unclear license, repository conflict, or other required evidence is missing. Report what is needed. Use `gzh triage skip --kind escalate --issue-updated-at <updated_at> --expected-event-id <event_id-or-none>` only for an intentional revisitable deferral, not a transient failure.

Treat comments as evidence to interpret in context. A warning or disagreement may block
the bump, narrow its scope, or require verification, but no keyword is an automatic
decision rule. Delegate package-specific evidence gaps to `gzh-version-bump`; its
escalation classes do not replace reading the package and issue.

### 3. Process viable packages

For each **Process** item:

1. Process packages sequentially in one checkout. When delegates run concurrently, give each package a separate Git worktree and topic branch; never let concurrent agents switch branches in one worktree.
2. Start each independent package from the freshly fetched canonical `master`, or resume its unambiguous existing topic branch. Never base the next package on the preceding package branch.
3. Invoke `gzh-version-bump` and follow its package-specific work and finish pipeline through `gzh commit` and `gzh urls`.
4. Stop at the locally committed and network-checked branch. Do not push and do not create or edit a PR.
5. On failure, record the package, branch, failed command or phase, error, and attempts. Do not convert an attempted bump into a persistent skip merely because a command failed.
6. Continue to another independent queue item only when doing so preserves the retry and branch-isolation rules.

### 4. Report the batch

Render a structured JSON report and pipe it to `gzh batch-report create --format json`.
The command exclusively reserves a path under `$(gzh state-dir)/batches/` named
`bump-batch-YYYYMMDDTHHMMSSZ-<8-hex-run-id>.json` and returns JSON containing `path` and
`sha256`. Keep a concise English Markdown summary in a report field when a human-readable
batch narrative is useful. Include:

- batch scope, source queue path, queue total, fetched count, and truncation state;
- canonical remote, fetch result, base commit, and synchronization state;
- successful local commits under `items`: stable item ID, issue, package, version, branch,
  full commit, commit time, changed files, gate results, executor evidence path and digest;
- failures: issue, package, branch, failed step, exact error, attempts, and retained work state;
- skips or escalations: issue, package, evidence-based reason, and whether a persistent triage record was written;
- checks skipped for environmental reasons, warnings, and residual risk.

Keep reports and triage state outside the overlay worktree. A successful local commit does
not create a triage record; its report and existing branch provide the resume evidence.

Create the report before processing the first item. Only the coordinating agent may write
it; delegates return structured evidence to that agent. After every classification,
failed attempt, gate result, commit, and network result, render the complete updated JSON
report and pipe it to `gzh batch-report checkpoint <report-path> --expected-sha256 <sha256>`.
Carry forward the new hash returned by each successful checkpoint. A stale hash stops
instead of losing another result, and a failed replacement retains the prior complete
file. Do not rewrite the report directly. On an interrupted run, inspect the report,
queue snapshot, branch, commit range, and worktree before rerunning gates or resuming; do
not infer success from the branch name alone.

Send an optional notification with `gzh notify telegram` only when its credentials are already configured and the user requested or established that notification behavior. A batch result is not authorization to publish any branch or PR.

## Publication boundary

This skill stops at local commits. Do not push branches or create or edit pull requests.
Read [publication-reconciliation.md](references/publication-reconciliation.md) only when
the user later asks to publish successful packages or reconcile a batch report after
publication.

## Exclusions

- A single ordinary package bump; use `gzh-version-bump`
- Issues outside the gentoo-zh nvchecker bump queue
- New package creation, stabilization, or Gentoo main-tree work
- Automatic push, PR creation, or PR editing
- Treating transient command failures as permanent skip decisions
