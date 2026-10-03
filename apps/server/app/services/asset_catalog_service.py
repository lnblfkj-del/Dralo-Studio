"""Project and global asset catalog operations."""

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError
from app.models import Asset, AssetVersion, MediaFile, Project, ProjectAssetLink

BASE_IMAGE_REQUIREMENTS = {
    "character": "角色身份正面参考图；单一角色，胸部以上半身正面构图，面部居中且清晰无遮挡，身份、五官、体型、发型与基础服装稳定；中性表情，均匀柔和的影棚光线，浅灰色无缝中性背景；禁止坐姿、戏剧动作、浅景深、逆光光晕、场景化背景、多宫格、拼版、文字、边框、水印或额外人物。",
    "costume": "展示该造型的完整穿着效果，主体无遮挡，服装、发型和妆容结构清楚，干净中性背景，无文字、水印或额外人物。",
    "scene": "纯场景环境图，不出现人物或临时表演；保留场景固定建筑、陈设、空间关系和既定光线，无文字、水印。",
    "prop": "单一道具完整展示，白色或透明感中性背景，轮廓、比例、材质和关键细节清楚，无手持人物、文字或水印。",
    "reference": "完整单幅镜头构图，明确人物、场景、道具和空间关系；这是镜头帧，不使用白底资产展示构图，无文字、水印。",
}

DERIVED_IMAGE_REQUIREMENTS = {
    ("character", "appearance"): "单一角色从头到脚完整入镜的正面全身造型参考图；自然直立，双臂与身体略微分开，脸部无遮挡，身份与五官稳定，服装、发型和妆容结构清楚；均匀影棚光线，浅灰色无缝中性背景；禁止裁切头顶、手、脚，禁止坐姿、戏剧动作、浅景深、多宫格、拼版、文字、水印或额外人物。",
    ("character", "expression"): "单一角色近景，身份与五官稳定，清楚呈现指定表情，干净中性背景，无文字、水印或额外人物。",
    ("character", "state"): "单一角色，身份稳定，清楚呈现指定剧情状态及必要外观变化，不加入额外人物、文字或水印。",
    ("character", "angle"): "单一角色从头到脚完整入镜，身份、五官、体型、发型和服装与身份主图严格一致，按提示严格呈现指定观察角度；自然直立，均匀影棚光线，浅灰色无缝中性背景；禁止裁切、多宫格、拼版、文字、水印或额外人物。",
    ("scene", "environment"): "同一纯场景的环境变体，固定建筑、陈设和空间关系不变，只改变指定日夜、天气或光线，不出现人物、文字或水印。",
    ("prop", "state"): "同一道具的指定剧情状态，轮廓、比例和材质身份保持一致，白色或中性背景，无手持人物、文字或水印。",
    ("character", "layout_sheet"): "排版预览图，可包含同一角色的多视角或表情分格；各分格边界清楚，仅用于后续拆分，不作为可直接采用的单幅角色素材。",
    ("costume", "layout_sheet"): "排版预览图，可包含同一造型的多视角或细节分格；各分格边界清楚，仅用于后续拆分，不作为可直接采用的单幅造型素材。",
    ("scene", "layout_sheet"): "排版预览图，可包含同一场景的多角度或环境分格；各分格边界清楚，仅用于后续拆分，不作为可直接采用的单幅场景素材。",
    ("prop", "layout_sheet"): "排版预览图，可包含同一道具的多角度或状态分格；各分格边界清楚，仅用于后续拆分，不作为可直接采用的单幅道具素材。",
    ("reference", "layout_sheet"): "排版预览图，可包含多个镜头格；各分格边界清楚，仅用于后续拆分，不作为可直接采用的单幅镜头帧。",
}

