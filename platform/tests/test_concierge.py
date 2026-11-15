import pytest
from pydantic import ValidationError

from platform.agents import base, concierge
from platform.agents.concierge import ConciergeAgent, ConciergeOutput


def test_concierge_interprets_english_message_with_explicit_transaction_id(monkeypatch):
    calls = []

    def fake_chat(system, user):
        calls.append((system, user))
        return (
            '{"intent":"failed_payment","transaction_id":"TXN1001",'
            '"summary":"Customer says TXN1001 failed.",'
            '"missing_information":[],"needs_clarification":false}'
        )

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    result = ConciergeAgent().interpret("My payment failed, transaction TXN1001")

    assert isinstance(result, ConciergeOutput)
    assert result.intent == "failed_payment"
    assert result.transaction_id == "TXN1001"
    assert calls[0][1] == "My payment failed, transaction TXN1001"
    assert "English and Hindi" in calls[0][0]


def test_concierge_supports_hindi_and_marks_omitted_transaction_id(monkeypatch):
    seen = []

    def fake_chat(system, user):
        seen.append((system, user))
        return (
            '{"intent":"pending_payment","transaction_id":null,'
            '"summary":"ग्राहक का भुगतान लंबित है।",'
            '"missing_information":["transaction_id"],"needs_clarification":true}'
        )

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    result = ConciergeAgent().interpret("मेरा भुगतान अभी तक लंबित है")

    assert result.intent == "pending_payment"
    assert result.transaction_id is None
    assert result.needs_clarification is True
    assert "transaction_id" in result.missing_information
    assert seen[0][1] == "मेरा भुगतान अभी तक लंबित है"


def test_concierge_prompt_prohibits_payment_actions_and_invention():
    prompt = concierge.CONCIERGE_SYSTEM_PROMPT.lower()
    assert "never infer or invent" in prompt
    assert "do not perform or promise payment operations" in prompt
    assert "approve refunds" in prompt
    assert "call tools" in prompt


def test_concierge_output_rejects_unmodeled_fields():
    with pytest.raises(ValidationError):
        ConciergeOutput.model_validate(
            {
                "intent": "refund_request",
                "transaction_id": None,
                "summary": "Customer requests a refund.",
                "missing_information": [],
                "needs_clarification": False,
                "approved": True,
            }
        )
