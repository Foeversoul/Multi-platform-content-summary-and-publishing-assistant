from app.adapter.platforms import PlatformConfig
from app.adapter.rules import validate_text


def score_copy(platform: PlatformConfig, text: str, compliance: dict) -> dict:
    rules = validate_text(platform, text)
    style = 100
    if not rules.length_ok:
        style -= 20
    if not rules.tags_ok:
        style -= 15
    if not rules.emojis_ok:
        style -= 15
    style -= 10 * min(len(compliance["sensitive_hits"]), 3)
    style -= 10 * min(len(compliance["ad_hits"]), 3)
    style = max(0, style)
    return {
        "length": rules.length,
        "length_ok": rules.length_ok,
        "tags": rules.tags,
        "tags_ok": rules.tags_ok,
        "emojis": rules.emojis,
        "emojis_ok": rules.emojis_ok,
        "sensitive_hits": compliance["sensitive_hits"],
        "ad_hits": compliance["ad_hits"],
        "style_score": style,
    }
