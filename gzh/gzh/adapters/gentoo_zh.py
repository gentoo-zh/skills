"""Reviewed gentoo-zh repository adapter data."""

from __future__ import annotations


PROFILE = {
    "schema_version": 1,
    "adapter_id": "gentoo-zh",
    "profile_revision": 2,
    "reviewed_at": "2026-08-20",
    "sources": {
        "overlay-policy": {
            "title": "gentoo-zh overlay AGENTS.md",
            "authority": "overlay-policy",
            "location": (
                "https://raw.githubusercontent.com/gentoo-zh/overlay/master/AGENTS.md"
            ),
            "reviewed_evidence": {
                "kind": "sha256",
                "value": (
                    "59a2ae4d8d8c8ff233b62b56914ec75213486a0ed9eb2e9225fd61f7146dedc0"
                ),
                "checked_at": "2026-08-20T11:10:43Z",
            },
        },
        "overlay-ci-pkgcheck": {
            "title": "gentoo-zh pkgcheck workflow",
            "authority": "overlay-policy",
            "location": (
                "https://raw.githubusercontent.com/gentoo-zh/overlay/master/"
                ".github/workflows/pkgcheck.yml"
            ),
            "reviewed_evidence": {
                "kind": "sha256",
                "value": (
                    "321a91b68c74cb37896f508e5b5f8bdf62acfd19879682887e37a73070f5392f"
                ),
                "checked_at": "2026-08-03T15:48:54Z",
            },
        },
        "overlay-ci-emerge": {
            "title": "gentoo-zh emerge-on-PR workflow",
            "authority": "overlay-policy",
            "location": (
                "https://raw.githubusercontent.com/gentoo-zh/overlay/master/"
                ".github/workflows/emerge-on-pr.yml"
            ),
            "reviewed_evidence": {
                "kind": "sha256",
                "value": (
                    "4a20b54d5fbf770c108e8cff3d8b6ec39c887c0a8e46ccfb1c1383fc34537ccc"
                ),
                "checked_at": "2026-08-03T15:48:54Z",
            },
        },
        "overlay-pr-template": {
            "title": "gentoo-zh pull request template",
            "authority": "overlay-policy",
            "location": (
                "https://raw.githubusercontent.com/gentoo-zh/overlay/master/"
                ".github/pull_request_template.md"
            ),
            "reviewed_evidence": {
                "kind": "sha256",
                "value": (
                    "f5a19117f36845b8b97d9ce75c86e94a5e024a0db0ba42f66cfc98eb26c653ec"
                ),
                "checked_at": "2026-08-03T15:48:54Z",
            },
        },
        "gzh-compatibility": {
            "title": "Existing gzh gentoo-zh repository compatibility behavior",
            "authority": "reviewed-implementation",
            "location": "repository:gzh/gzh/repo.py",
            "reviewed_evidence": {
                "kind": "git-revision",
                "value": "76938251c5f56737fc62d33dd53bcb445160768e",
                "checked_at": "2026-08-03T23:54:56Z",
            },
        },
    },
    "resolution": {
        "repository_names": "repository-names",
        "canonical_repositories": "canonical-repositories",
        "default_branch": "default-branch",
        "remote_preference": "remote-preference",
        "forbidden_roots": "forbidden-roots",
    },
    "capabilities": {
        "repository-names": {
            "state": "known",
            "value": ["gentoo-zh"],
            "sources": ["overlay-policy"],
        },
        "canonical-repositories": {
            "state": "known",
            "value": [
                {
                    "host": "github.com",
                    "path": "gentoo-zh/overlay",
                    "priority": 0,
                    "case_sensitive": False,
                    "source": "overlay-policy",
                },
                {
                    "host": "github.com",
                    "path": "microcai/gentoo-zh",
                    "priority": 1,
                    "case_sensitive": False,
                    "source": "gzh-compatibility",
                },
            ],
            "sources": ["overlay-policy", "gzh-compatibility"],
        },
        "default-branch": {
            "state": "known",
            "value": "master",
            "sources": ["overlay-policy"],
        },
        "remote-preference": {
            "state": "known",
            "value": ["upstream", "origin", "canonical"],
            "sources": ["gzh-compatibility"],
        },
        "forbidden-roots": {
            "state": "known",
            "value": ["/var/db/repos"],
            "sources": ["overlay-policy", "gzh-compatibility"],
        },
        "local-gates": {
            "state": "known",
            "value": ["gzh lint", "gzh manifest", "gzh qa"],
            "sources": ["overlay-policy"],
        },
        "authoritative-ci": {
            "state": "known",
            "value": ["pkgcheck", "emerge-on-pr"],
            "sources": ["overlay-ci-pkgcheck", "overlay-ci-emerge"],
        },
        "publication-approval": {
            "state": "known",
            "value": {
                "per_pull_request": True,
                "template_required": True,
                "authorization_is_runtime_only": True,
            },
            "sources": ["overlay-policy", "overlay-pr-template"],
        },
    },
    "operations": {
        "inspect": {
            "state": "known",
            "value": {
                "write": False,
                "requires_capabilities": [],
                "runtime_requirements": [],
            },
            "sources": ["overlay-policy"],
        },
        "repository-write-preflight": {
            "state": "known",
            "value": {
                "write": True,
                "requires_capabilities": [
                    "repository-names",
                    "canonical-repositories",
                    "default-branch",
                    "remote-preference",
                    "forbidden-roots",
                    "local-gates",
                ],
                "runtime_requirements": [
                    "root",
                    "development_checkout",
                    "repo_name",
                    "identity",
                    "canonical_remote",
                    "default_branch",
                    "clean",
                    "base_synchronized",
                ],
            },
            "sources": ["overlay-policy", "gzh-compatibility"],
        },
        "publication": {
            "state": "unknown",
            "reason": (
                "Repository policy defines the approval procedure, but current user "
                "authorization cannot be stored in a static adapter profile."
            ),
            "sources": ["overlay-policy", "overlay-pr-template"],
        },
        "unattended-publication": {
            "state": "unsupported",
            "reason": (
                "Per-pull-request approval is required before publication actions."
            ),
            "sources": ["overlay-policy", "overlay-pr-template"],
        },
    },
}
