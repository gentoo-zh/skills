# Continuous Improvement

Detect source drift automatically, but change skill policy only after reviewing current
primary evidence. A changed fingerprint is a review request, not a new rule.

## Update Commands

Run the repository updater from its checkout:

```bash
./update.sh
./update.sh --installed-only
./update.sh --references
./update.sh --installed-only --references
```

- `./update.sh` refuses a dirty checkout or detached HEAD, resolves exactly one canonical
  `gentoo-zh/skills` remote by URL, fetches its `master`, performs a fast-forward-only
  merge, and refreshes managed installations.
- `--installed-only` skips the Git pull and refreshes only installations already managed
  by the installer.
- `--references` also audits registered source fingerprints with drift treated as a
  failure and reports the current lesson corpus statistics. It does not update policy or
  the source lock.

Run a focused source audit from the skill directory when a full installation update is
not needed:

```bash
python3 <skill-root>/scripts/source_manager.py audit
python3 <skill-root>/scripts/source_manager.py audit --topic eclass --fail-on-drift
```

Do not use `refresh-lock` as an update command. It writes reviewed fingerprints and must
run only after the review process below is complete.

## Review Source Drift

1. Capture the source ID, old fingerprint, observed fingerprint, URL, date, and retrieval
   error, if any.
2. Open the changed source and inspect the relevant diff or current section.
3. Classify the source according to [official-sources.md](official-sources.md).
4. Determine the exact affected behavior and scope. Do not infer a general rule from an
   editorial change, one package commit, GURU policy, or a derived corpus entry.
5. Update the smallest applicable instruction, script, and regression test.
6. Run repository validation and the relevant tests.
7. Forward-test a changed workflow with a fresh agent when the behavior is difficult to
   cover deterministically. Give it the skill and a realistic task, not the expected
   answer.
8. Review the resulting diff before recording new fingerprints:

   ```bash
   python3 <skill-root>/scripts/source_manager.py refresh-lock --id <source-id>
   ```

Never let a scheduled job rewrite skill instructions, update the source lock, reset a
checkout, or overwrite local changes.

## Review recent overlay evidence

After fetching the canonical overlay remote, inspect the complete recent commit range and
its follow-up commits. Group changes by policy, workflow, license, package type, and QA
failure rather than treating every package diff as a new rule. For each candidate:

1. read the full commit message, diff, parent state, linked evidence, and later fixes;
2. separate repository policy from package-specific precedent;
3. confirm a general rule in the live policy or official Gentoo documentation;
4. add or update a deterministic regression case only when the local tool can observe the
   pass and fail condition;
5. record statistics as dated evidence, never as a permanent threshold.

License corrections, `RESTRICT` changes, QA exceptions, installed file modes, desktop
integration, dependency changes, and autobump failures are useful review groups. A later
fix in the same group takes priority over the earlier commit.

## Acceptance Gate

Promote a finding to a hard instruction only when all of these conditions hold:

- a current primary source requires the behavior;
- the instruction states its repository, EAPI, eclass, package type, or tool scope;
- the behavior has an observable pass and fail condition;
- a regression test covers deterministic implementation behavior;
- the wording does not present repository precedent as PMS or Devmanual policy.

Keep an unconfirmed finding as a research note or candidate fixture. Remove stale rules
instead of retaining them for historical interest.

## Validation

Run at least the repository validator and relevant test suites after changing the skill:

```bash
python3 scripts/validate_repository.py
python3 -m pytest -q
```

Also run source audits when evidence metadata changes and installer integration tests when
installation or update behavior changes. Report every skipped check and its reason.
