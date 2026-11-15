import httpx
import pytest

from brain import llm


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.is_error = status_code >= 400

    def json(self):
        return self._payload


def test_empty_content_uses_reasoning_and_default_model(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, **kwargs):
            calls.append({"timeout": kwargs["timeout"]})

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, *, headers, json):
            calls[-1].update({"url": url, "headers": headers, "json": json})
            return FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {"content": "", "reasoning_content": '{"ok": true}'},
                            "finish_reason": "length",
                        }
                    ]
                },
            )

    monkeypatch.setenv("SARVAM_API_KEY", "mock-key")
    monkeypatch.delenv("SARVAM_MODEL", raising=False)
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)

    assert llm.chat("system", "user") == ('{"ok": true}', "length")
    assert calls[0]["json"]["model"] == "sarvam-105b"
    assert calls[0]["json"]["temperature"] == 0.2
    assert calls[0]["json"]["max_tokens"] == 3000
    assert calls[0]["timeout"].read == 90
    assert calls[0]["timeout"].connect == 10


def test_empty_content_without_reasoning_returns_empty_text(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse(
                200,
                {"choices": [{"message": {"content": None}, "finish_reason": "length"}]},
            )

    monkeypatch.setenv("SARVAM_API_KEY", "mock-key")
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)

    assert llm.chat("system", "user") == ("", "length")


def test_http_error_exposes_status_and_finish_reason(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            return FakeResponse(400, {"choices": [{"finish_reason": "length"}]})

    monkeypatch.setenv("SARVAM_API_KEY", "mock-key")
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)

    with pytest.raises(llm.SarvamError, match="HTTP 400.*finish_reason='length'") as error:
        llm.chat("system", "user")
    assert error.value.http_status == 400
    assert error.value.finish_reason == "length"


def test_sarvam_timeout_error_keeps_timeout_type(monkeypatch):
    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("mock timeout")

    monkeypatch.setenv("SARVAM_API_KEY", "mock-key")
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)

    with pytest.raises(TimeoutError, match="timed out"):
        llm.chat("system", "user")


def test_call_metadata_records_budget_effort_usage_and_excludes_prompts(monkeypatch):
    observed = {}
    usage = {"prompt_tokens": 12, "completion_tokens": 34, "total_tokens": 46}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, url, *, headers, json):
            observed.update(json)
            return FakeResponse(
                200,
                {
                    "choices": [
                        {
                            "message": {"content": "verified text"},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": usage,
                },
            )

    monkeypatch.setenv("SARVAM_API_KEY", "mock-key")
    monkeypatch.setenv("SARVAM_REASONING_EFFORT", "low")
    monkeypatch.setattr(llm.httpx, "Client", FakeClient)

    assert llm.chat(
        "system prompt",
        "user prompt",
        max_tokens=6500,
        allow_reasoning_fallback=False,
        attempt=2,
    ) == ("verified text", "stop")

    metadata = llm.recent_call_metadata(1)[0]
    assert observed["max_tokens"] == 6500
    assert observed["reasoning_effort"] == "low"
    assert metadata["usage"] == usage
    assert metadata["request_max_tokens"] == 6500
    assert metadata["reasoning_effort_sent"] is True
    assert metadata["attempt"] == 2
    assert metadata["finish_reason"] == "stop"
    assert metadata["content_length"] == len("verified text")
    assert metadata["elapsed_ms"] >= 0
    assert "system prompt" not in repr(metadata)
    assert "user prompt" not in repr(metadata)
    assert "mock-key" not in repr(metadata)
