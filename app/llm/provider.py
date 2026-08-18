from dataclasses import dataclass

import httpx

from app.config import Settings


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
        try:
            async with httpx.AsyncClient(timeout=self.settings.llm_timeout_seconds, transport=self.transport) as client:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {self.settings.llm_api_key}"},
                    json=payload,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise LLMError(f"llm request failed: {exc!r}") from exc
        try:
            return data["choices"][0]["message"]["content"].strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"unexpected LLM response: {data!r}") from exc
