"""Remove internal identity markers from generated state lines, not saved documents."""

import re


def readable_prompt(text: str) -> str:
    return re.sub(r"[ \t]*\[asset-\d+\](?=\s*(?:位置|姿态|服装|持有者|在场状态)[：:])", "", text)
