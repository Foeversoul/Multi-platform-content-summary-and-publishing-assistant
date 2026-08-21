from app.llm.provider import ChatMessage, LLMError
from app.processor.summarizer import generate_summary


class FakeProvider:
    def __init__(self, content: str | Exception):
        self.content = content

    async def chat(self, messages: list[ChatMessage], temperature: float = 0.7) -> str:
        if isinstance(self.content, Exception):
            raise self.content
        return self.content


ARTICLE = (
    "人工智能大模型在医疗影像诊断中展现出显著优势。" * 5
    + "张三团队在北京市发布了最新研究成果，会议于2026年8月18日举行。" * 5
)


async def test_generate_summary_llm_path():
    provider = FakeProvider(
        '{"summary": "' + "这是两百字左右的摘要内容。" * 16 + '", "key_points": ["要点一", "要点二", "要点三"], "short_title": "精简标题"}'
    )
    result = await generate_summary(provider, ARTICLE, "研究标题")
    assert result.source == "llm"
    assert len(result.key_points) == 3
    assert result.short_title == "精简标题"


async def test_generate_summary_falls_back_on_error():
    provider = FakeProvider(LLMError("boom"))
    result = await generate_summary(provider, ARTICLE, "研究标题", min_chars=10, max_chars=200)
    assert result.source == "extractive"
    assert result.summary_text


async def test_generate_summary_falls_back_on_invalid_json():
    provider = FakeProvider("not json")
    result = await generate_summary(provider, ARTICLE, "研究标题", min_chars=10, max_chars=200)
    assert result.source == "extractive"


async def test_generate_summary_writes_full_description_for_short_source():
    long_text = "这是AI根据视频简介与时间戳补写的一大段完整描述。" * 10
    provider = FakeProvider('{"summary": "' + long_text + '"}')
    result = await generate_summary(provider, "芯片对比实测。", "芯片对比实测", min_chars=50)
    assert result.source == "llm"
    assert len(result.summary_text) >= 50
    assert "一大段完整描述" in result.summary_text


async def test_generate_summary_short_source_falls_back_without_provider():
    result = await generate_summary(None, "00:00 开场，01:00 实测。", "芯片对比实测", min_chars=50)
    assert result.source == "extractive"
    assert result.summary_text
