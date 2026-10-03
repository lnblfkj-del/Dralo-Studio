"""Restricted paragraph/list document; never accepts arbitrary HTML or attributes."""
from typing import Any


def validate_synopsis_document(value: dict[str, Any]) -> dict[str, Any]:
    nodes = 0
    text_size = 0

    def visit(node: Any, depth: int, parent: str | None) -> None:
        nonlocal nodes, text_size
        nodes += 1
        if depth > 24 or nodes > 10000 or not isinstance(node, dict):
            raise ValueError("梗概文档结构过深或过大")
        kind = node.get("type")
        allowed = {None: {"doc"}, "doc": {"paragraph", "bulletList", "orderedList"}, "paragraph": {"text", "hardBreak"}, "bulletList": {"listItem"}, "orderedList": {"listItem"}, "listItem": {"paragraph", "bulletList", "orderedList"}}
        if not isinstance(kind, str) or kind not in allowed.get(parent, set()):
            raise ValueError("梗概包含不支持的内容类型")
        if set(node) - {"type", "content", "text", "marks", "attrs"}:
            raise ValueError("梗概包含不支持的字段")
        attrs = node.get("attrs") or {}
        if not isinstance(attrs, dict):
            raise ValueError("梗概属性无效")
        if attrs and (kind != "orderedList" or set(attrs) - {"start", "type"} or attrs.get("type") is not None or not isinstance(attrs.get("start", 1), int) or not 1 <= attrs.get("start", 1) <= 10000):
            raise ValueError("梗概不允许自定义样式或链接")
        marks = node.get("marks") or []
        if not isinstance(marks, list) or any(not isinstance(mark, dict) or set(mark) != {"type"} or not isinstance(mark["type"], str) or mark["type"] not in {"bold", "italic"} for mark in marks):
            raise ValueError("梗概仅支持加粗和斜体标记")
        if kind == "text":
            if not isinstance(node.get("text"), str) or node.get("content"):
                raise ValueError("文本节点无效")
            text_size += len(node["text"])
        elif "text" in node:
            raise ValueError("文本只能出现在文本节点")
        content = node.get("content", [])
        if not isinstance(content, list) or len(content) > 10000:
            raise ValueError("梗概节点无效")
        for child in content:
            visit(child, depth + 1, kind)

    visit(value, 0, None)
    if text_size > 20000:
        raise ValueError("梗概最多支持 20000 字符")
    return value


def synopsis_document_text(node: dict) -> str:
    if node["type"] == "text":
        return node.get("text", "")
    if node["type"] == "hardBreak":
        return "\n"
    children = [synopsis_document_text(child) for child in node.get("content", [])]
    return ("" if node["type"] == "paragraph" else "\n\n").join(children)