ASSET_VIEW_LABELS = {
    "base": "基础视图", "appearance": "造型视图", "expression": "表情视图",
    "state": "状态视图", "angle": "角度视图", "environment": "环境视图",
    "detail": "细节视图", "first_frame": "首帧", "last_frame": "尾帧",
    "key_frame": "关键帧", "storyboard_frame": "分镜参考帧",
    "layout_sheet": "排版预览（待拆分）",
}
ASSET_VIEW_TYPES = {
    "character": {"base", "appearance", "expression", "state", "angle", "detail", "layout_sheet"},
    "costume": {"base", "appearance", "state", "angle", "detail", "layout_sheet"},
    "scene": {"base", "angle", "environment", "detail", "layout_sheet"},
    "prop": {"base", "state", "angle", "detail", "layout_sheet"},
    "reference": {"base", "first_frame", "last_frame", "key_frame", "storyboard_frame", "layout_sheet"},
    "voice": {"base"}, "video": {"base"}, "canvas": {"base"},
}
FRAME_VIEW_TYPES = {"first_frame", "last_frame", "key_frame", "storyboard_frame"}
LAYOUT_SHEET_VIEW_TYPE = "layout_sheet"
LAYOUT_SHEET_HINTS = ("三视图", "多视图", "九宫格", "拼图", "排版", "contact sheet", "model sheet", "turnaround")

CHARACTER_REFERENCE_SHEET_KEY = "character-reference-sheet.v2"
ASSET_REFERENCE_CONTRACTS = {
    "scene": "scene-reference.v1",
    "prop": "prop-reference.v1",
    "costume": "costume-reference.v1",
}
ASSET_REFERENCE_NEGATIVE_PROMPTS = {
    "scene": "人物，人群，剧情动作，多宫格，拼图，鱼眼畸变，建筑线条倾斜，浅景深，背景虚化，裁切关键建筑或地貌边界，文字，标识，水印",
    "prop": "人物，手持，多个道具，多宫格，拼图，道具贴边，裁切轮廓，裁切边角，局部特写，剧情场景，浅景深，文字，水印",
    "costume": "角色换脸，角色体型改变，多个人物，坐姿，戏剧动作，多宫格，拼图，裁切头饰，裁切手部，裁切鞋底，裁切长袍或裙摆，人物贴边，剧情场景，文字，水印",
}
CHARACTER_REFERENCE_SHEET_VIEWS = (
    {
        "key": "reference_sheet",
        "view_type": "base",
        "view_label": "角色设定图（正面 / 侧面 / 背面 / 半身）",
        "prompt_suffix": "生成一张横向角色设定图，在同一画布内依次排布半身面部特写、全身正面、全身侧面、全身背面。",
        "aspect_ratio_preferences": ("3:2", "16:9", "4:3"),
    },
)


def character_reference_sheet_views() -> tuple[dict[str, Any], ...]:
    return CHARACTER_REFERENCE_SHEET_VIEWS


def character_reference_view(view_key: str) -> dict[str, Any]:
    for view in CHARACTER_REFERENCE_SHEET_VIEWS:
        if view["key"] == view_key:
            return view
    raise ConflictError(f"未知角色参考视图：{view_key}")


def character_reference_prompt(identity_prompt: str, view_key: str) -> str:
    view = character_reference_view(view_key)
    return (
        f"角色身份设定：\n{identity_prompt.strip()}\n\n"
        f"本次视图：{view['prompt_suffix']}\n\n"
        f"角色参考资产硬性契约（{CHARACTER_REFERENCE_SHEET_KEY}）：只生成一张横向图片，画面中只能出现同一名角色。"
        "使用四个不重叠的清晰分区，从左到右固定为：半身面部特写、全身正面、全身侧面、全身背面。"
        "左侧半身分区占画布宽度约28%，其余三个全身分区均分；分区之间保留明显空隙，角色不得跨区或互相遮挡。"
        "三个全身视图的人物高度不得超过画布高度的82%，上方头饰或发髻与画布边缘之间、下方鞋底或服装下摆与画布边缘之间，都必须保留至少8%的空白安全区。"
        "人物必须站在可见的统一地面线上，从头顶、头饰、发髻到鞋底、脚趾和长袍衣摆全部完整入镜，任何部位都不得触碰或超出画布边缘。"
        "半身分区只允许在胸口以下裁切，头顶、完整发型、头饰和双肩必须完整显示，顶部及左右至少保留6%空白。"
        "三个全身视图比例一致、自然直立；四个分区的脸型、五官、年龄感、"
        "体型、发型、服装、头饰和配件必须严格一致。使用均匀影棚光线与浅灰色无缝中性背景。"
        "禁止剧情场景、坐姿、动作表演、逆光、光晕、浅景深、背景虚化、贴边、越界、裁切头顶、裁切头饰、裁切鞋底、裁切脚趾、裁切衣摆、额外人物、文字、水印和装饰边框。"
        "项目美术风格只能影响材质、色彩、服装设计语言和渲染质感，不得覆盖上述排版与一致性要求。"
    )


