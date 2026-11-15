import pytest

from platform.policy import PolicyDecision, check_policy


def test_read_only_status_checks_are_allowed_without_payment_evidence():
    decision = check_policy("check_bank_status", {})

    assert decision == PolicyDecision(
        allowed=True,
        reason="Read-only status or transaction checks are permitted.",
        requires_human_approval=False,
    )


def test_retry_allowed_only_with_confirmed_retryable_failure():
    decision = check_policy(
        "create_payment_link",
        {
            "transaction": {"status": "FAILED"},
            "bank_status": {"state": "FAILED", "retryable": True},
        },
    )

    assert decision.allowed is True
    assert decision.requires_human_approval is False


@pytest.mark.parametrize(
    ("context", "reason"),
    [
        ({"bank_status": {"state": "FAILED", "retryable": False}}, "non-retryable"),
        ({"bank_status": {"state": "FAILED"}}, "missing or invalid"),
        ({"bank_status": {"state": "PENDING", "retryable": True}}, "FAILED"),
        ({}, "missing"),
    ],
)
def test_retry_is_blocked_without_verified_retryable_failure(context, reason):
    decision = check_policy("retry_payment", context)

    assert decision.allowed is False
    assert reason in decision.reason


def test_refund_requires_human_approval_regardless_of_amount():
    context = {"transaction": {"id": "TXN1001", "amount": 7500}}

    blocked = check_policy("refund", context)
    approved = check_policy("refund", context, human_approved=True)

    assert blocked.allowed is False
    assert blocked.requires_human_approval is True
    assert approved.allowed is True
    assert approved.requires_human_approval is True


def test_duplicate_resolution_requires_evidence_and_human_approval():
    missing = check_policy("resolve_duplicate_charge", {"transaction": {}})
    unapproved = check_policy(
        "resolve_duplicate_charge", {"transaction": {"duplicate_of": "TXN1001"}}
    )
    approved = check_policy(
        "resolve_duplicate_charge",
        {"transaction": {"duplicate_of": "TXN1001"}},
        human_approved=True,
    )

    assert missing.allowed is False
    assert "missing" in missing.reason
    assert unapproved.allowed is False
    assert unapproved.requires_human_approval is True
    assert approved.allowed is True


def test_conflicting_transaction_and_bank_statuses_block_action():
    decision = check_policy(
        "create_payment_link",
        {
            "transaction": {"status": "SUCCESS"},
            "bank_status": {"state": "FAILED", "retryable": True},
        },
    )

    assert decision.allowed is False
    assert "conflict" in decision.reason


def test_unknown_actions_fail_closed():
    decision = check_policy("issue_gift_card", {})

    assert decision.allowed is False
    assert decision.requires_human_approval is False
