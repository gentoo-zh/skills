# Derived Lesson Intake

Treat `gentoo-tree-lessons` as a secondary discovery index. Its records and synthesized
rules do not define Gentoo or gentoo-zh policy.

## Allowed Uses

Use the corpus only to:

- find a candidate `gentoo/gentoo` commit;
- discover a failure mode worth checking against current sources;
- select a real diff for a regression fixture;
- formulate a question for current PMS, Devmanual, eclass, tool, or repository policy.

Do not copy a corpus rule, statistic, threshold, or generated explanation into a hard
gate. Do not cite a short commit ID or corpus record as the final authority.

## Query the Corpus

Use an existing checkout through `GZH_LESSONS_DIR`, or let the helper create a cache
checkout with `--refresh`:

```bash
python3 <skill-root>/scripts/lesson_lookup.py --refresh --stats
python3 <skill-root>/scripts/lesson_lookup.py --search 'slot operator' --limit 5
python3 <skill-root>/scripts/lesson_lookup.py --topic deps-revbump
```

The helper adds a full Gentoo commit URL when the record contains a full SHA. A successful
lookup establishes only that the candidate exists.

## Verify a Candidate

1. Open the full `gentoo/gentoo` commit and inspect the actual diff, message, linked bug,
   parent state, and follow-up commits.
2. Check whether the change was reverted, superseded, or limited to one package or
   historical toolchain.
3. Compare the affected code with the current package, EAPI, eclass source, and build
   system.
4. Find the current primary source that states the general requirement. Prefer live
   overlay policy for repository workflow and current PMS, Devmanual, GLEP, eclass, or
   tool documentation for Gentoo semantics.
5. If no primary source generalizes the behavior, label the conclusion as package-specific
   precedent or inference. Do not make it a hard skill rule.
6. Add a deterministic fixture only for behavior that the local tool can observe without
   guessing runtime, ABI, licensing, or upstream intent.

Record the full primary URL, full commit URL when used, check date, affected scope,
classification, and local test. Check the source repository's license and preserve
provenance before importing any text, data, or code.

Reject evidence that exists only as a short SHA, model memory, an unexplained percentage,
an undocumented sample, an arbitrary size threshold, or an incident without a durable
primary link.
