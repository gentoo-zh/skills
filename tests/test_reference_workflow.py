from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "reference-audit.yml"


def test_maintenance_workflow_restores_authenticated_state_and_open_plan():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert "verify-provenance" in text
    assert "authenticated-run.json" in text
    assert "authenticated-job.json" in text
    assert "candidate-state-${artifact_id}" in text
    assert "eligible_candidates=0" in text
    assert "no eligible state artifact passed content verification" in text
    assert "failed to download eligible state artifact" in text
    assert text.count("state_bundle.py verify --database") >= 2
    assert "find-open-plan" in text
    assert text.index("find-open-plan") < text.index(
        'plan_id="maintenance-${GITHUB_SHA}-${cursor}"')
    assert "maintenance-${GITHUB_RUN_ID}" not in text
    assert "resolve-git-ref" in text
    assert "git ls-remote" not in text
    assert text.index("- name: Run maintenance cycle") < text.index(
        "- name: Run durable maintenance queue")
    assert '"$(cat maintenance-output/cycle.status)" != "0"' in text
    assert "jq -n --slurpfile contract" in text


def test_maintenance_workflow_compacts_before_sealing_separate_state():
    text = WORKFLOW.read_text(encoding="utf-8")

    queue_compact = text.index("compact --keep-plans 16")
    evidence_compact = text.index("compact --keep-runs 32")
    seal = text.index("state_bundle.py create")
    assert queue_compact < seal
    assert evidence_compact < seal
    assert "name: skill-maintenance-report" in text
    assert "name: skill-maintenance-state" in text
    assert "maintenance-output/state-artifact/maintenance-state.db" in text
    assert "state-artifact.staging" in text


def test_maintenance_workflow_preserves_evidence_before_final_failure():
    text = WORKFLOW.read_text(encoding="utf-8")

    finalize = text.index("- name: Finalize report after state persistence")
    report_upload = text.index("- name: Preserve the complete report")
    state_upload = text.index("- name: Preserve authenticated maintenance state")
    issue_sync = text.index("- name: Synchronize the review issue")
    final_failure = text.index("- name: Fail after preserving maintenance evidence")
    assert state_upload < finalize < report_upload < issue_sync < final_failure
    assert "if-no-files-found: error" in text
    assert "--limit 1000" in text
    assert "state-seal.status" in text
    assert "STATE_UPLOAD_OUTCOME" in text
    assert ".state_persistence" in text
    assert "maintenance-output/queue-enqueue.log" in text
    assert "maintenance-output/queue-run.log" in text
    assert "maintenance-output/queue-status.log" in text


def test_queue_failure_marks_existing_cycle_report_incomplete():
    text = WORKFLOW.read_text(encoding="utf-8")

    prepare = text[text.index("- name: Prepare complete failure report"):]
    assert "elif (( overall != 0 ))" in prepare
    assert ".ok = false" in prepare
    assert ".complete = false" in prepare
    assert ".status = {state: $state, queue: $queue, cycle: $cycle}" in prepare
