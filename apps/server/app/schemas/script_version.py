from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.common import ORMModel


class ScriptVersionSummary(ORMModel):
    id: int
    episode_id: int
    revision: int
    character_count: int
    source: str
    note: str | None = None
    restored_from: int | None = None
    created_by: int | None = None
    created_at: datetime


class ScriptVersionOut(ScriptVersionSummary):
    script: str


class RestoreScript(BaseModel):
    expected_revision: int = Field(ge=0)
