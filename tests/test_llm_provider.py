import httpx
import pytest

from app.config import Settings
from app.llm.provider import ChatMessage, ChatProvider, LLMError


def _settings():
    return Settings(llm_api_key="test-key", llm_base_url="https://llm.example/v1", llm_model="m1")


async def test_chat_returns_content():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200, json={"choices": [{"message": {"content": " 摘要内容 "}}]}, request=request)

    provider = ChatProvider(_settings(), transport=httpx.MockTransport(handler))
    text = await provider.chat([ChatMessage("user", "hi")])
    assert text == "摘要内容"


async def test_chat_missing_key_raises():
    provider = ChatProvider(Settings(llm_api_key=""), transport=httpx.MockTransport(lambda r: httpx.Response(200, request=r)))
    with pytest.raises(LLMError):
        await provider.chat([ChatMessage("user", "hi")])


async def test_chat_http_error_wrapped_as_llm_error():
    provider = ChatProvider(_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(500, request=r)))
    with pytest.raises(LLMError):
        await provider.chat([ChatMessage("user", "hi")])


async def test_chat_malformed_response_raises():
    provider = ChatProvider(_settings(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"foo": 1}, request=r)))
    with pytest.raises(LLMError):
        await provider.chat([ChatMessage("user", "hi")])
