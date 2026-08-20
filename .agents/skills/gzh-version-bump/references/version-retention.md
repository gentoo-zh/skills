# Keeping or Dropping the Old Version

A bump replaces the version it supersedes. `add NEW, drop OLD` is the default; keeping the
old version is the exception and needs one of the reasons below, recorded in the delivery
report.

Query its registered evidence route from the `gentoo-overlay-development` skill directory with
`python3 scripts/source_manager.py list --capability gentoo-zh-version-retention-review`.

## Reasons to keep the prior version

- A major version jump, large rewrite, or build-system migration. This applies to a `-bin`
  package and to a package whose history rolls the latest. Drop the retained version once
  the new branch has held.
- A reverse-dependency pin, `SLOT`, or profile entry still resolves against it. Check this
  overlay and the main Gentoo tree per `SLOT` and per retained architecture before
  dropping.
- The replacement is unverified: upstream skipped an architecture in this release, or a
  security fix has not reached every keyworded branch.

Being prebuilt is not a reason. A previous commit that kept a version is not a reason.
Follow the package's own history only where it shows an explicit retention pattern;
otherwise apply the rules above and state the choice.

## Otherwise drop

A package with no cross-version state, no reverse dependencies, and nothing worth
downgrading to keeps exactly one release version plus any live ebuild.

## Evidence before dropping

1. List the existing versions. `gzh drop-old --pkg <category/package>` orders candidates by
   version alone; it applies no policy reason, proves no compatibility, and never deletes a
   file.
2. Run `gzh deps reverse <category/package>` as a raw potential-consumer index, then
   confirm that each relevant consumer still resolves against a retained version under the
   required profile.
3. Confirm that the retained versions cover every required `SLOT` and every keyword the
   dropped ebuild carried, with no unintended downgrade.
4. Remove the ebuild together with its `Manifest` entries, unreferenced `files/` inputs,
   and metadata in the same commit. Keep every input a surviving ebuild still references.

## Stop conditions

- Dropping the old version would lose immutable source bytes that nothing else provides.
- The new release replaces the old one in place without an explanation.

## Autobump retention

`keep_old = N` in `.github/workflows/overlay.toml` applies this same policy to automatic
bumps. Set it only for a package that meets a reason above, and remove it when the reason
lapses. Change that file only when current repository policy and upstream evidence justify
it, and review the resulting diff.
