from enum import Enum

import httpx
import pytest
from pydantic import BaseModel

from brain.agents.base import (
    AgentBase,
    SarvamReasoningBudgetError,
)
from brain.agents.resolver import Action, ResolverAgent
from brain import llm


class Intent(str, Enum):
    FAILED_PAYMENT = "failed_payment"


class Reply(BaseModel):
    intent: Intent
    transaction_id: str | None = None


def test_parses_think_wrapped_fenced_json_and_normalizes_key_and_enum(monkeypatch):
    replies = iter(
        [
            (
                '<think>brief reasoning</think>\n```json\n'
                '{"classification": "FAILED_PAYMENT", "txn_id": "TXN1"}\n```',
                "stop",
            )
        ]
    )
    monkeypatch.setattr(llm, "chat", lambda system, user: next(replies))

    result = AgentBase().run("system", "user", Reply)

    assert result.intent is Intent.FAILED_PAYMENT
    assert result.transaction_id == "TXN1"


def test_empty_reply_with_length_retries_invalid_json_once(monkeypatch):
    calls = []

    def mocked_chat(system, user):
        calls.append(1)
        return "", "length"

    monkeypatch.setattr(llm, "chat", mocked_chat)
    with pytest.raises(SarvamReasoningBudgetError) as error:
        AgentBase().run("system", "user", Reply)
    assert len(calls) == 2
    assert error.value.finish_reason == "length"


def test_empty_length_reply_has_reasoning_budget_diagnostics(monkeypatch):
    usage = {"prompt_tokens": 48, "completion_tokens": 3000, "total_tokens": 3048}
    calls = []

    class FakeResponse:
        status_code = 200
        is_error = False

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": "", "reasoning_content": ""},
                        "finish_reason": "length",
                    }
                ],
                "usage": usage,
            }

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            calls.append(1)
            return FakeResponse()

    monkeypatch.setenv("SARVAM_API_KEY", "mock-key")
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)
    with pytest.raises(SarvamReasoningBudgetError) as error:
        AgentBase().run("system", "user", Reply)

    assert len(calls) == 2
    assert error.value.finish_reason == "length"
    assert error.value.usage == usage
    assert error.value.request_max_tokens == llm.MAX_TOKENS
    assert error.value.input_length == len("system") + len("user")
    assert error.value.attempt == 2
    assert len(error.value.sarvam_attempts) == 2


def test_combined_resolver_accepts_observed_reply_variants(monkeypatch):
    observed_prefix = (
        '\n```json\n{\n  "action": "retry_payment",\n'
        '  "reason": "Transaction failed due to bank timeout, is marked retryable, '
        'and has not resulted in a refund or issued any payment link.",\n'
        '  "confidence": "high",'
    )
    complete_reply = (
        observed_prefix
        + '\n  "message": "I checked the verified transaction details. '
        'I will share the next step."\n}\n```'
    )
    monkeypatch.setattr(llm, "chat", lambda system, user: (complete_reply, "stop"))

    result = ResolverAgent().resolve_with_comms(
        "failed_payment",
        {"txn_id": "TXN1015", "status": "FAILED", "retryable": True},
        "en",
    )

    assert result.action is Action.CREATE_LINK
    assert result.confidence == 0.9
    assert result.customer_message == (
        "I checked the verified transaction details. I will share the next step."
    )


def test_read_timeout_retries_once_then_succeeds_with_new_client(monkeypatch):
    calls = []
    clients = []

    class FakeResponse:
        status_code = 200
        is_error = False

        def json(self):
            return {
                "choices": [
                    {
                        "message": {"content": '{"intent":"failed_payment"}'},
                        "finish_reason": "stop",
                    }
                ]
            }

    class FakeClient:
        def __init__(self, **kwargs):
            clients.append(self)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                raise httpx.ReadTimeout("mock timeout")
            return FakeResponse()

    monkeypatch.setenv("SARVAM_API_KEY", "mock-key")
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)

    assert AgentBase().run("system", "user", Reply).intent is Intent.FAILED_PAYMENT
    assert len(calls) == 2
    assert len(clients) == 2


def test_two_timeouts_stop_after_one_retry(monkeypatch):
    calls = []

    def mocked_chat(system, user):
        calls.append(1)
        raise httpx.ReadTimeout("mock timeout")

    monkeypatch.setattr(llm, "chat", mocked_chat)

    with pytest.raises(httpx.ReadTimeout):
        AgentBase().run("system", "user", Reply)
    assert len(calls) == 2