def asset_generation_contract(asset_type: str) -> str | None:
    if asset_type == "character":
        return CHARACTER_REFERENCE_SHEET_KEY
    return ASSET_REFERENCE_CONTRACTS.get(asset_type)


def asset_reference_negative_prompt(asset_type: str) -> str | None:
    return ASSET_REFERENCE_NEGATIVE_PROMPTS.get(asset_type)


def infer_asset_view_type(asset_type: str, source_text: str | None) -> str | None:
    if asset_type not in {"character", "costume", "scene", "prop", "reference"}:
        return None
    normalized = (source_text or "").strip().casefold()
    return LAYOUT_SHEET_VIEW_TYPE if normalized and any(hint in normalized for hint in LAYOUT_SHEET_HINTS) else None


def normalize_asset_view(asset_type: str, view_type: str | None = None, view_label: str | None = None, *, source_text: str | None = None) -> tuple[str, str]:
    resolved_type = view_type or infer_asset_view_type(asset_type, source_text) or ("first_frame" if asset_type == "reference" else "base")
    if resolved_type not in ASSET_VIEW_TYPES.get(asset_type, {"base"}):
        raise ConflictError(f"{asset_type}资产不支持 {resolved_type} 视图")
    return resolved_type, (view_label or "").strip() or ASSET_VIEW_LABELS[resolved_type]


def asset_image_prompt(asset_type: str, prompt: str, view_type: str = "base") -> str:
    requirement = DERIVED_IMAGE_REQUIREMENTS.get((asset_type, view_type), BASE_IMAGE_REQUIREMENTS.get(asset_type))
    clean = prompt.strip()
    compiled = f"{clean}\n\n资产视图制作要求：{requirement}" if requirement else clean
    contract = ASSET_REFERENCE_CONTRACTS.get(asset_type)
    if view_type != "base" or contract is None:
        return compiled
    boundaries = {
        "scene": (
            "只生成一张连续空间的横向场景身份参考图，不做多视图拼版。使用平视广角全景，保持水平线和建筑垂直线稳定，"
            "前景、中景、后景及出入口的空间关系可读。固定建筑、地貌、陈设和地面边界必须完整，关键结构与画布边缘至少保留8%安全区。"
            "使用深景深和清晰均匀的环境光，不得出现人物、剧情动作，也不得擅自增加身份提示词中没有的季节、天气或时段。"
        ),
        "prop": (
            "只生成一张单一道具身份参考图，只出现一个完整道具，不做多视图拼版。使用最能说明结构的轻微三分之四视角，物体居中并稳定落地，"
            "整体轮廓最多占画布宽高的76%，四周至少保留12%空白安全区；不得裁切任何边角、附件或突出部件。"
            "比例、材质、连接结构、表面磨损和辨识特征必须清楚，使用柔和均匀的影棚光与浅灰或白色无缝背景，禁止手持、人物和剧情场景。"
        ),
        "costume": (
            "只生成一张单套服装造型身份参考图，使用已绑定角色参考图中的同一角色完整穿着，严格保持其物种、脸型、五官、年龄感、肤色、体型和发型，只改变服装、头饰、妆容与指定状态，不做多视图拼版。角色自然正面直立，双臂与躯干略微分开，"
            "整体高度不得超过画布高度的82%，头饰上方与鞋底或长袍、裙摆下方各保留至少8%空白；头饰、内外层、袖口、手套、腰带、配饰、下摆和鞋履必须完整显示。"
            "不得重新设计或美化角色脸孔与体型；使用均匀影棚光与浅灰无缝背景，禁止坐姿、动作表演和剧情场景。"
        ),
    }[asset_type]
    return (
        f"{compiled}\n\n{asset_type} 参考资产硬性契约（{contract}）：{boundaries}"
        "项目美术风格只能影响材质、色彩和渲染质感，不得覆盖上述构图、完整性、安全边距与主体数量要求。"
    )


