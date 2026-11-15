import httpx
import pytest

from platform import llm


def configure(monkeypatch):
    monkeypatch.setenv("SARVAM_API_KEY", "test-secret")
    monkeypatch.setenv("SARVAM_MODEL", "sarvam-105b")


def test_chat_posts_documented_payload_and_returns_content(monkeypatch):
    configure(monkeypatch)
    seen = {}

    def fake_post(url, **kwargs):
        seen.update(url=url, **kwargs)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Namaste"}}]},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(llm.httpx, "post", fake_post)

    assert llm.chat("Be helpful", "Hello") == "Namaste"
    assert seen["url"] == "https://api.sarvam.ai/v1/chat/completions"
    assert seen["headers"] == {"api-subscription-key": "test-secret"}
    assert seen["json"] == {
        "model": "sarvam-105b",
        "messages": [
            {"role": "system", "content": "Be helpful"},
            {"role": "user", "content": "Hello"},
        ],
    }


@pytest.mark.parametrize("name", ["SARVAM_API_KEY", "SARVAM_MODEL"])
def test_chat_requires_configuration(monkeypatch, name):
    configure(monkeypatch)
    monkeypatch.delenv(name)
    with pytest.raises(ValueError, match=f"{name} is required"):
        llm.chat("system", "user")


def test_chat_maps_timeout_without_exposing_key(monkeypatch):
    configure(monkeypatch)

    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("internal detail")

    monkeypatch.setattr(llm.httpx, "post", timeout)
    with pytest.raises(TimeoutError, match="timed out") as error:
        llm.chat("system", "user")
    assert "test-secret" not in str(error.value)


@pytest.mark.parametrize("status", [401, 403])
def test_chat_maps_auth_errors(monkeypatch, status):
    configure(monkeypatch)
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(status, request=httpx.Request("POST", url)),
    )
    with pytest.raises(PermissionError, match=f"HTTP {status}"):
        llm.chat("system", "user")


def test_chat_maps_other_http_errors(monkeypatch):
    configure(monkeypatch)
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(429, request=httpx.Request("POST", url)),
    )
    with pytest.raises(RuntimeError, match="HTTP 429"):
        llm.chat("system", "user")


@pytest.mark.parametrize("payload", [{}, {"choices": []}, {"choices": [{"message": {"content": 4}}]}])
def test_chat_rejects_malformed_response(monkeypatch, payload):
    configure(monkeypatch)
    monkeypatch.setattr(
        llm.httpx,
        "post",
        lambda url, **kwargs: httpx.Response(
            200, json=payload, request=httpx.Request("POST", url)
        ),
    )
    with pytest.raises(ValueError, match="malformed chat response"):
        llm.chat("system", "user")
