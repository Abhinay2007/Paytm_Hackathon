import pytest
from pydantic import ValidationError

from platform.agents import base
from platform.agents.resolver import ResolverAgent, ResolverOutput


def test_resolver_recommends_wait_for_pending_payment(monkeypatch):
    calls = []

    def fake_chat(system, user):
        calls.append((system, user))
        return (
            '{"recommendation":"wait","rationale":"The supplied bank state is PENDING.",'
            '"requires_human_approval":false,"needs_more_information":false,"evidence_gaps":[]}'
        )

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    context = {"transaction": {"id": "TXN1001", "status": "PENDING"}, "customer_language": "hi"}

    result = ResolverAgent().resolve(context)

    assert isinstance(result, ResolverOutput)
    assert result.recommendation == "wait"
    assert result.requires_human_approval is False
    assert '"id": "TXN1001"' in calls[0][1]
    assert "Never invent transaction or bank information" in calls[0][0]


@pytest.mark.parametrize(
    ("context", "recommendation"),
    [
        ({"issue": "duplicate charge suspected"}, "human_review"),
        ({"customer_intent": "refund_request"}, "recommend_refund_review"),
    ],
)
def test_resolver_marks_sensitive_paths_for_human_approval(
    monkeypatch, context, recommendation
):
    monkeypatch.setattr(
        base.llm,
        "chat",
        lambda *_: (
            f'{{"recommendation":"{recommendation}","rationale":"Sensitive case.",'
            '"requires_human_approval":true,"needs_more_information":false,"evidence_gaps":[]}'
        ),
    )

    result = ResolverAgent().resolve(context)

    assert result.recommendation == recommendation
    assert result.requires_human_approval is True


def test_resolver_escalates_when_evidence_is_insufficient(monkeypatch):
    monkeypatch.setattr(
        base.llm,
        "chat",
        lambda *_: (
            '{"recommendation":"request_more_information",'
            '"rationale":"No transaction or bank status was supplied.",'
            '"requires_human_approval":false,"needs_more_information":true,'
            '"evidence_gaps":["transaction reference","bank status"]}'
        ),
    )

    result = ResolverAgent().resolve({"customer_message": "Payment problem"})

    assert result.needs_more_information is True
    assert result.evidence_gaps == ["transaction reference", "bank status"]


def test_resolver_prompt_makes_recommendations_advisory_only():
    prompt = ResolverAgent().system_prompt.lower()
    assert "recommendation is advisory only" in prompt
    assert "do not execute tools" in prompt
    assert "issue or approve a refund" in prompt
    assert "suspected duplicate charges always require human approval" in prompt


def test_resolver_rejects_unmodeled_recommendation(monkeypatch):
    monkeypatch.setattr(
        base.llm,
        "chat",
        lambda *_: (
            '{"recommendation":"issue_refund","rationale":"do it",'
            '"requires_human_approval":false,"needs_more_information":false,"evidence_gaps":[]}'
        ),
    )

    with pytest.raises(ValidationError):
        ResolverAgent(max_retries=0).resolve({"customer_intent": "refund_request"})