async def list_assets(session: AsyncSession, project_id: int, asset_type: str | None = None) -> list[dict[str, Any]]:
    statement = select(Asset, ProjectAssetLink.local_slug).join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id).where(ProjectAssetLink.project_id == project_id)
    if asset_type is not None:
        statement = statement.where(Asset.asset_type == asset_type)
    rows = (await session.execute(statement.order_by(Asset.id))).all()
    return await _asset_outs_batch(session, [(asset, slug) for asset, slug in rows])


async def get_asset(session: AsyncSession, project_id: int, asset_id: int) -> Asset:
    asset = await session.scalar(select(Asset).join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id).where(Asset.id == asset_id, ProjectAssetLink.project_id == project_id))
    if asset is None:
        raise NotFoundError("资产不存在")
    return asset


async def get_project_assets(session: AsyncSession, project_id: int, asset_ids: list[int]) -> dict[int, Asset]:
    if not asset_ids:
        return {}
    rows = (await session.execute(select(Asset).join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id).where(Asset.id.in_(asset_ids), ProjectAssetLink.project_id == project_id))).scalars().all()
    return {asset.id: asset for asset in rows}


async def create_asset(session: AsyncSession, project: Project, data: dict[str, Any]) -> Asset:
    await ensure_slug_available(session, project.id, data["slug"])
    asset = Asset(project_id=project.id, owner_id=project.owner_id, **data)
    session.add(asset)
    await session.flush()
    session.add(ProjectAssetLink(project_id=project.id, asset_id=asset.id, local_slug=asset.slug))
    await session.flush()
    return asset


async def update_asset(session: AsyncSession, asset: Asset, data: dict[str, Any], project_id: int) -> Asset:
    if asset.project_id != project_id:
        raise ConflictError("复用资产的源资料只可在来源项目编辑；本项目请编辑制作资料副本")
    if "slug" in data and data["slug"] != asset.slug:
        await ensure_slug_available(session, project_id, data["slug"], asset.id)
        link = await get_project_link(session, project_id, asset.id)
        link.local_slug = data["slug"]
    for key, value in data.items():
        setattr(asset, key, value)
    await session.flush()
    return asset


async def delete_asset(session: AsyncSession, asset: Asset) -> list[Path]:
    from app.services.asset_production_service import guard_delete
    await guard_delete(session, asset.id)
    media_items = list((await session.execute(select(MediaFile).join(AssetVersion, AssetVersion.media_file_id == MediaFile.id).where(AssetVersion.asset_id == asset.id))).scalars())
    await session.delete(asset)
    await session.flush()
    from app.core.config import settings
    if settings.runtime_execution_location == "cloud":
        return []
    orphaned: list[Path] = []
    for media in media_items:
        still_referenced = await session.scalar(select(AssetVersion.id).where(AssetVersion.media_file_id == media.id).limit(1))
        if still_referenced is None:
            voice_reference = await session.scalar(select(Asset.id).where(Asset.owner_id == media.owner_id, Asset.attributes["canvas_profile"]["voice_media_id"].as_integer() == media.id).limit(1))
            if voice_reference is not None:
                continue
            orphaned.append(Path(media.file_path))
            await session.delete(media)
    await session.flush()
    return orphaned


