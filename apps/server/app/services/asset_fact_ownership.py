"""Conservative character-fact ownership, separate from literal quote matching."""

# ruff: noqa: RUF001

import re

from app.services.screenplay_source_parser import SCENE, SCENE_SLUG, split_cue, unwrap

OWNED_FIELDS = {"role", "age", "gender", "appearance", "costume", "hair", "makeup", "injury", "stage"}
NARRATIVE_LABELS = {"动作", "画面", "人物", "角色", "出场人物", "场景", "旁白", "说明"}


def character_fact_owner(asset, fact, source, alignment, names, *, resolve_pronouns=False):
    if asset.kind != "character" or fact.field not in OWNED_FIELDS:
        return "not_applicable"
    start = alignment["start"] - source["start"]
    end = alignment["end"] - source["start"]
    text = source["text"]
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", max(line_start, end - 1))
    line = text[line_start:line_end if line_end >= 0 else len(text)]
    targets = {asset.name, getattr(asset, "group_label", None)} - {None, ""}
    cast = []
    for preceding_line in text[:start].splitlines():
        preceding_line = unwrap(preceding_line)
        parts = split_cue(preceding_line)
        cue = unwrap(parts[0]) if parts else ""
        if SCENE_SLUG.fullmatch(preceding_line) or SCENE.fullmatch(cue):
            cast = []
        elif parts and cue in {"人物", "角色", "出场人物"}:
            cast.append(parts[1])
    if cast and cast[-1].strip() == asset.name:
        targets.update(alias for alias in getattr(asset, "aliases", [])
                       if len(alias) >= 2 and alias in asset.name
                       and not any(alias in name for name in names - {asset.name}))
        targets.update(asset.name[index:] for index in range(1, len(asset.name) - 1)
                       if "的" + asset.name[index:] in line
                       and not any(asset.name[index:] in name for name in names - {asset.name}))
    label = re.match(r"^\s*(?:【)?([^：:】]{1,80})(?:】)?\s*[：:]", line)
    if label and label[1].strip() not in NARRATIVE_LABELS:
        stage = re.match(r"\s*[（(]([^）)]*)[）)]", line[label.end():])
        if fact.field != "role" and label[1].strip() in targets and stage and fact.value in stage[1]:
            return "explicit_subject"
        return "unresolved"
    value_offset = line.find(fact.value, max(0, start - line_start))
    if value_offset < 0:
        return "unresolved"
    prefix = re.split(r"[。！？!?；;]", line[:value_offset])[-1]
    if fact.field == "role" and any(target in fact.value for target in targets):
        return "explicit_subject"
    if fact.field == "costume" and re.search(r"抓着|手持|递出|抱着|接过", prefix):
        return "unresolved"
    suffix = line[value_offset + len(fact.value):]
    introducing = re.search(r"(?:一|两|三|[0-9]+)(?:名|位)[^。；!?]{0,80}$", prefix)
    if introducing and any(re.match(r"^(?:、[^，。:：]{0,40})?的?" + re.escape(target), suffix) for target in targets):
        return "explicit_subject"
    mentions = [(match.end(), len(name), name) for name in names | targets
                for match in re.finditer(re.escape(name), prefix)]
    if mentions and max(mentions)[2] in targets:
        return "explicit_subject"
    if resolve_pronouns and not mentions and re.match(r"^\s*(?:他|她|它)(?!们)", prefix):
        sentences = re.split(r"[。！？!?；;]", line[:value_offset])
        previous = sentences[-2].strip() if len(sentences) >= 2 else ""
        previous_cue = split_cue(previous)
        if previous_cue and unwrap(previous_cue[0]) in {"动作", "画面", "说明"}:
            previous = previous_cue[1].strip()
        mentioned = {name for name in names | targets if name in previous}
        if (len(mentioned) == 1 and mentioned <= targets
                and any(re.match(re.escape(target) + r"(?:护体法光|摔落|摔倒|倒地|倒下|倒飞|踉跄|受伤|中弹)", previous)
                        for target in targets)
                and not re.search(r"推|抱|扶|望|看|递|救|向|对", previous)
                and not re.search(r"[“”\"「」]|说道|说着|声称|转述", previous)):
            return "local_pronoun"
    sentence = re.split(r"[。！？!?；;]", suffix, maxsplit=1)[0]
    mentioned = {name for name in names | targets if name in sentence}
    if not mentions and mentioned and mentioned <= targets:
        return "explicit_subject"
    return "unresolved"
