"""页面快照采集器：常规爬虫与 OpenCLI 渲染均无法提取正文时的最终兜底。

流程：Playwright 加载页面（JS 渲染）-> 优先提取 DOM 文本 ->
若 DOM 文本为空则全页截图 + OCR 识别 -> LLM 清洗噪声 -> 返回 Candidate。
"""

import asyncio
import io
import logging

from app.collector.base import Candidate
from app.config import Settings

logger = logging.getLogger(__name__)

_CLEAN_PROMPT = (
    "你是一个文字清洗助手。以下文本来自网页截图 OCR 识别或 DOM 提取，"
    "可能包含噪声、乱码、重复内容、导航元素等。请："
    "1. 去除导航、广告、页脚、按钮文字等无关内容"
    "2. 修正明显的 OCR 识别错误"
    "3. 保留正文内容的完整性和阅读顺序"
    "4. 输出清洗后的纯文本，不要添加任何额外说明或标注"
)


class SnapshotCollector:
    """页面快照采集：Playwright 渲染 -> DOM 文本 -> 截图 OCR -> LLM 清洗。"""

    def __init__(self, settings: Settings, llm_provider=None) -> None:
        self.settings = settings
        self.llm = llm_provider
        self._ocr_engine = None
        self._ocr_checked = False

    async def capture(self, url: str) -> Candidate | None:
        """加载页面并提取文本，返回 Candidate 或 None。"""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.warning("playwright not installed, snapshot disabled")
            return None

        browser = None
        try:
           async with async_playwright() as p:
                # 优先使用系统已安装的 Chrome，避免等待 Playwright 浏览器下载
                try:
                    browser = await p.chromium.launch(headless=True, channel="chrome")
                except Exception:  # noqa: BLE001
                    browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                try:
                    await page.goto(url, wait_until="networkidle", timeout=int(self.settings.snapshot_timeout_seconds * 1000))
                except Exception:  # noqa: BLE001
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=int(self.settings.snapshot_timeout_seconds * 1000))
                    except Exception:  # noqa: BLE001
                        logger.warning("snapshot: page navigation failed for %s", url)
                        await browser.close()
                        return None

                title = await page.title()
                body_text = await self._extract_dom_text(page)

                if len(body_text) > 200:
                    await browser.close()
                    cleaned = await self._llm_clean(body_text)
                    return Candidate(url=url, title=(title or url)[:500], text=cleaned or body_text)

                ocr_text = await self._screenshot_ocr(page)
                await browser.close()

                combined = body_text
                if ocr_text:
                    combined = ocr_text if not combined else f"{combined}\n{ocr_text}"
                if not combined.strip():
                    return None
                cleaned = await self._llm_clean(combined)
                return Candidate(url=url, title=(title or url)[:500], text=cleaned or combined)
        except Exception:
            logger.exception("snapshot capture failed for %s", url)
            return None
        finally:
            if browser is not None:
                try:
                   await browser.close()
                except Exception:
                    logger.debug("snapshot: browser close failed", exc_info=True)

    async def _extract_dom_text(self, page) -> str:
        """从渲染后的 DOM 提取可见文本。"""
        try:
            text = await page.inner_text("body")
            return self._clean_dom_text(text)
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _clean_dom_text(text: str) -> str:
        """清理 DOM 文本：去多余空白、常见噪声。"""
        noise = {"登录", "注册", "搜索", "首页", "下载APP", "关注", "点赞", "投币", "收藏", "分享", "弹幕"}
        lines = []
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            if line in noise:
                continue
            lines.append(line)
        return " ".join(lines)

    async def _screenshot_ocr(self, page) -> str:
        """全页截图并 OCR 识别文字。"""
        try:
            screenshot_bytes = await page.screenshot(full_page=True)
        except Exception:
            logger.exception("snapshot: screenshot failed")
            return ""
        engine = self._get_ocr_engine()
        if engine is None:
            logger.warning("snapshot: no OCR engine available, skipping OCR")
            return ""
        try:
            return engine(screenshot_bytes).strip()
        except Exception:
            logger.exception("snapshot: OCR failed")
            return ""

    def _get_ocr_engine(self):
        """懒加载 OCR 引擎，优先 RapidOCR，其次 Tesseract。"""
        if self._ocr_checked:
            return self._ocr_engine
        self._ocr_checked = True
        # 1. RapidOCR（纯 Python，无需外部二进制）
        try:
            from rapidocr_onnxruntime import RapidOCR

            rapid = RapidOCR()

            def _rapid_ocr(img_bytes: bytes) -> str:
                import PIL.Image

                image = PIL.Image.open(io.BytesIO(img_bytes))
                result, _elapsed = rapid(image)
                if not result:
                    return ""
                return "\n".join(item[1] for item in result if item and len(item) > 1)

            self._ocr_engine = _rapid_ocr
            logger.info("snapshot: using RapidOCR engine")
            return self._ocr_engine
        except ImportError:
            pass
        except Exception:
            logger.warning("snapshot: RapidOCR init failed, trying Tesseract", exc_info=True)
        # 2. Tesseract（需要外部二进制）
        try:
            import pytesseract

            if self.settings.tesseract_bin:
                pytesseract.pytesseract.tesseract_cmd = self.settings.tesseract_bin

            def _tesseract_ocr(img_bytes: bytes) -> str:
                import PIL.Image

                image = PIL.Image.open(io.BytesIO(img_bytes))
                return pytesseract.image_to_string(image, lang="chi_sim+eng")

            self._ocr_engine = _tesseract_ocr
            logger.info("snapshot: using Tesseract engine")
            return self._ocr_engine
        except ImportError:
            pass
        except Exception:
            logger.warning("snapshot: Tesseract init failed", exc_info=True)
        return None

    async def _llm_clean(self, text: str) -> str:
        """用 LLM 清洗 OCR/DOM 文本噪声。"""
        if not self.llm or not text.strip():
            return ""
        try:
            from app.llm.provider import ChatMessage

            result = await asyncio.wait_for(
                self.llm.chat(
                    [ChatMessage("system", _CLEAN_PROMPT), ChatMessage("user", text[:4000])],
                    temperature=0.3,
                ),
                timeout=15.0,
            )
            return result.strip()
        except Exception:  # noqa: BLE001
            logger.warning("snapshot: LLM cleanup failed, using raw text")
            return ""
