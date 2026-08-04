from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = ROOT / ".github" / "workflows" / "reference-audit.yml"


@pytest.fixture
def branch_advance():
    return {
        "trigger_sha": "a" * 40,
        "advanced_master_sha": "b" * 40,
    }


def test_maintenance_workflow_pins_execution_to_trigger_identity(
        branch_advance):
    text = WORKFLOW.read_text(encoding="utf-8")
    checkout = text[text.index("- uses: actions/checkout@v7"):
                    text.index("- uses: actions/setup-python@v7")]
    ref = next(line.split(":", 1)[1].strip()
               for line in checkout.splitlines()
               if line.strip().startswith("ref:"))
    resolved_ref = {
        "${{ github.sha }}": branch_advance["trigger_sha"],
        "master": branch_advance["advanced_master_sha"],
    }[ref]

    assert "ref: ${{ github.sha }}" in checkout
    assert "ref: master" not in checkout
    assert resolved_ref == branch_advance["trigger_sha"]
    assert resolved_ref != branch_advance["advanced_master_sha"]
    assert "git rev-parse HEAD" in checkout
    assert '"$checked_out_sha" != "$GITHUB_SHA"' in checkout


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
    assert "restore-decision" in text
    assert "initial_cursor:" in text
    assert "reviewed-seed.txt" in text
    assert "workflow-runs.json" in text
    assert "historical maintenance state" not in text
    assert "resolve-git-ref" not in text
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
    assert "if $overall != 0" in prepare
    assert ".ok = false" in prepare
    assert ".complete = false" in prepare
    assert (
        ".status = {state: $state, decision: $decision_status, "
        "queue: $queue, cycle: $cycle, "
        "review: $review_status}" in prepare)
    assert "if (( overall != 0 ))" in prepare
    assert "s/^- Result: pass$/- Result: review required/" in prepare
    assert "s/^- Result: scoped pass$/- Result: review required/" in prepare


def test_maintenance_workflow_keeps_candidate_review_visible_and_blocking():
    text = WORKFLOW.read_text(encoding="utf-8")

    assert text.count("review-status") >= 2
    assert "maintenance-output/candidate-review.json" in text
    assert "maintenance-output/candidate-review.md" in text
    assert "maintenance-output/review.status" in text
    assert '"$review_status" == "0"' in text
    assert ".candidate_review = $review[0]" in text
    assert text.index("candidate-review.md") < text.index(
        "- name: Synchronize the review issue")


def test_manual_candidate_decision_requires_authenticated_restored_state():
    text = WORKFLOW.read_text(encoding="utf-8")
    start = text.index("- name: Apply reviewed candidate decision")
    end = text.index("- name: Run maintenance cycle")
    decision = text[start:end]

    for input_name in (
            "candidate_action:", "candidate_key:", "candidate_from_state:",
            "candidate_to_state:", "candidate_reason:",
            "evidence_fingerprint:", "checklist_json:"):
        assert input_name in text
    assert '"$GITHUB_EVENT_NAME" != "workflow_dispatch"' in decision
    assert 'state-bootstrap.txt)" != "restored"' in decision
    assert "authenticated-state.json" in decision
    assert "authenticated-provenance.json" in decision
    assert "candidate decisions cannot mutate new or recovered empty state" in decision
    assert "transition \"$CANDIDATE_KEY\"" in decision
    assert "--from-state \"$CANDIDATE_FROM_STATE\"" in decision
    assert "--to-state \"$CANDIDATE_TO_STATE\"" in decision
    assert "link-evidence \"$CANDIDATE_KEY\"" in decision
    assert "--reason \"$CANDIDATE_REASON\"" in decision
    assert "--evidence-fingerprint \"$EVIDENCE_FINGERPRINT\"" in decision
    assert "--checklist maintenance-output/candidate-checklist.json" in decision
    assert "--to-state promoted" not in decision


def test_candidate_decision_continues_to_review_and_authenticated_persistence():
    text = WORKFLOW.read_text(encoding="utf-8")

    decision = text.index("- name: Apply reviewed candidate decision")
    cycle = text.index("- name: Run maintenance cycle")
    queue = text.index("- name: Run durable maintenance queue")
    review = text.index("- name: Summarize candidate review state")
    prepare = text.index("- name: Prepare complete failure report")
    seal = text.index("- name: Seal maintenance state")
    upload = text.index("- name: Preserve authenticated maintenance state")
    assert decision < cycle < queue < review < prepare < seal < upload
    assert "decision.status" in text
    assert ".candidate_decision = $decision[0]" in text
    assert "candidate_decision: ($candidate_decision[0]" in text
    assert "maintenance-output/candidate-decision.json" in text
    assert "maintenance-output/candidate-decision-result.json" in text


def test_newest_authenticated_state_failure_cannot_fall_back_to_older_state():
    lines = WORKFLOW.read_text(encoding="utf-8").splitlines()
    failure = next(
        index for index, line in enumerate(lines)
        if "eligible state artifact ${artifact_id} failed content verification"
        in line)

    assert lines[failure + 1].strip() == "exit 1"
