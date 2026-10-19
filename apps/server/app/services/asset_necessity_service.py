"""Frozen necessity selection; no media generation or source rewriting."""


from copy import deepcopy

from pydantic import ValidationError as SchemaValidationError

from app.core.errors import ModelOutputBusinessValidationError, ValidationError
from app.schemas.asset_necessity import (
    AssetNecessityPolicy,
    CompactExtractionPolicy,
    CrossStyleExtractionPolicy,
    SourceOnlyCrowdPolicy,
    ReusableMaterialPolicy,
    IsolatedItemPolicy,
    PerformerNecessityPolicy,
    ProductionClosurePolicy,
    ProductionEvidencePolicy,
    ScopedCostumePolicy,
    StableVoiceNecessityPolicy,
)
from app.services.director_output_transport import digest

AUDIO_TYPES = {"character_voice", "music", "ambience", "sound_effect"}
ALL_TYPES = {"character", "costume", "scene", "prop", *AUDIO_TYPES}
CONTRACT_VERSION = 3


def freeze_policy(requested_types=None):
    requested = set(requested_types or [])
    independent = sorted(requested & AUDIO_TYPES) if requested != ALL_TYPES else []
    result = {**ReusableMaterialPolicy().model_dump(), "independent_audio_types": independent}
    result["sha256"] = digest(result)
    return result


def validate_policy(value):
    schemas = {"asset-necessity.nc0.v1": AssetNecessityPolicy,
               "asset-necessity.nc3.v2": StableVoiceNecessityPolicy,
               "asset-necessity.nc3.v3": PerformerNecessityPolicy,
               "asset-necessity.nc3.v4": ProductionEvidencePolicy,
               "asset-necessity.nc3.v5": IsolatedItemPolicy,
               "asset-necessity.nc3.v6": ScopedCostumePolicy,
               "asset-necessity.nc3.v7": ProductionClosurePolicy,
               "asset-necessity.nc3.v8": CompactExtractionPolicy,
               "asset-necessity.nc3.v9": CrossStyleExtractionPolicy,
               "asset-necessity.nc3.v10": SourceOnlyCrowdPolicy,
               "asset-necessity.nc3.v11": ReusableMaterialPolicy}
    schema = schemas.get(value.get("version"), AssetNecessityPolicy) if isinstance(value, dict) else AssetNecessityPolicy
    if not isinstance(value, dict) or set(value) != {*schema.model_fields, "independent_audio_types", "sha256"}:
        raise ValidationError("必要资产政策缺失或格式无效，未调用模型")
    try:
        schema.model_validate({key: value[key] for key in schema.model_fields})
    except SchemaValidationError as exc:
        raise ValidationError("必要资产政策规则无效，未调用模型") from exc
    types = value["independent_audio_types"]
    if (not isinstance(types, list) or any(not isinstance(kind, str) for kind in types)
            or not set(types) <= AUDIO_TYPES or types != sorted(set(types))
            or value["sha256"] != digest({key: item for key, item in value.items() if key != "sha256"})):
        raise ValidationError("必要资产政策与冻结记录不一致，未调用模型")
    return value


