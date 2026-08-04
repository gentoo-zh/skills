# Local and SSH Install Executors

Use a named executor only when the live repository contract requires an exact install
and the current host cannot perform it directly. Executor configuration is user-owned;
never place hostnames, ports, identity files, credentials, or personal paths in this
skill.

## Configuration

`gzh exec` reads
`${GZH_EXECUTOR_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/gzh/executors.toml}`.
The versioned configuration accepts only `local` and `ssh` entries:

```toml
version = 1

[executors.local]
type = "local"
allow_dependency_install = true

[executors.builder]
type = "ssh"
host = "build.example"
user = "portage"
port = 22
identity_file = "/absolute/path/to/identity"
remote_overlay_path = "/srv/gentoo-zh-overlay"
allow_dependency_install = true
```

The remote path must be a non-root development checkout and must not be under
`/var/db/repos`. The SSH executor verifies `portageq get_repo_path`, the Git worktree,
the canonical fetch URL, and the exact parent commit before transferring any bytes. It
does not forward the caller's environment or GitHub credentials.

`allow_dependency_install` authorizes only packages classified as new dependencies by
the pretend plan. It does not authorize rebuilds, upgrades, downgrades, removals, or
unclassified actions.

## Execution

Use an exact repository-qualified atom. Local execution requires the overlay selected by
`gzh repo` to be the exact Git root, have a canonical fetch remote and a clean worktree,
and have `HEAD` equal the full recorded commit before binding Portage to that path. SSH
execution remains bound to `remote_overlay_path` and additionally requires every path
changed by that commit:

```bash
gzh exec '=category/package-version::gentoo-zh' \
  --executor local --use +flag -x

gzh exec '=category/package-version::gentoo-zh' \
  --executor builder --commit HEAD \
  --path category/package/Manifest \
  --path category/package/package-version.ebuild \
  --use +flag -x
```

Both executors run one `emerge --pretend` with the exact arguments intended for the real
merge. The command uses `--ignore-default-opts`, disables autounmask, prefers binary
packages for dependencies, and excludes only the exact target atom from binary-package
selection. The executor stops before the merge unless the plan contains the exact target
ebuild once and every other action is an authorized new dependency. Rebuilds, upgrades,
downgrades, removals, unknown rows, incomplete output, and an unexpected binary target
fail closed. There is no separate source-only `--onlydeps` phase.

The SSH path set must exactly match the commit diff. `gzh` creates a bounded binary
patch, verifies its digest remotely, applies it only after repository validation, runs
the pretend and merge contract, downloads bounded evidence, reverses the patch, and
removes only its run directory. It never runs `git clean`, `git checkout`, or `git reset`
on the remote checkout.

## Evidence

Each run creates a fresh directory under `$(gzh state-dir)/evidence/executors/` unless
`--evidence-dir` selects another new path. The manifest records executor type and name,
package, commit, redacted commands, times, USE state, architecture, profile, exit state,
final-log digest, saved elog inventory, installed-file inventory, retained dependencies,
repository binding, authorized pretend plan, and cleanup state. Resume only after
`verify_evidence()` confirms the stored digest and every declared artifact.

Only saved `qa`, `warn`, and `error` elog files fail the elog gate. Warning-like compiler
or phase output remains build evidence and must not be reclassified as an elog file.
