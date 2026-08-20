# Upstream Version Discovery

Version discovery produces a candidate, not an approved bump target. Verify the result
against upstream primary material, Gentoo version rules, the live overlay policy, and the
package history before creating an ebuild.

## Current `gzh latest` behavior

`gzh latest <category/package>` currently:

1. Reads the package entry from `.github/workflows/overlay.toml` and runs `nvchecker` on
   an isolated temporary configuration.
2. Accepts a version from the JSON events recognized by the current implementation.
3. If no tracker entry exists, reads `metadata.xml` and queries PyPI only when an
   upstream `remote-id` explicitly identifies a PyPI project.
4. Returns `cat_pkg`, `upstream`, `source`, and `advisory` fields.

The structured metadata check prevents a package-name-only PyPI lookup. A PyPI result is
still a candidate: confirm that the metadata remains current and that PyPI is the release
channel packaged by the ebuild.

When a configured nvchecker entry returns no recognized version, the command reports that
state and does not switch providers. Investigate authentication, event format, filters,
and the upstream release stream directly.

## Verify a candidate

1. Read the current ebuild, `metadata.xml`, package history, and overlay tracker entry.
2. Identify the canonical upstream project and release channel from current upstream
   documentation, not from a redirect or package-name similarity.
3. Match the candidate to a release, tag, source archive, and required binary or vendor
   assets. Check whether it is final, prerelease, yanked, superseded, or from another
   release channel.
4. Compare upstream build metadata and release notes with the packaged version.
5. Convert the upstream version to Gentoo syntax using the PMS and Devmanual. Preserve
   the literal upstream tag or filename separately when normalization changes it.
6. Confirm that all required artifacts exist and that their license and provenance are
   suitable before scaffolding the ebuild.

Return no version and escalate when the upstream identity or release mapping remains
ambiguous. Never select a plausible version string merely because it sorts higher.

## Maintain `overlay.toml`

Add or change a tracker entry only after verifying the upstream release model.

- Select the nvchecker source and options from the current nvchecker documentation.
  GitHub releases and git tags are different streams; choose the one the package actually
  follows.
- Verify prefixes, prerelease handling, tag filtering, and sorting with real upstream
  data. Do not copy options from another package without checking their meaning.
- When the verified project has moved, propagate it: `HOMEPAGE`, the `metadata.xml`
  `remote-id`, and every version-tracking URL name the current project, while `SRC_URI`
  still names the exact artifact that release published.
- Inspect existing active and commented entries for the package and related variants.
  Preserve a documented reason for leaving an entry disabled.
- When two entries would track the same upstream source, leave only one active and comment
  the other out with its reason. A package that can enable automatic bumping is the
  exception to that rule.
- The overlay's tracker compares versions with Portage ordering, because the installed
  version comes from the tree and a PEP440 comparison misreads `_p` and `_rc` and reports a
  downgrade as an update. Read a tracker result under that ordering and verify the real tag
  before acting on it.
- Preserve overlay-specific keys such as `github_account`; they are not nvchecker source
  options. Change them only when the live workflow or a maintainer provides the required
  evidence.
- Run nvchecker on the exact entry with the required authentication. Keep credentials out
  of command output, files in the repository, and reports.

`gzh nvchecker-config set` replaces the selected table and then sorts package blocks. It
can change table formatting and the placement of adjacent comments. Review the complete
`overlay.toml` diff after every invocation. Edit a commented-only entry manually when the
tool cannot preserve its intended structure.

A non-empty `advisory` is a request for review. It does not by itself require adding or
enabling a tracker entry.

## Example GitHub release entry

Use this only after confirming that the project publishes the package's release stream
through GitHub releases and that the current nvchecker documentation supports the
selected options.

```toml
["category/package"]
source = "github"
github = "OWNER/REPOSITORY"
use_latest_release = true
```

Add filtering or prefix options only when the observed tags require them and an isolated
nvchecker run returns the intended Gentoo version candidate.

## Primary references

- [nvchecker usage documentation](https://nvchecker.readthedocs.io/en/latest/usage.html)
- [Gentoo Package Manager Specification](https://projects.gentoo.org/pms/latest/pms.html)
- [Gentoo version guidance](https://devmanual.gentoo.org/ebuild-writing/file-format/index.html)
- The live overlay `AGENTS.md`, `overlay.toml`, and nvchecker workflow
- The package's upstream release pages, tags, archives, build metadata, and license text
