import re

import jieba.posseg as pseg

FLAG_TO_CATEGORY = {
    "nr": "PERSON",
    "ns": "LOCATION",
    "nt": "ORG",
    "nz": "ORG",
    "m": "NUMBER",
    "t": "DATE",
}
DATE_RE = re.compile(r"\d{4}年\d{1,2}月\d{1,2}日|\d{4}-\d{2}-\d{2}|\d{1,2}月\d{1,2}日")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?%?|\d+(?:\.\d+)?[亿万]")


def extract_entities(text: str) -> dict[str, set[str]]:
    entities: dict[str, set[str]] = {}

    def add(category: str, value: str) -> None:
        value = value.strip()
        if value:
            entities.setdefault(category, set()).add(value)

    for word, flag in pseg.cut(text):
        category = FLAG_TO_CATEGORY.get(flag)
        if category:
            add(category, word)
    for match in DATE_RE.finditer(text):
        add("DATE", match.group())
    for match in NUMBER_RE.finditer(text):
        add("NUMBER", match.group())
    return entities
