from copy import deepcopy

import pytest

from gzh.pr_plan import (
    build_pr_plan,
    verify_plan_confirmation,
    verify_pr_plan,
)


def _plan(**overrides):
    values = {
        "title": "app-misc/demo: add 1.2.3",
        "body": "Closes #123\n\n<!-- template marker -->\n- [x] pkgcheck\n",
        "files": ["app-misc/demo/Manifest", "app-misc/demo/demo-1.2.3.ebuild"],
        "head_branch": "app-misc-demo-1.2.3",
        "head_sha": "a" * 40,
        "base_branch": "master",
        "base_sha": "b" * 40,
        "template": "<!-- template marker -->\n- [ ] pkgcheck\n",
    }
    values.update(overrides)
    return build_pr_plan(**values)


@pytest.mark.parametrize("change", [
    {"title": "app-misc/demo: add 1.2.4"},
    {"body": ("Different rationale.\n\n<!-- template marker -->\n"
              "- [x] pkgcheck\n")},
    {"files": ["app-misc/demo/demo-1.2.3.ebuild"]},
    {"head_branch": "app-misc-demo-rewritten"},
    {"head_sha": "c" * 40},
    {"base_branch": "next"},
    {"base_sha": "d" * 40},
    {"template": "changed template\n",
     "body": "Different rationale.\n\nchanged template\n"},
])
def test_digest_and_verification_change_for_every_confirmed_field(change):
    confirmed = _plan()
    current = _plan(**change)
    result = verify_pr_plan(confirmed, current)
    assert current["sha256"] != confirmed["sha256"]
    assert result["ok"] is False
    assert result["changed_fields"]


def test_verification_detects_tampered_confirmed_plan():
    confirmed = _plan()
    confirmed["body"] = "tampered after confirmation"
    result = verify_pr_plan(confirmed, confirmed)
    assert result["ok"] is False
    assert result["integrity_ok"] is False


def test_one_confirmation_may_name_several_exact_plan_ids():
    plans = [_plan(), _plan(
        title="dev-util/other: add 2.0",
        files=["dev-util/other/other-2.0.ebuild"],
        head_branch="dev-util-other-2.0",
        head_sha="e" * 40,
    )]
    result = verify_plan_confirmation(
        plans, [plan["plan_id"] for plan in reversed(plans)])
    assert result["ok"] is True
    assert result["missing_plan_ids"] == []
    assert result["unknown_plan_ids"] == []


def test_blanket_publish_all_is_not_a_plan_confirmation():
    plan = _plan()
    result = verify_plan_confirmation([plan], ["publish-all"])
    assert result["ok"] is False
    assert result["missing_plan_ids"] == [plan["plan_id"]]
    assert result["unknown_plan_ids"] == ["publish-all"]


def test_plan_retains_complete_body_file_order_and_template():
    plan = _plan()
    assert plan["body"].endswith("- [x] pkgcheck\n")
    assert plan["files"] == [
        "app-misc/demo/Manifest", "app-misc/demo/demo-1.2.3.ebuild"]
    assert plan["template"].startswith("<!-- template marker -->")
    assert verify_pr_plan(plan, deepcopy(plan))["ok"] is True


@pytest.mark.parametrize("body", [
    "Closes #123\n",
    "Closes #123\n\n- [x] pkgcheck\n<!-- template marker -->\n",
    "Closes #123\n\n<!-- template marker -->\n- [x] rewritten check\n",
    ("Closes #123\n\n<!-- template marker -->\n- [x] pkgcheck\n"
     "<!-- template marker -->\n- [ ] pkgcheck\n"),
])
def test_plan_rejects_missing_reordered_rewritten_or_duplicate_template(body):
    with pytest.raises(ValueError, match="retain the complete live template"):
        _plan(body=body)


def test_plan_allows_only_checkbox_state_to_change_in_template():
    plan = _plan(body=(
        "Closes #123\n\n<!-- template marker -->\n- [X] pkgcheck\n"))
    assert plan["body"].endswith("- [X] pkgcheck\n")