async def ensure_slug_available(session: AsyncSession, project_id: int, slug: str, except_id: int | None = None) -> None:
    if not slug or not re.fullmatch(r"[\w\u4e00-\u9fff-]+", slug):
        raise ConflictError("引用名称只能包含文字、数字、下划线或连字符")
    statement = select(ProjectAssetLink.asset_id).where(ProjectAssetLink.project_id == project_id, ProjectAssetLink.local_slug == slug)
    if except_id is not None:
        statement = statement.where(ProjectAssetLink.asset_id != except_id)
    if await session.scalar(statement):
        raise ConflictError(f"引用名称 @{slug} 已存在")


async def to_asset_out(session: AsyncSession, asset: Asset, local_slug: str | None = None) -> dict[str, Any]:
    return (await _asset_outs_batch(session, [(asset, local_slug)]))[0]


async def _asset_outs_batch(session: AsyncSession, pairs: list[tuple[Asset, str | None]]) -> list[dict[str, Any]]:
    if not pairs:
        return []
    asset_ids = [asset.id for asset, _ in pairs]
    versions = list((await session.execute(select(AssetVersion).where(AssetVersion.asset_id.in_(asset_ids)).order_by(AssetVersion.asset_id, AssetVersion.version.desc()))).scalars())
    versions_by_asset: dict[int, list[AssetVersion]] = {}
    for version in versions:
        versions_by_asset.setdefault(version.asset_id, []).append(version)
    links = (await session.execute(select(ProjectAssetLink.asset_id, ProjectAssetLink.project_id).where(ProjectAssetLink.asset_id.in_(asset_ids)))).all()
    projects_by_asset: dict[int, list[int]] = {}
    for asset_id, project_id in links:
        projects_by_asset.setdefault(asset_id, []).append(project_id)
    return [{
        "id": asset.id, "project_id": asset.project_id, "owner_id": asset.owner_id, "asset_type": asset.asset_type,
        "name": asset.name, "slug": local_slug or asset.slug, "description": asset.description,
        "prompt_anchor": asset.prompt_anchor, "attributes": asset.attributes,
        "versions": versions_by_asset.get(asset.id, []), "created_at": asset.created_at,
        "updated_at": asset.updated_at, "linked_project_ids": projects_by_asset.get(asset.id, []),
    } for asset, local_slug in pairs]


async def expand_prompt(session: AsyncSession, project_id: int, prompt: str) -> tuple[str, list[dict[str, Any]]]:
    rows = (await session.execute(select(Asset, ProjectAssetLink.local_slug, ProjectAssetLink.production_data).join(ProjectAssetLink, ProjectAssetLink.asset_id == Asset.id).where(ProjectAssetLink.project_id == project_id).order_by(Asset.id))).all()
    referenced: list[Asset] = []
    expanded = prompt
    slugs: dict[int, str] = {}
    for asset, local_slug, production_data in sorted(rows, key=lambda item: len(item[1]), reverse=True):
        pattern = re.compile(rf"@{re.escape(local_slug)}(?![\w\u4e00-\u9fff-])")
        if not pattern.search(expanded):
            continue
        referenced.append(asset)
        slugs[asset.id] = local_slug
        data = production_data or {}
        anchor = data.get("prompt_anchor", asset.prompt_anchor) or asset.description or asset.name
        expanded = pattern.sub(f"{asset.name} ({anchor})", expanded)
    return expanded, await _asset_outs_batch(session, [(asset, slugs[asset.id]) for asset in referenced])


async def get_project_link(session: AsyncSession, project_id: int, asset_id: int) -> ProjectAssetLink:
    link = await session.scalar(select(ProjectAssetLink).where(ProjectAssetLink.project_id == project_id, ProjectAssetLink.asset_id == asset_id))
    if link is None:
        raise NotFoundError("项目资产关联不存在")
    return link
