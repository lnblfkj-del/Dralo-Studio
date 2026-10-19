"""Separate performer roles, group identity and image delivery contracts."""


import re

from app.core.errors import ModelOutputBusinessValidationError

GROUP_CONTRACT = "character-group-reference.v1"
GROUP_FIELDS = ("character_form", "group_label", "group_size")


def character_profile(attributes, data=None):
    attrs = attributes or {}
    overlay = (data or {}).get("profile") or {}
    return {key: overlay[key] if key in overlay else attrs.get(key) for key in GROUP_FIELDS}


def is_group(profile):
    return profile.get("character_form") in {"fixed_group", "crowd"}


def _count(text):
    digits = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if text.isdecimal():
        return int(text)
    if text in digits:
        return digits[text]
    if "十" in text and len(text) <= 3:
        tens, _, ones = text.partition("十")
        if (not tens or tens in digits) and (not ones or ones in digits):
            return digits.get(tens, 1) * 10 + digits.get(ones, 0)
    return None


def source_only_crowd(asset, policy, sources):
    """An uncharacterized background sound is not evidence for a visual cast."""
    if (not policy.get("audio_only_crowds") or asset.kind != "character"
            or asset.character_form != "crowd" or asset.group_size is not None
            or asset.facts or set(asset.necessity.reasons) != {"visible_performer"}):
        return False
    from app.services.asset_evidence_relation import record_span
    for record in [*asset.occurrences, asset.necessity]:
        source = sources.get(record.source_segment_id)
        span = record_span(source, record) if source else None
        if span is None:
            return False
        line = source["text"][source["text"].rfind("\n", 0, span[0]) + 1:].splitlines()[0].strip()
        if not re.match(r"^(?:【)?(?:环境声|环境音|音效)(?:】[：:]?|[：:])", line):
            return False
    return bool(asset.occurrences)


def extraction_fields(asset, policy, sources=None):
    if not policy.get("visible_performers"):
        return {}
    form = asset.character_form or "single"
    label, count = asset.group_label, asset.group_size
    label_source = None
    if asset.kind != "character":
        if asset.character_form or label or count:
            raise ModelOutputBusinessValidationError("非角色资产不能携带群演形式，未入库")
        return {}
    if sources and ("visible_performer" in asset.necessity.reasons or form in {"fixed_group", "crowd"}):
        visible = False
        for occurrence in asset.occurrences:
            source = sources[occurrence.source_segment_id]["text"]
            start = source.find(occurrence.evidence_text)
            line = source[source.rfind("\n", 0, start) + 1:].splitlines()[0].strip()
            if not re.match(r"^(?:【)?(?:环境声|环境音|音效|配乐(?:BGM)?|BGM|旁白|广播)(?:】[：:]?|[：:])", line, re.I):
                visible = True
        if not visible:
            raise ModelOutputBusinessValidationError("仅声音或旁白依据不能确认群演出镜，未入库")
    if form == "single":
        if label or count:
            raise ModelOutputBusinessValidationError("单人角色不能携带群体人数或称呼，未入库")
        return {"character_form": "single"}
    verified_quotes = [row.evidence_text for row in asset.occurrences
                       if sources and row.evidence_text in sources[row.source_segment_id]["text"]]
    if not label and any(asset.name in quote for quote in verified_quotes):
        label = asset.name
        label_source = "source_name_exact"
    if not label or (form == "fixed_group" and (count is None or count > 12)):
        raise ModelOutputBusinessValidationError("固定小组须有原文人数，群体须有原文称呼，未入库")
    quotes = [row.evidence_text for row in asset.occurrences]
    if not any(label in quote for quote in quotes):
        raise ModelOutputBusinessValidationError("群体称呼未命中出场证据，未入库")
    def count_matches(quote):
        for match in re.finditer(r"(?<![\d一二两三四五六七八九十百千万数几])([\d一二两三四五六七八九十]+)\s*(?:名|位|个|人)", quote):
            # A literal group label may already contain its count. Match it at
            # this exact count span, not after it or elsewhere in the sentence.
            if (_count(match.group(1)) == count and quote.startswith(label, match.start())
                    and len(label) > match.end() - match.start()):
                return True
            tail = quote[match.end():]
            position = tail.find(label)
            if (_count(match.group(1)) == count and 0 <= position <= 100
                    and not re.search(r"[。！？；\n]|[\d一二两三四五六七八九十]+\s*(?:名|位|个|人)|[与和对]|围住|围攻|攻击|护送|抓住|走向|面对|看向|袭击|迎接", tail[:position])):
                return True
        return False
    count_quotes = verified_quotes if sources else quotes
    named_rosters = []
    for quote in count_quotes:
        if re.match(r"^\s*(?:人物|角色|出场人物)[：:]", quote):
            people = re.split(r"[、,，]", re.split(r"[：:]", quote, maxsplit=1)[1].strip())
            members = sorted({person.strip() for person in people if re.fullmatch(
                re.escape(label) + r"[甲乙丙丁戊己庚辛壬癸]", person.strip())})
            open_roster = any(label in person and person.strip() not in members for person in people)
            if count is not None and len(members) == count and not open_roster:
                named_rosters.append(members)
    if count is not None and not any(count_matches(quote) for quote in count_quotes) and not named_rosters:
        raise ModelOutputBusinessValidationError("群体人数没有逐字原文依据，未入库")
    return {"character_form": form, "group_label": label, "group_size": count,
            **({"group_count_source": "explicit_named_roster.v1", "group_members": named_rosters[0]} if named_rosters else {}),
            **({"group_label_source": label_source} if label_source else {})}


def group_prompt(prompt, profile):
    marker = f"群体参考资产契约（{GROUP_CONTRACT}）"
    if marker in prompt:
        return prompt
    fixed = profile.get("character_form") == "fixed_group"
    count = profile.get("group_size")
    population = (f"画面恰好展示{count}名不同成员，不增减人数；跨镜头沿用这些成员的身份、服装和相互差异。"
                  if fixed and count else
                  "画面展示少量可辨识的不同成员作为群体样本，不是现场人数，也不替代正文中的真实人数；"
                  "前景成员需要固定身份时另建单人资产，大规模背景仅保持群体风格与人口构成。")
    if not fixed and count:
        population += f"正文人数为{count}人，不要求这张样本参考图画出所有人。"
    return (
        prompt.strip() + f"\n\n{marker}：{population}"
        "本群体契约优先于单人四视图格式；剧情动作只是来源说明，参考图中自然站立，不表演剧情。"
        "同一连续画面排列不同成员，每位只出现一次，不生成同一成员的正侧背四视图。"
        "成员共享已确认的群体身份、年代、服装规制和美术风格；"
        "在已确认物种允许的范围内，脸型、眉眼、发型、身高、体型或外壳细节应各有区别；"
        "允许变化的服装细节可合理区分，禁止复制脸和相邻克隆成员。"
        "年龄、性别、族裔、物种只遵循原文及已确认设计，不无条件添加男女老幼、儿童或人类面孔。"
        "未说明的长相是美术设计建议，不冒充原文事实。"
        "人物不互相遮挡，从头到脚完整展示，面部可读，均匀光线，中性背景；无主角混入、无文字水印。"
    )
