import pytest
from pydantic import ValidationError

from platform.agents import base
from platform.agents.investigator import InvestigatorAgent, InvestigatorOutput


def test_investigator_analyzes_only_supplied_evidence(monkeypatch):
    calls = []
    evidence = {
        "transaction": {"id": "TXN1001", "status": "FAILED", "bank_code": "BANK_TIMEOUT"},
        "bank_status": {"state": "FAILED", "retryable": True},
        "customer_message": "पैसे कट गए लेकिन भुगतान विफल दिखा।",
    }

    def fake_chat(system, user):
        calls.append((system, user))
        return (
            '{"likely_cause":"Bank timeout","confirmed_facts":'
            '["Transaction status is FAILED","Bank status is FAILED"],'
            '"hypotheses":["Funds may have been deducted despite failure"],'
            '"confidence":0.75,"retryable":true,'
            '"recommended_next_step":"Verify settlement status before retrying."}'
        )

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    result = InvestigatorAgent().investigate(evidence)

    assert isinstance(result, InvestigatorOutput)
    assert result.retryable is True
    assert result.confidence == 0.75
    assert result.confirmed_facts != result.hypotheses
    assert '"id": "TXN1001"' in calls[0][1]
    assert "पैसे कट गए" in calls[0][1]
    assert "Analyze only this supplied evidence JSON" in calls[0][1]
    assert "Never turn a hypothesis into a confirmed fact" in calls[0][0]


def test_investigator_keeps_retryability_unknown_without_support(monkeypatch):
    monkeypatch.setattr(
        base.llm,
        "chat",
        lambda *_: (
            '{"likely_cause":"Unknown","confirmed_facts":[],"hypotheses":[],'
            '"confidence":0.1,"retryable":null,'
            '"recommended_next_step":"Request the transaction reference."}'
        ),
    )

    result = InvestigatorAgent().investigate({"customer_message": "मेरा भुगतान अटका है"})

    assert result.retryable is None
    assert result.confirmed_facts == []


def test_investigator_rejects_confidence_outside_range(monkeypatch):
    monkeypatch.setattr(
        base.llm,
        "chat",
        lambda *_: (
            '{"likely_cause":"Unknown","confirmed_facts":[],"hypotheses":[],'
            '"confidence":1.5,"retryable":null,"recommended_next_step":"Check status."}'
        ),
    )

    with pytest.raises(ValidationError):
        InvestigatorAgent(max_retries=0).investigate({})


def test_investigator_prompt_forbids_execution_and_approval():
    prompt = InvestigatorAgent().system_prompt.lower()
    assert "never execute tools" in prompt
    assert "issue refunds" in prompt
    assert "approve actions" in prompt
