"""Sound annotations do not hide genuine missing visual references."""

from types import SimpleNamespace

import pytest

from app.services.director_asset_review import append_asset_review, split_reference_names


@pytest.mark.parametrize("name,note,action,catalog,audio_only", [
    ("door sound", "SFX: door sound.", "Stand still.", [], True),
    ("guitar", "BGM: guitar.", "Stand still.", [], True),
    ("door sound", "", "Stand still.", [], False),
    ("door sound", "SFX: door sound.", "Stand still.", [{"asset_name": "door sound"}], False),
    ("door sound", "SFX: door sound.", "Stand still.", [{"asset_name": "sound", "aliases": ["door sound"]}], False),
])
def test_sound_evidence_preserves_visual_catalog(name, note, action, catalog, audio_only):
    shot = SimpleNamespace(shot_id=1, subject="", action=action, audio_note=note)
    pending, audio = split_reference_names([name, name], shot, catalog)
    assert pending == ([] if audio_only else [name])
    assert audio == ([name] if audio_only else [])
    assert shot.audio_note == note


def test_sound_does_not_remove_a_missing_prop():
    shot = SimpleNamespace(shot_id=1, subject="", action="Stand still.", audio_note="SFX: door sound.")
    source = {"shot_id": 71, "unresolved_names": ["door sound", "table", "table"]}
    proposal = {"continuity_report": {"status": "passed", "issues": []},
                "segments": [{"shot_ids": [71], "refs": {}}]}
    append_asset_review(proposal, [source], [shot], [], shot_id_map={1: 71})
    assert source["unresolved_names"] == ["table"]
    assert source["audio_reference_notes"] == ["door sound"]
    assert proposal["segments"][0]["refs"]["unmatched_assets"] == ["table"]
    assert proposal["continuity_report"]["status"] == "blocked"
