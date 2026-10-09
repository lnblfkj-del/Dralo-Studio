"""Narration remains exact speech; ambiguous or incomplete source stays rejected."""

import pytest

from app.core.errors import ValidationError
from app.services.director_response_assembly import protected_source_lines
from app.services.segment_script_semantics import build_structured_script, validate_structured_script


@pytest.mark.parametrize('text', ['旁白：他终于回家了。', '旁白: 钟声穿过空街。', '旁白：\n“他终于回家了。”'])
def test_explicit_narration_preserves_exact_source(text):
    snapshot = {'source_lines': [{'line': i, 'text': line} for i, line in enumerate(text.splitlines(), 1)]}
    protected = protected_source_lines(snapshot)
    assert list(protected) == [1]
    assert protected[1]['field'] == 'dialogue' and protected[1]['text'] == text
    shot = {'shot_id': 1, 'scene_id': 1, 'duration': 5, 'action': '林夏在雨夜等待。', 'dialogue': text}
    script = build_structured_script([1], {1: shot}, {1: {'scene_id': 1}})
    assert script['dialogue'][0]['speaker'] == '旁白'
    assert script['dialogue'][0]['source_text'] == text
    assert script['validation_issues'] == []
    checked = validate_structured_script(script, [1], {1: shot}, {1: {'scene_id': 1}})
    assert checked['dialogue'][0]['speaker'] == '旁白'
    assert checked['dialogue'][0]['text'] == script['dialogue'][0]['text']
    assert checked['validation_issues'] == []


@pytest.mark.parametrize('text', ['陌生人：我来了。', 'url：https://example.com', '旁白：',
                                '旁白：\n动作未明确为台词。', '旁白：“没有结束'])
def test_unknown_or_incomplete_source_is_not_silently_omitted(text):
    snapshot = {'source_lines': [{'line': i, 'text': line} for i, line in enumerate(text.splitlines(), 1)]}
    with pytest.raises(ValidationError):
        protected_source_lines(snapshot)
