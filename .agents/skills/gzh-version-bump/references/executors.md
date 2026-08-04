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

## Execution

Use an exact repository-qualified atom. Local execution records the selected commit.
SSH execution additionally requires every path changed by that commit:

```bash
gzh exec '=category/package-version::gentoo-zh' \
  --executor local --use +flag -x

gzh exec '=category/package-version::gentoo-zh' \
  --executor builder --commit HEAD \
  --path category/package/Manifest \
  --path category/package/package-version.ebuild \
  --use +flag -x
```

The SSH path set must exactly match the commit diff. `gzh` creates a bounded binary
patch, verifies its digest remotely, applies it only after repository validation, runs
the same dependency and exact-atom merge contract as the local executor, downloads
bounded evidence, reverses the patch, and removes only its run directory. It never runs
`git clean`, `git checkout`, or `git reset` on the remote checkout.

## Evidence

Each run creates a fresh directory under `$(gzh state-dir)/evidence/executors/` unless
`--evidence-dir` selects another new path. The manifest records executor type and name,
package, commit, redacted commands, times, USE state, architecture, profile, exit state,
final-log digest, saved elog inventory, installed-file inventory, retained dependencies,
and cleanup state. Resume only after `verify_evidence()` confirms the stored digest and
every declared artifact.

Only saved `qa`, `warn`, and `error` elog files fail the elog gate. Warning-like compiler
or phase output remains build evidence and must not be reclassified as an elog file.
