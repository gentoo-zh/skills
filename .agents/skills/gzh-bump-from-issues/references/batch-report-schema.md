# Batch report schema

Use structured batch reports for resumable package outcomes. Markdown reports remain
human notes and cannot participate in typed updates or publication reconciliation.
Earlier free-form JSON reports did not have a stable item contract, so do not transform
them automatically. Preserve the original file and hash, then reconstruct a schema-v2
report from its queue snapshot, branches, commits, and retained evidence.

## Contents

- [Schema version 2](#schema-version-2)
- [Outcome transitions](#outcome-transitions)
- [Typed updates](#typed-updates)

## Schema version 2

A report requires these fields:

- `schema_version`: integer `2`
- `batch_id`: stable non-empty identifier
- `created_at`: UTC timestamp ending in `Z`
- `selection_snapshot`: source queue `path`, SHA-256, snapshot `schema_version`, exact
  `selection_expression`, and ordered unique `resulting_issues`
- `items`: zero or more package outcome records

Every item requires a unique `id`, selected `issue`, `package`, `target_version`, and an
`outcome`. States at or after `local_committed` also require an exact branch and full Git
object ID. Fields such as QA reports, executor evidence, failures, skips, warnings, risks,
and implementation-specific extensions remain part of the original report.
The set of item issue numbers must equal `selection_snapshot.resulting_issues`; an omitted
or extra issue invalidates the report. Every outcome history begins with `null -> pending`;
`pending` records selection without claiming that classification or package work occurred.

```json
{
  "schema_version": 2,
  "batch_id": "bump-20260804-01",
  "created_at": "2026-08-04T01:00:00Z",
  "selection_snapshot": {
    "path": "/state/queues/bump-issues-20260804.json",
    "sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
    "schema_version": 2,
    "selection_expression": {
      "issue_mode": "exact",
      "composition": "explicit_only",
      "queue": {
        "evaluated": false,
        "repository": "gentoo-zh/overlay",
        "label": "nvchecker",
        "state": "open",
        "limit": 100,
        "maintainer": null,
        "package": null,
        "autobump": "any"
      },
      "explicit_issues": [11700]
    },
    "resulting_issues": [11700]
  },
  "items": [
    {
      "id": "category/package@1.2.3",
      "issue": 11700,
      "package": "category/package",
      "target_version": "1.2.3",
      "outcome": {
        "state": "pending",
        "transitions": [
          {
            "from_state": null,
            "state": "pending",
            "at": "2026-08-04T01:00:00Z",
            "reason": "The queue snapshot selected this item for processing.",
            "evidence": {
              "selection_snapshot_sha256": "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
            }
          }
        ]
      }
    }
  ]
}
```

Create the initial report before package processing. Include every selected issue as a
`pending` item, with one `null -> pending` transition, a UTC time, a concrete selection
reason, and JSON evidence that binds it to the selection snapshot. Use a typed update to
record the later classification; do not represent an unprocessed item as `blocked`.

## Outcome transitions

The allowed progressions are:

```text
null -> pending
pending -> blocked | local_committed | superseded_by_external_merge
blocked -> local_committed | superseded_by_external_merge
local_committed -> pushed | superseded_by_external_merge
pushed -> pr_open | superseded_by_external_merge
pr_open -> checks_passed | superseded_by_external_merge
checks_passed -> merged | superseded_by_external_merge
```

`merged` and `superseded_by_external_merge` are terminal. A repeated state, backward
transition, or skipped intermediate publication state is invalid. `blocked` may advance
after its evidence changes; it is not silently converted into a skip. `pending` is valid
only as the first state and is never a typed update target.

Each transition has this form:

```json
{
  "from_state": "local_committed",
  "state": "pushed",
  "at": "2026-08-04T02:00:00Z",
  "reason": "The recorded commit is present on the exact fork branch.",
  "evidence": {
    "branch": "category-package-1.2.3",
    "sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
  }
}
```

Store the transition array under `item.outcome.transitions` and keep
`item.outcome.state` equal to the final transition state.

## Typed updates

Write transition evidence as a JSON object, then update one item:

```bash
gzh batch-report update report.json \
  --expected-sha256 <current-file-sha256> \
  --item-id category/package@1.2.3 \
  --state pushed \
  --reason 'The recorded commit is present on the exact fork branch.' \
  --evidence push-observation.json
```

Carry the returned SHA-256 into the next update. A stale digest stops before mutation.
The update appends one transition and preserves original and unknown fields. When a
pending or previously blocked item first reaches `local_committed`, pass `--branch`
and `--commit` to fill missing identity fields; an existing different value is a conflict.
Use `gzh batch-report reconcile` separately to append read-only GitHub publication
observations. Neither command pushes, opens a pull request, merges, or closes an issue.
