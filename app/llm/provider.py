import asyncio
import logging
from dataclasses import dataclass

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# T6：可重试的 HTTP 状态码（429 限流 / 5xx 服务端错误）
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class LLMError(RuntimeError):
    pass


@dataclass
class ChatMessage:
    role: str
    content: str


class ChatProvider:
    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    async def chat(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        if not self.settings.llm_api_key:
            raise LLMError("llm_api_key is not configured")
        url = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.llm_model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": temperature,
            "max_tokens": self.settings.llm_max_tokens,
        }
        retries = self.settings.llm_retries
        # T6：429/5xx 指数退避重试，网络异常同样重试
        for attempt in range(retries + 1):
            try:
                return await self._request_once(url, payload)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in _RETRYABLE_STATUS or attempt >= retries:
                    raise LLMError(f"llm request failed: {exc!r}") from exc
                logger.warning("llm retry due to http %s", exc.response.status_code, extra={"attempt": attempt})
            except httpx.HTTPError as exc:
                if attempt >= retries:
                    raise LLMError(f"llm request failed: {exc!r}") from exc
                logger.warning("llm retry due to network error", extra={"attempt": attempt})
            await asyncio.sleep(self.settings.llm_retry_base_seconds * (2**attempt))
        raise LLMError("unreachable")

    async def _request_once(self, url: str, payload: dict) -> str:
        async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds, transport=self.transport) as client:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response: {data!r}") from exc
