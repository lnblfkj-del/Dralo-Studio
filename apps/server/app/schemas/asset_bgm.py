from pydantic import BaseModel, ConfigDict, Field


class ApplyAssetBgm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    episode_id: int = Field(gt=0)
    version_id: int = Field(gt=0)
    expected_asset_revision: int = Field(ge=0)
    expected_episode_revision: int = Field(ge=0)
    volume: float = Field(default=0.3, ge=0, le=1, allow_inf_nan=False)
