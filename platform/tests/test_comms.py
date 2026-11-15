import pytest
from pydantic import ValidationError

from platform.agents import base
from platform.agents.comms import CommsAgent, CommsOutput


def test_comms_composes_english_messages_from_supplied_context(monkeypatch):
    calls = []
    context = {
        "transaction": {"id": "TXN1001", "status": "PENDING"},
        "investigation": {"finding": "Bank status remains pending"},
        "decision_pending": "Wait for status update",
    }

    def fake_chat(system, user):
        calls.append((system, user))
        return (
            '{"customer_message":"Your payment is still being checked. We will update you when its status is confirmed.",'
            '"desk_brief":"Issue: payment status is pending. Evidence: TXN1001 is PENDING. '
            'Investigation: bank status remains pending. Pending decision: wait for a status update.",'
            '"language":"English"}'
        )

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    result = CommsAgent().compose(context)

    assert isinstance(result, CommsOutput)
    assert result.language == "English"
    assert "still being checked" in result.customer_message
    assert "Pending decision" in result.desk_brief
    assert '"id": "TXN1001"' in calls[0][1]
    assert "Never say a payment, refund, or retry succeeded" in calls[0][0]


def test_comms_supports_hindi_and_does_not_claim_unconfirmed_success(monkeypatch):
    seen = []

    def fake_chat(system, user):
        seen.append(user)
        return (
            '{"customer_message":"आपका भुगतान अभी जाँच में है। पुष्टि होते ही हम आपको बताएँगे।",'
            '"desk_brief":"समस्या: भुगतान की स्थिति स्पष्ट नहीं है। साक्ष्य: कोई बैंक स्थिति उपलब्ध नहीं। '
            'जाँच: अभी पुष्टि नहीं हुई। लंबित निर्णय: बैंक स्थिति की प्रतीक्षा करें।",'
            '"language":"Hindi"}'
        )

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    result = CommsAgent().compose({"customer_message": "मेरा भुगतान कहाँ है?"})

    assert result.language == "Hindi"
    assert "अभी जाँच में है" in result.customer_message
    assert "सफल" not in result.customer_message
    assert "मेरा भुगतान कहाँ है?" in seen[0]


def test_comms_rejects_unsupported_language_or_missing_message(monkeypatch):
    monkeypatch.setattr(
        base.llm,
        "chat",
        lambda *_: '{"customer_message":"","desk_brief":"Brief","language":"French"}',
    )

    with pytest.raises(ValidationError):
        CommsAgent(max_retries=0).compose({})


def test_comms_generates_only_text_and_never_sends_messages():
    prompt = CommsAgent().system_prompt.lower()
    assert "generate text only" in prompt
    assert "do not send messages" in prompt
    assert "do not send messages, call telegram" in prompt
