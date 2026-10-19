"""Admitted character relations must survive production detail reads and edits."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models import ProjectAssetLink
from app.schemas.production_contract import AssetProfile
from app.services.asset_admission_identity import bind_admitted_identities



@pytest.fixture
async def auth_client():
    """Use only the temporary database configured by public tests/conftest.py."""
    from httpx import ASGITransport, AsyncClient

    from app.core.database import Base, engine
    from app.main import create_app
    from app.services import user_service

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    try:
        async with SessionLocal() as db:
            await user_service.create_user(db, username="relation-admin",
                                          password="relation-test-only-password", role="admin")
            await db.commit()
        async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://testserver") as client:
            response = await client.post("/api/auth/login", json={
                "username": "relation-admin", "password": "relation-test-only-password",
            })
            assert response.status_code == 200, response.text
            client.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
            yield client
    finally:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await engine.dispose()

@pytest.mark.parametrize("kind", ["voice", "costume"])
@pytest.mark.parametrize("names", [["Alpha"], ["Alpha", "Beta"], ["Unknown"]])
async def test_admission_detail_and_edit_preserve_relations(auth_client, kind, names):
    project = (await auth_client.post("/api/projects", json={"name": "Relations"})).json()
    base = f"/api/projects/{project['id']}/assets"
    roles = []
    for name in ("Alpha", "Beta"):
        response = await auth_client.post(base, json={
            "asset_type": "character", "name": name, "slug": name.lower(),
        })
        assert response.status_code == 201, response.text
        roles.append(response.json()["id"])
    response = await auth_client.post(base, json={
        "asset_type": kind, "name": "Reference", "slug": "reference",
        "attributes": {"character_name": ",".join(names)},
    })
    assert response.status_code == 201, response.text
    asset_id = response.json()["id"]
    async with SessionLocal() as db:
        await bind_admitted_identities(db, project["id"], [{"candidate_id": 1, "formal_asset_id": asset_id}])
        await db.commit()
    url = f"{base}/{asset_id}/production"
    response = await auth_client.get(url)
    assert response.status_code == 200, response.text
    detail = response.json()
    expected = roles[:len(names)] if names != ["Unknown"] else []
    relation = {
        "linked_character_asset_ids": expected,
        "linked_character_asset_id": expected[0] if len(expected) == 1 else None,
        "character_relation_status": "linked" if expected else "needs_review",
    }
    for field, value in relation.items():
        assert detail["profile"][field] == value
    patch = {"expected_revision": detail["revision"], "request_id": "edit-related-profile",
             "profile": {**detail["profile"], "description": "Updated"}}
    saved = await auth_client.patch(url, json=patch)
    assert saved.status_code == 200, saved.text
    assert (await auth_client.get(url)).json() == saved.json()
    async with SessionLocal() as db:
        link = await db.scalar(select(ProjectAssetLink).where(
            ProjectAssetLink.project_id == project["id"], ProjectAssetLink.asset_id == asset_id))
        for field, value in relation.items():
            assert link.production_data["profile"][field] == value


def test_relation_profile_is_typed_and_still_rejects_unknown_fields():
    assert AssetProfile(character_relation_status="not_applicable").character_relation_status == "not_applicable"
    for invalid in [{"linked_character_asset_ids": [-1]}, {"linked_character_asset_id": 0},
                    {"character_relation_status": "guessed"}, {"unknown_relation": True}]:
        with pytest.raises(ValidationError):
            AssetProfile.model_validate(invalid)


@pytest.mark.parametrize("field", ["linked_character_asset_ids", "linked_character_asset_id"])
async def test_relation_patch_cannot_reference_another_project(auth_client, field):
    projects = [(await auth_client.post("/api/projects", json={"name": name})).json()
                for name in ("Local", "Other")]
    assets = []
    for project, kind in zip(projects, ("voice", "character"), strict=True):
        response = await auth_client.post(f"/api/projects/{project['id']}/assets", json={
            "asset_type": kind, "name": kind, "slug": kind,
        })
        assert response.status_code == 201, response.text
        assets.append(response.json()["id"])
    url = f"/api/projects/{projects[0]['id']}/assets/{assets[0]}/production"
    detail = (await auth_client.get(url)).json()
    response = await auth_client.patch(url, json={
        "expected_revision": detail["revision"], "request_id": "reject-cross-project-relation",
        "profile": {field: [assets[1]] if field.endswith("ids") else assets[1]},
    })
    assert response.status_code == 404, response.text
    assert (await auth_client.get(url)).json() == detail
