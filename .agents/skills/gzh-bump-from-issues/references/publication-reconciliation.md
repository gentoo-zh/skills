# Publish or Reconcile a Batch

Read this file only after the batch has stopped at local commits and the user separately
asks to publish successful packages or reconcile publication state.

Handle every package under the live overlay policy and the `gzh-version-bump` publication
procedure. Build a separate immutable `gzh pr-plan` for every branch. One response may
approve several separately enumerated exact plan IDs, but prior batch or wildcard
authorization does not. Recompute each plan before `gh pr create` or `gh pr edit`, and
observe checks with `gzh ci`. Invoke `chinese-skill` when repository policy requires a
Chinese pull request body; keep its writing rules and examples in that skill.

After publication, run
`gzh batch-report reconcile <report> --expected-sha256 <sha256>` to append read-only push,
pull request, CI, merge, and issue states. Preserve the original gate results, skipped
checks, failures, and risks. Run `gzh batch cleanup <report> --dry-run` only after
reconciliation. It lists candidates and never deletes a branch or worktree.
