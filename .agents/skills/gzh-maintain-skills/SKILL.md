---
name: gzh-maintain-skills
description: Audit, repair, and evolve this Gentoo overlay development skills repository from current evidence. Use for scheduled or user-requested skill maintenance, official source drift, failed repository or installer CI, cross-client compatibility changes, overlay policy changes, repository-adapter development, regression intake, source-lock review, eval expansion, repository release readiness, version tags, GitHub releases, and one bounded unattended improvement iteration. Do not use to perform a package bump, publish an unrelated package, or modify an external repository directly.
---

# Maintain Gentoo Overlay Development Skills

Keep the repository current without turning source drift or isolated package history into
unsupported policy. Prefer deterministic checks and the smallest evidence-backed change.

## Establish the Contract

1. Read this repository's `AGENTS.md` and the live instructions for every target repository
   in scope.
2. Read [maintenance-contract.md](references/maintenance-contract.md).
3. Read [overlay-architecture.md](references/overlay-architecture.md) when changing target
   scope, repository discovery, or shared Gentoo behavior.
4. Treat official Gentoo sources as authoritative for portable behavior and each target
   repository's live policy as authoritative for its local workflow. Treat other overlays
   and derived corpora as comparative candidate evidence only.
5. Keep skill instructions, prompts, code, comments, logs, and notes in English. Apply the
   repository's Chinese writing skill only to the Simplified Chinese README or an allowed
   Chinese pull request body example.
6. Confirm the current Git branch, canonical remote by URL, clean state, and exact
   synchronization before the first edit. Preserve unrelated changes.

## Run a Baseline Cycle

Run the deterministic collector from the repository root:

```bash
python3 .agents/skills/gzh-maintain-skills/scripts/maintenance_cycle.py \
  --fetch --require-synced-master --output /tmp/gzh-maintenance.json \
  --markdown-output /tmp/gzh-maintenance.md
```

The collector fetches only the canonical `gentoo-zh/skills` remote, records synchronization
state, audits every registered source against its reviewed lock, refreshes the secondary
lesson checkout, validates every skill and the source-only release contract, runs static
evals, the test suite, and the compile check, then checks the diff. It never rewrites
instructions, source locks, Git history, or user configuration.

Audit the source inventory directly when classifying drift:

```bash
python3 .agents/skills/gentoo-overlay-development/scripts/source_manager.py \
  audit --all-scopes --fail-on-drift
```

Use an explicit `--scope`, `--all-scopes`, or `--id` for every list or audit. Refresh
requires one or more explicit `--id` values whose complete current diff was reviewed.

Stop on an incomplete queue, retrieval error, ambiguous remote, failed validator, failed
test, or repository-state conflict. A clean cycle with no new evidence is a valid no-op;
do not invent work to force an iteration.

## Use the Durable Evidence Path

Use the bundled tools according to ownership:

- Run `gentoo-overlay-development/scripts/qa_runner.py` for bounded, read-only package QA.
- Run `gentoo-overlay-development/scripts/dependency_analyzer.py` for extracted EAPI
  dependency metadata; never source an ebuild to obtain it.
- Run `qa_style_collector.py` with an immutable `--after-revision`, explicit adapter and
  canonical repository identity, and `--audit-sources` for complete history collection.
- Ingest only complete producer reports through `maintenance_queue.py`; do not insert a
  hand-written success record into `evidence_store.py`.
- Use `state_bundle.py` only with authenticated workflow run and job metadata. A workflow
  conclusion may be failed only when the seal and state-preservation steps completed.

The scheduled workflow restores the newest authenticated state, resumes a matching
incomplete plan before using a newer cursor for a new plan, runs an allowlisted queue, and
advances a repository cursor only in the final producer task. It stores exact payload hashes, bounded
output, retry state, producer artifacts, observations, and candidate transitions. Two
equivalent failures block the task; no task receives more than three attempts.

Candidate history never establishes policy. Move a candidate through `candidate` and
`reviewed`, link separately reviewed primary evidence, complete every promotion field,
then permit `promoted`. Compaction may remove routine run bodies and compress old discovery
reports, but it must preserve original hashes, normalized provenance, review links,
transitions, promoted evidence, and the latest cursor. A backlog or database ceiling is a
hard stop, not permission to discard evidence.

## Select One Iteration

Prioritize candidates in this order:

1. a failing GitHub Actions job or deterministic regression;
2. changed target-repository policy, workflow, template, or repository-owned documentation;
3. changed official Gentoo specification, Devmanual, eclass, or tool behavior;
4. changed client discovery or skill format;
5. a reusable Gentoo behavior still embedded in one repository adapter;
6. repeated failures confirmed by primary evidence;
7. another overlay or derived-corpus observation that still requires confirmation.

Choose one coherent behavior boundary. Record its old evidence, current evidence, affected
behavior, scope, and observable pass/fail condition. Inspect the full source diff and later
follow-ups. Extract only genuinely portable Gentoo behavior into the shared core. Keep
repository names, remotes, branches, keywords, CI gates, templates, and publication rules
in an adapter. Do not generalize one package commit, copy another repository's policy, or
infer intent from a fingerprint.

## Implement and Prove

1. Change the smallest applicable skill, reference, deterministic helper, and regression
   case. Keep detailed material in references and keep `SKILL.md` concise.
2. Add an activation or exclusion eval when trigger scope changes.
3. Add a deterministic test when code or a safety boundary changes.
4. Run the focused test that exposes the defect, then run the complete maintenance cycle
   without `--fetch` against the candidate worktree.
5. Run an isolated workflow evaluation when deterministic tests cannot cover instruction
   behavior. Supply the skill and a realistic task without the expected answer.
6. Require an independent review for high-risk installer, Git, publication, state, or
   concurrency changes when parallel review is available.

Keep the shared skill format at the documented common denominator: `name` and a
trigger-complete `description` in `SKILL.md`, with client-specific OpenAI interface data
in `agents/openai.yaml`. Keep every `SKILL.md` below 500 lines, link each reference
directly from its owning skill, and move detailed or variant-specific material into one
level of references. Do not add context merely to resemble a large skill repository.

Run the bounded eval surface after instruction or trigger changes:

```bash
python3 scripts/eval_runner.py static
```

Use the external JSON protocol only for an explicitly configured isolated runner. Give
it the skill snapshot and task, not the expected answer or a prior diagnosis.

Refresh only the fingerprints whose current content was reviewed, and do so after the
behavioral diff is complete:

```bash
python3 .agents/skills/gentoo-overlay-development/scripts/source_manager.py \
  refresh-lock --id <reviewed-source-id>
```

Run the complete cycle again after refreshing a lock. All registered sources must be
`current`; a source error is not approval to retain or replace a rule.

## Publish Within Authority

Review the complete diff, test output, source states, commit message, and file list. Commit
and push only when the current user authorization covers this repository and branch.
Never create or edit an overlay pull request under this skill.

After a push, verify the remote SHA and watch every GitHub Actions job to completion. Read
failing logs before changing code. Apply at most three attempts to one failed gate, and stop
after the identical failure repeats twice.

For a repository release, follow the root `RELEASING.md` only after the user explicitly
authorizes publication. Run `scripts/release_check.py` for the exact mode and tag. Never
infer a license or attach custom package artifacts while the release check rejects them.

Report the branch, canonical remote and fetch result, base and synchronization state,
changed files, reviewed evidence, commands and outcomes, skipped checks, remaining risks,
commit SHA, remote SHA, and GitHub Actions result.
