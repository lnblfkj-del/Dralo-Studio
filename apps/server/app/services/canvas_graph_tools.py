"""Graph tools with the same padding/order/gaps as canvasStore menu actions."""

# ruff: noqa: RUF001
from math import ceil, sqrt

from app.core.errors import ConflictError
from app.schemas.canvas import CanvasSave
from app.services import canvas_service


def size(node):
    return node.get("width") or (520 if node["type"] == "frame" else 220), node.get("height") or (
        320 if node["type"] == "frame" else 120
    )


async def apply(session, project, snapshot, command, allowed):
    nodes = {n["id"]: n for n in snapshot["nodes"]}
    ids = command.node_ids if command.operation != "ungroup" else [command.node_id]
    if len(ids) != len(set(ids)) or any(i not in nodes or i not in allowed for i in ids):
        raise ConflictError("布局目标不存在、重复或超出确认范围")
    affected = set(ids)
    # Moving a frame moves its children; all must be in scope and unlocked.
    for _ in range(len(nodes)):
        expanded = affected | {n["id"] for n in nodes.values() if n.get("parent_id") in affected}
        if expanded == affected:
            break
        affected = expanded
    if not affected <= allowed:
        raise ConflictError("分组包含未确认的节点")
    for key in affected:
        node = await canvas_service.get_canvas_node(session, project.id, key)
        await canvas_service.assert_node_unlocked(session, project, node)
    selected = [nodes[i] for i in ids]
    if command.operation == "ungroup":
        frame = nodes[command.node_id]
        if frame["type"] != "frame" or frame.get("parent_id"):
            raise ConflictError("只能解散顶层组合")
        for node in nodes.values():
            if node.get("parent_id") == frame["id"]:
                node["x"] += frame["x"]
                node["y"] += frame["y"]
                node["parent_id"] = None
        snapshot["nodes"] = [n for n in snapshot["nodes"] if n["id"] != frame["id"]]
        snapshot["edges"] = [
            e for e in snapshot["edges"] if frame["id"] not in (e["source"], e["target"])
        ]
    else:
        if len(selected) < 2:
            raise ConflictError("请选择至少两个节点")
        x, y = min(n["x"] for n in selected), min(n["y"] for n in selected)
        if command.operation == "group":
            if command.node_id in nodes or any(
                n.get("parent_id") or n["type"] == "frame" for n in selected
            ):
                raise ConflictError("打组需要未分组节点和新的组合编号")
            frame = {
                "id": command.node_id,
                "type": "frame",
                "x": x - 40,
                "y": y - 60,
                "width": max(360, max(n["x"] + size(n)[0] for n in selected) - x + 80),
                "height": max(240, max(n["y"] + size(n)[1] for n in selected) - y + 100),
                "data": {"title": command.title},
            }
            for n in selected:
                n["x"] -= frame["x"]
                n["y"] -= frame["y"]
                n["parent_id"] = frame["id"]
            snapshot["nodes"].insert(0, frame)
        else:
            parents = {n.get("parent_id") for n in selected}
            if len(parents) != 1:
                raise ConflictError("只能排列同一层级节点")
            ordered = sorted(selected, key=lambda n: (n["y"], n["x"], n["id"]))
            columns = (
                len(ordered)
                if command.layout == "horizontal"
                else 1
                if command.layout == "vertical"
                else ceil(sqrt(len(ordered)))
            )
            widths, heights = [0] * columns, [0] * ceil(len(ordered) / columns)
            for index, n in enumerate(ordered):
                widths[index % columns] = max(widths[index % columns], size(n)[0])
                heights[index // columns] = max(heights[index // columns], size(n)[1])
            for index, n in enumerate(ordered):
                n["x"] = x + sum(v + 32 for v in widths[: index % columns])
                n["y"] = y + sum(v + 32 for v in heights[: index // columns])
            parent = next(iter(parents))
            if parent:
                if parent not in allowed:
                    raise ConflictError("排列需要调整所属组合，请把组合加入确认范围")
                frame = nodes[parent]
                children = [n for n in nodes.values() if n.get("parent_id") == parent]
                frame["width"] = max(
                    size(frame)[0], max(n["x"] + size(n)[0] for n in children) + 40
                )
                frame["height"] = max(
                    size(frame)[1], max(n["y"] + size(n)[1] for n in children) + 40
                )
    return await canvas_service.save_snapshot(
        session,
        project,
        CanvasSave(
            expected_revision=snapshot["revision"],
            viewport=snapshot["viewport"],
            nodes=snapshot["nodes"],
            edges=snapshot["edges"],
        ),
    )
