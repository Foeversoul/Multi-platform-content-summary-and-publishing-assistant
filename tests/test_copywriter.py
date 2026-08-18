from app.adapter.copywriter import generate_copy
from app.adapter.platforms import PlatformConfig
from app.llm.provider import LLMError
from app.storage.models import Summary


class FakeProvider:
    def __init__(self, content: str | Exception):
        self.content = content

    async def chat(self, messages, temperature=0.7):
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


def _summary():
    return Summary(
        id=1,
        article_id=1,
        summary_text="张三团队发布人工智能研究成果。",
        key_points=["要点一", "要点二", "要点三"],
        short_title="研究成果",
        scores={},
        status="summarized",
    )


def _platform():
    return PlatformConfig(id="weibo", name="微博", min_chars=1, max_chars=140, min_tags=1, max_tags=3, style_prompt="微博风格")


async def test_generate_copy_llm_path():
    provider = FakeProvider('{"text": "今日热点：#科技# 研究成果发布。"}')
    result = await generate_copy(provider, _summary(), _platform())
    assert result.source == "llm"
    assert "#科技#" in result.text


async def test_generate_copy_falls_back_on_error():
    provider = FakeProvider(LLMError("boom"))
    result = await generate_copy(provider, _summary(), _platform())
    assert result.source == "fallback"
    assert "研究成果" in result.text


async def test_generate_copy_enforces_max_chars():
    provider = FakeProvider('{"text": "' + "很长的内容" * 100 + '"}')
    result = await generate_copy(provider, _summary(), _platform())
    assert len(result.text) <= 140