def instruction(policy):
    validate_policy(policy)
    explicit = policy["independent_audio_types"]
    form_rule = (
        "costume表示服装/造型，不仅限于衣服：原文明示且需保持一致的变身形态、机体形态、"
        "虚拟形象、不同年龄阶段也用costume分别管理，character_name指向同一角色；"
        "stage记录原文形态或阶段，appearance记录该状态的外观，并引用各自出现的原文单元。"
        "同一角色只有一个身份，不能把变身拆成陌生角色，不能把不同阶段混成一张造型。"
        "不得只在角色description或performance提及变化而遗漏其必要造型。"
        "若只有一种固定外观且无切换需求，保留角色外观即可，不额外复制造型资产。"
        "美术风格不构成新增剧情事实；机器人、虚拟角色、动物不套人类肤色、性别或衣着默认值。"
        if policy.get("visual_form_scope") else ""
    )
    performer_rule = (
        "实际出镜的无名角色与群演也必须建档，不因临时出场或没有姓名而排除。"
        "单人、小组、人群都用character；character_form分别为single、fixed_group、crowd。"
        "fixed_group指可辨认且需保持成员一致的2至12人小组，group_size须为原文明示人数；"
        "crowd保持群体风格，不逐个建人，也不保证全部面孔跨镜头不变。"
        "群体group_label须逐字引用其出场证据中的身份称呼，不能仅按弟子/群众等泛称跨阵营合并；"
        "group_size只能是整数或null，绝不能返回字符串；数千、若干、一群等不精确数量必须为null，不猜具体整数。"
        "single的group_label和group_size必须为null；fixed_group须有同组明确人数，crowd的参考图样本数不是剧情人数。"
        "只有环境声/环境音/旁白/音效行中的欢呼、喝彩或鼓掌，不能证明群演出镜；"
        "只有明确动作、画面或人物出场行才是视觉群演依据；本批没有这样的证据就不提取，不借用未提供的后续集。"
        "visible_performer是necessity.reasons中的理由枚举，仅用于有原文出镜依据的character，"
        "不是资产顶层的布尔字段。普通人群不自动配TTS。"
        if policy.get("visible_performers") else "临时路人不无条件建独立资产。"
    )
    voice_rule = (
        "角色的已确认固定声线由服务端关联为文字声线档案，不必重复提取或生成TTS；"
        "正文新增的明确声线可用fixed_voice提取，character_name必须引用原文角色。"
        if policy.get("stable_voice_profiles") else
        "固定声线通常放在角色facts，不自动再建同名character_voice；"
    )
    reusable_rule = (
        "资产清单管理可复用的一致性素材，不代替完整剧本。"
        "只返回必要身份、造型、固定空间、关键道具及其最短原文依据，不扩写详细图片提示词。"
        "姿态、表情、站位、一次性动作不是新角色身份或独立造型；动作细节完整留在原文。"
        "真正的服装切换、破损和形态变化仍分别保留，不合并不同造型。"
        if policy.get("candidate_review") else ""
    )
    return (
        reusable_rule +
        "先判断独立管理必要性，再输出资产；不要先枚举全部物件或声音。"
        "主要角色、稳定身份、必须保持外观的服装/场景、剧情关键道具、"
        "用户明确指定独立制作或复用的素材才建候选。"
        + performer_rule + "普通背景物件、"
        "关门/脚步/雨声、普通设备或AI播报、普通对白/旁白、泛化情绪配乐不建独立资产，"
        "这些内容仍完整留在script，后续按文字交给视频模型或明确后期方案。"
        "频繁出现本身不是资产化理由；一次出现的关键证物仍须提取。"
        "逐段回查决定行动成败的实体：攻防武器、逃脱或救治工具、证物、机关与可独立操控的关键部件，"
        "不能因为仅出现一次就归为普通背景，也不能只输出几件代表性道具而遗漏其余关键物。普通螺丝等附属零件不逐个拆卡。"
        "每个动作单元按动作的施加物、受作用物和关键操作部件回查：武器、布阵/封锁器物、牵引/举升/转动的机关控制件。"
        "不要把这些具体实体只写在人物performance中却漏掉必要道具；同一句多人和多个独立实体逐项判断。"
        "镜中或监控画面明确呈现的实际关键器物也可有出场依据；单纯比喻、设想、未发生的台词描述不算。"
        "有明确外观且需要固定或切换的角色服装必须提取costume；明确换装、烧毁、损坏等造型分别记录穿戴者和状态。"
        + form_rule +
        "每项必须含necessity对象：reasons（1至3项）、source_segment_id、逐字evidence_text、"
        "简短rationale，可含user_requirement_id。reasons仅为stable_identity、fixed_appearance、"
        "fixed_voice、plot_critical、reuse_media、independent_control、user_required"
        + ("、visible_performer。" if policy.get("visible_performers") else "。") +
        "necessity证据须来自本资产occurrences所覆盖的script。"
        + voice_rule +
        "普通音效仍只有指定素材、独立音频控制或明确制作要求才建声音资产。"
        "身份、原文台词、发声来源、动作与时机不能因不建资产而删除或改写。"
        "本次明确要求独立管理的声音类别为：" + ",".join(explicit) + "。"
        "仅这些类别可使用user_required，并写user_requirement_id为category:类别；"
        "没有对应要求不得声称用户指定。其他必要性来自原文，不能凭频率或模型偏好编造。"
        "没有必要独立资产时返回{\"assets\":[]}，不要为了凑数建卡。"
    )


