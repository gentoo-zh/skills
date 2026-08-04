import json

import pytest

from gzh.executor_evidence import (
    EvidenceError,
    command_record,
    create_evidence,
    redact_argv,
    verify_evidence,
)


def _write_evidence(path):
    return create_evidence(
        path,
        executor_type="ssh",
        executor_name="builder",
        package="=cat/pkg-1::gentoo-zh",
        commit="a" * 40,
        commands=[command_record(
            ["emerge", "=cat/pkg-1::gentoo-zh"],
            environment={
                "PORTAGE_ELOG_CLASSES": "qa warn error",
                "GITHUB_TOKEN": "top-secret",
            },
        )],
        started_at="2026-08-04T00:00:00Z",
        ended_at="2026-08-04T00:01:00Z",
        exit_state="passed",
        use_state=["+ssl", "-test"],
        arch="amd64",
        profile="default/linux/amd64/23.0/desktop",
        final_log="warning: compiler output only\n",
        elogs={"cat:pkg-1:0.log": "QA Notice\n"},
        installed_inventory=["/usr/bin/pkg"],
        cleanup={"ok": True, "removed_paths": ["/tmp/owned"]},
        retained_dependencies=["dev-libs/dep-1"],
    )


def test_evidence_is_bounded_durable_and_redacted(tmp_path):
    directory = tmp_path / "evidence"
    record = _write_evidence(directory)

    assert record["executor"] == {"type": "ssh", "name": "builder"}
    assert record["final_log"]["sha256"]
    assert record["elog_inventory"][0]["path"] == "elogs/cat:pkg-1:0.log"
    assert record["commands"][0]["environment"]["GITHUB_TOKEN"] == "[REDACTED]"
    assert verify_evidence(directory, expected_digest=record["digest"])["ok"] is True
    assert json.loads((directory / "evidence.json").read_text())["digest"] == record["digest"]


@pytest.mark.parametrize("relative", ["logs/final.log", "elogs/cat:pkg-1:0.log"])
def test_evidence_verifier_detects_modified_or_missing_artifacts(tmp_path, relative):
    directory = tmp_path / "evidence"
    record = _write_evidence(directory)
    artifact = directory / relative
    if relative.startswith("logs/"):
        artifact.write_text("changed\n")
    else:
        artifact.unlink()

    result = verify_evidence(directory, expected_digest=record["digest"])
    assert result["ok"] is False
    assert result["state"] == "incomplete"
    assert any(relative in error for error in result["errors"])


def test_evidence_requires_a_fresh_directory(tmp_path):
    directory = tmp_path / "existing"
    directory.mkdir()
    with pytest.raises(EvidenceError, match="already exists"):
        _write_evidence(directory)


def test_ssh_connection_arguments_and_secrets_are_redacted():
    command = [
        "ssh", "-i", "/private/id", "-p", "2200", "user@private-host",
        "TOKEN=secret", "emerge",
    ]
    result = redact_argv(command, sensitive_values=["private-host", "secret"])

    assert "/private/id" not in result
    assert "2200" not in result
    assert "user@private-host" not in result
    assert "TOKEN=secret" not in result
