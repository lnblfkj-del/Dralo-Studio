"""Project-local visual identity relationships, independent of story genre."""

# ruff: noqa: RUF001

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConsistencyProp(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_id: int = Field(gt=0)
    presence: Literal["fixed", "occurrence"] = "occurrence"
    source_scene_keys: list[str] = Field(default_factory=list, max_length=100)


class ConsistencyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=0)
    identity_asset_id: int | None = Field(default=None, gt=0)
    location_asset_id: int | None = Field(default=None, gt=0)
    kind: Literal["base", "state", "view"] = "base"
    props: list[ConsistencyProp] = Field(default_factory=list, max_length=50)
    design_description: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def coherent_relation(self):
        if (self.kind == "base") != (self.identity_asset_id is None):
            raise ValueError("基础资产不关联自身；状态和视角必须选择基础资产")
        if len({item.asset_id for item in self.props}) != len(self.props):
            raise ValueError("关联道具不能重复")
        return self


class ConsistencyReset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    asset_ids: list[Annotated[int, Field(gt=0)]] = Field(min_length=1, max_length=1500)
    confirmed: Literal[True]