def validate_necessity(asset, sources, policy):
    validate_policy(policy)
    value = asset.necessity
    if "visible_performer" in value.reasons and (not policy.get("visible_performers") or asset.kind != "character"):
        raise ModelOutputBusinessValidationError("出镜群演理由只能用于当前政策允许的角色资产，未入库")
    source = sources.get(value.source_segment_id)
    covered = {row.source_segment_id for row in asset.occurrences}
    if (source is None or value.source_segment_id not in covered
            or value.evidence_text not in source["text"] or not value.evidence_text.strip()):
        raise ModelOutputBusinessValidationError("必要性依据未命中本资产原文范围，未入库",
                                                  details={"field": "necessity.evidence_text", "contract_version": 3})
    relation = None
    if policy.get("evidence_relation"):
        from app.services.asset_evidence_relation import necessity_relation
        relation = necessity_relation(asset, source, version=policy["evidence_relation"])
    elif value.source_unit_id:
        from app.services.asset_source_units import named_header_reference, unit_source
        text = unit_source(source, value.source_unit_id)["text"]
        if not named_header_reference(asset.kind, asset.name, text) and not any(row.source_segment_id == value.source_segment_id and (
            row.source_unit_id == value.source_unit_id or (
                not row.source_unit_id and row.evidence_text.strip() and row.evidence_text in text
            )) for row in asset.occurrences):
            raise ModelOutputBusinessValidationError("必要性原文单元不在本资产出场依据中, 未写入资产",
                details={"field": "necessity.source_unit_id", "contract_version": 3})
    if "fixed_voice" in value.reasons and asset.kind != "character_voice":
        raise ModelOutputBusinessValidationError("固定声线理由只能用于独立角色声音需求，未入库")
    unused_reasons = [reason for reason in value.reasons if reason == "stable_identity" and asset.kind != "character"]
    applied_reasons = [reason for reason in value.reasons if reason not in unused_reasons]
    if unused_reasons and not applied_reasons:
        raise ModelOutputBusinessValidationError("稳定角色身份不能作为普通物件或音效的资产化理由，未入库")
    requirement = f"category:{asset.kind}"
    if "user_required" in value.reasons:
        if asset.kind not in policy["independent_audio_types"] or value.user_requirement_id != requirement:
            raise ModelOutputBusinessValidationError("模型声称的用户指定需求不存在，未入库",
                                                      details={"field": "necessity.user_requirement_id", "contract_version": 3})
    elif value.user_requirement_id:
        raise ModelOutputBusinessValidationError("必要性依据引用了未声明的用户需求，未入库")
    return {**value.model_dump(), "reasons": applied_reasons,
            **({"entity_relation": relation, "relation_version": policy["evidence_relation"]} if relation else {}),
            **({"model_reasons": list(value.reasons), "unapplied_reasons": unused_reasons,
                "reason_normalization": "redundant-kind-tags.v1"} if unused_reasons else {}),
            "policy_version": policy["version"], "policy_sha256": policy["sha256"], "source_verified": True}


def coverage_context(context, policy=None):
    """Functional speaker identities remain in source catalog, not required asset cards."""
    if not policy:
        return context
    validate_policy(policy)
    if policy.get("visible_performers"):
        return context
    result = deepcopy(context)
    bible = (context.get("confirmed_story_bible") or {}).get("content") or {}
    optional_names = {str(row.get("name") or "").casefold() for row in bible.get("characters") or []
                      if isinstance(row, dict) and row.get("importance") == "functional"}
    result["character_appearances"] = [row for row in context.get("character_appearances") or []
                                       if str(row.get("character_name") or "").casefold() not in optional_names]
    return result


def requires_image(asset):
    attributes = asset.get("attributes") or {}
    if attributes.get("visual_required"):
        return True
    if attributes.get("extraction_contract_version") == 3 and attributes.get("media_delivery") == "description":
        return False
    return asset["asset_type"] in {"character", "scene"}
