import httpx
import pytest

from brain import llm
from brain.agents.comms import CommsAgent


_SUCCESS = "I checked the verified transaction details. I will share an update soon."


def test_empty_length_retries_once_with_double_budget(monkeypatch):
    calls = []

    def mocked_chat(system, user, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return "", "length"
        return _SUCCESS, "stop"

    monkeypatch.setenv("BRAIN_MAX_TOKENS", "2500")
    monkeypatch.delenv("SARVAM_REASONING_EFFORT", raising=False)
    monkeypatch.setattr(llm, "chat", mocked_chat)

    agent = CommsAgent()
    assert agent.compose("en", {"status": "PENDING"}) == _SUCCESS

    assert [call["max_tokens"] for call in calls] == [2500, 5000]
    assert all(call["allow_reasoning_fallback"] is False for call in calls)
    assert [item["attempt"] for item in agent.sarvam_attempts] == [1, 2]


def test_timeout_retries_once_with_same_budget(monkeypatch):
    calls = []

    def mocked_chat(system, user, **kwargs):
        calls.append(kwargs["max_tokens"])
        if len(calls) == 1:
            raise httpx.ReadTimeout("mock timeout")
        return _SUCCESS, "stop"

    monkeypatch.setattr(llm, "chat", mocked_chat)
    agent = CommsAgent()

    assert agent.compose("en", {"status": "PENDING"}) == _SUCCESS
    assert calls == [3000, 3000]


@pytest.mark.parametrize(
    ("outcome", "language", "observations", "expected"),
    [
        ("waiting", "en", {"status": "PENDING"}, "Your payment is still pending."),
        ("awaiting_approval", "en", {}, "awaiting human approval"),
        ("rejected", "en", {}, "did not approve"),
        ("blocked", "hi", {}, "एक व्यक्ति आपसे आगे संपर्क करेगा"),
    ],
)
def test_two_timeouts_fall_back_to_local_templates(
    monkeypatch, outcome, language, observations, expected
):
    def timeout(system, user, **kwargs):
        raise httpx.ReadTimeout("mock timeout")

    monkeypatch.setattr(llm, "chat", timeout)
    agent = CommsAgent()

    message = agent.compose(language, observations, outcome=outcome)

    assert expected in message
    assert agent.fallback_info == {
        "fallback": True,
        "reason": "ReadTimeout",
        "usage": {},
    }
    if language == "hi":
        assert any("\u0900" <= character <= "\u097f" for character in message)


def test_fallback_templates_include_verified_link_and_refund_details():
    link = CommsAgent.template(
        "en",
        "link_created",
        {"txn_id": "TXN1", "link_url": "https://example.test/pay/L1"},
    )
    refund = CommsAgent.template(
        "hi",
        "refund_done",
        {"txn_id": "TXN2", "refund_amount": 300},
    )

    assert "https://example.test/pay/L1" in link
    assert "₹300" in refund
    assert any("\u0900" <= character <= "\u097f" for character in refund)


def test_unverified_action_claim_is_replaced_with_template(monkeypatch):
    calls = []

    def mocked_chat(system, user, **kwargs):
        calls.append(kwargs)
        return "I created a payment link. You can pay now.", "stop"

    monkeypatch.setattr(llm, "chat", mocked_chat)
    agent = CommsAgent()

    message = agent.compose("en", {"status": "FAILED"}, outcome="waiting")

    assert len(calls) == 1
    assert agent.fallback_info["fallback"] is True
    assert agent.fallback_info["reason"] == "ValueError"
    assert message == CommsAgent.template("en", "waiting", {"status": "FAILED"})


def test_reasoning_content_is_not_used_as_customer_message(monkeypatch):
    class FakeResponse:
        status_code = 200
        is_error = False

        def json(self):
            return {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "reasoning_content": "private reasoning, not a customer answer",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setenv("SARVAM_API_KEY", "mock-key")
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)
    agent = CommsAgent()

    message = agent.compose("en", {"status": "PENDING"}, outcome="waiting")

    assert "private reasoning" not in message
    assert message == CommsAgent.template("en", "waiting", {"status": "PENDING"})
    assert agent.fallback_info["fallback"] is True
    assert agent.sarvam_attempts[0]["content_length"] == 0
    assert agent.sarvam_attempts[0]["reasoning_content_length"] > 0
