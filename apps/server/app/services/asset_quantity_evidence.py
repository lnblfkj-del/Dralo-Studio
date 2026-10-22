"""Literal quantity evidence for human, animal and mechanical performers.

Counts belong to a local noun phrase, never to an arbitrary name later in a quote.
Approximate, ranged, negated and container counts are not exact member counts.
"""

# ruff: noqa: RUF001

import re
import unicodedata

QUANTITY_EVIDENCE_VERSION = "asset-quantity-evidence.v1"

NUMERALS = "零〇一二两兩三四五六七八九十百千万萬壹贰貳叁參肆伍陆陸柒捌玖拾佰仟"
CLASSIFIERS = "名|位|个|個|人|头|頭|只|隻|匹|条|條|尾|羽|峰|台|臺|具|尊|架|艘|辆|輛|株|棵|朵|枚|颗|顆|件|对|對"
QUANTITY = re.compile(
    rf"(?<![\d{NUMERALS}数數几幾余餘多第每约約俩仨])"
    rf"(?:(?P<number>[\d{NUMERALS}]+)\s*(?P<unit>{CLASSIFIERS})|(?P<short>俩|仨))"
)
_DIGITS = dict(zip("零一二三四五六七八九", range(10), strict=True))
_TRANSLATION = str.maketrans(dict(zip("〇两兩萬壹贰貳叁參肆伍陆陸柒捌玖拾佰仟", "零二二万一二二三三四五六六七八九十百千", strict=True)))
_UNCERTAIN_PREFIX = re.compile(
    r"(?:大约|大約|约|約|约有|約有|近|将近|將近|大概|大概有|至少|至多|最多|最少|"
    r"不超过|不超過|不满|不滿|不足|超过|超過|多于|少于|不止|不是|并非|並非|并无|沒有|没有|"
    r"仅有不到|不到|[-—–~～])\s*$"
)
_RANGE_PREFIX = re.compile(rf"[\d{NUMERALS}](?:\s*(?:{CLASSIFIERS}))?\s*(?:至|到|或)\s*$")
_UNCERTAIN_SUFFIX = re.compile(
    rf"\s*(?:左右|上下|以上|以下|以内|以內|以外|之内|之內|有余|有餘|多|余|餘|"
    rf"(?:至|到|或|[-—–~～])\s*[\d{NUMERALS}])"
)
_BOUNDARY = re.compile(
    rf"[，,。！？!?；;：:\n、]|[与與和及对對]|(?:[{NUMERALS}\d数數几幾]+)\s*(?:{CLASSIFIERS})|"
    r"围住|圍住|围攻|攻击|攻擊|护送|護送|抓住|走向|面对|面對|看向|袭击|襲擊|迎接|"
    r"拉着|拉著|牵着|牽著|驮着|馱著|拖着|拖著|跟随|跟隨|带着|帶著|追赶|追趕|"
    r"身旁|旁边|旁邊|身后|身後|前方|后方|後方|各|每"
)
_BODY_PART = re.compile(r"^(?:眼|耳|翅|翼|爪|腿|蹄|角|尾巴|触手|觸手|手臂|胳膊|脚|腳)")


def literal_number(value):
    """Parse explicit integers; '两三' and abbreviated '一百二' stay unknown."""
    text = unicodedata.normalize("NFKC", value).translate(_TRANSLATION)
    if text.isdecimal():
        return int(text)
    if text in _DIGITS:
        return _DIGITS[text]
    if "万" in text:
        if text.count("万") != 1:
            return None
        left, right = text.split("万")
        high = literal_number(left)
        low = literal_number(right.removeprefix("零")) if right else 0
        if high is None or not 0 < high < 10000 or low is None or low >= 10000:
            return None
        # '一万二' is ambiguous, unlike '一万零二' or '一万二千'.
        if right and not right.startswith("零") and not any(unit in right for unit in "十百千"):
            return None
        return high * 10000 + low
    total, digit, last_unit, zero = 0, None, 10000, False
    for char in text:
        if char in _DIGITS:
            if char == "零":
                if digit is not None or total == 0 or zero:
                    return None
                zero = True
            elif digit is not None:
                return None
            else:
                digit = _DIGITS[char]
        elif char in "十百千":
            unit = {"十": 10, "百": 100, "千": 1000}[char]
            if unit >= last_unit or (digit is None and not (total == 0 and unit == 10)):
                return None
            total += (digit if digit is not None else 1) * unit
            digit, last_unit, zero = None, unit, False
        else:
            return None
    if digit is not None and last_unit > 10 and total and not zero:
        return None
    if zero and digit is None:
        return None
    return total + (digit or 0) if total else None


def exact_quantities(text):
    for match in QUANTITY.finditer(text):
        count = {"俩": 2, "仨": 3}.get(match["short"]) if match["short"] else literal_number(match["number"])
        if count is None or count <= 0:
            continue
        if match["unit"] in {"对", "對"}:
            count *= 2
        if (_UNCERTAIN_PREFIX.search(text[:match.start()]) or _RANGE_PREFIX.search(text[:match.start()])
                or _UNCERTAIN_SUFFIX.match(text[match.end():])):
            continue
        if re.search(r"\d[.,，．/:：]$", text[:match.start()]):
            continue
        yield match, count


def label_count_matches(quote, label, count):
    if not label or count is None:
        return False
    for match, actual in exact_quantities(quote):
        if actual != count:
            continue
        if _BODY_PART.match(quote[match.end():]) and not quote[match.end():].startswith(label):
            continue
        if (quote.startswith(label, match.start()) and len(label) > match.end() - match.start()
                and not _UNCERTAIN_SUFFIX.match(quote[match.start() + len(label):])):
            return True
        tail = quote[match.end():]
        position = tail.find(label)
        if (0 <= position <= 100 and not _BOUNDARY.search(tail[:position])
                and not _BODY_PART.match(tail[:position])
                and not _UNCERTAIN_SUFFIX.match(tail[position + len(label):])):
            return True
        # Explicit postposed counts: '飞豹共两头', not '飞豹追赶两头牛'.
        if re.search(re.escape(label) + r"\s*(?:[：:]\s*)?(?:共|合计|合計|总计|總計)?\s*$", quote[:match.start()]):
            return True
    return False


def counted_introduction(prefix):
    return any(len(prefix) - match.end() <= 80 and not _BOUNDARY.search(prefix[match.end():])
               for match, _ in exact_quantities(prefix))


def vague_group_matches(quote, label):
    """Recognize an explicitly unnamed population without inventing its size."""
    return bool(label and re.search(
        rf"(?:数十|數十|数百|數百|数千|數千|数万|數萬|若干|几|幾|许多|許多|众多|眾多|多)"
        rf"(?:{CLASSIFIERS})?\s*" + re.escape(label), quote))
