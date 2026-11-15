import pytest
from pydantic import BaseModel, ValidationError

from platform.agents import base
from platform.agents.base import AgentBase


class Result(BaseModel):
    answer: str


def test_agent_returns_validated_model(monkeypatch):
    calls = []

    def fake_chat(system, user):
        calls.append((system, user))
        return '{"answer":"resolved"}'

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    agent = AgentBase[Result]("system instructions", Result)

    result = agent.run("user request")

    assert result == Result(answer="resolved")
    assert calls == [("system instructions", "user request")]


def test_agent_retries_invalid_output_with_validation_feedback(monkeypatch):
    prompts = []
    replies = iter(['{"answer":3}', '{"answer":"fixed"}'])

    def fake_chat(system, user):
        prompts.append(user)
        return next(replies)

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    result = AgentBase("system", Result, max_retries=1).run("request")

    assert result.answer == "fixed"
    assert prompts[0] == "request"
    assert "previous response did not match" in prompts[1]
    assert "answer" in prompts[1]


def test_agent_raises_validation_error_after_bounded_invalid_output(monkeypatch):
    calls = []
    monkeypatch.setattr(base.llm, "chat", lambda *_: calls.append(1) or "not json")

    with pytest.raises(ValidationError):
        AgentBase("system", Result, max_retries=2).run("request")

    assert len(calls) == 3


@pytest.mark.parametrize(
    "error",
    [TimeoutError("timed out"), RuntimeError("Sarvam chat request failed"), RuntimeError("Sarvam chat request failed (HTTP 503)"), RuntimeError("Sarvam chat request failed (HTTP 429)")],
)
def test_agent_retries_transient_llm_failures(monkeypatch, error):
    calls = []

    def fake_chat(*_):
        calls.append(1)
        if len(calls) == 1:
            raise error
        return '{"answer":"ok"}'

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    result = AgentBase("system", Result, max_retries=1).run("request")

    assert result.answer == "ok"
    assert len(calls) == 2


def test_agent_does_not_retry_non_transient_http_error(monkeypatch):
    calls = []

    def fake_chat(*_):
        calls.append(1)
        raise RuntimeError("Sarvam chat request failed (HTTP 400)")

    monkeypatch.setattr(base.llm, "chat", fake_chat)
    with pytest.raises(RuntimeError, match="HTTP 400"):
        AgentBase("system", Result, max_retries=3).run("request")
    assert len(calls) == 1


@pytest.mark.parametrize("kwargs", [{"max_retries": -1}, {"retry_delay": -0.1}])
def test_agent_rejects_invalid_retry_configuration(kwargs):
    with pytest.raises(ValueError):
        AgentBase("system", Result, **kwargs)
