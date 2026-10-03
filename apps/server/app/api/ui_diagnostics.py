"""Bounded, authenticated UI timing reports; never accept project contents."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import CurrentUser
from app.core.logging import get_logger

router = APIRouter(prefix="/ui-diagnostics", tags=["diagnostics"])
logger = get_logger(__name__)


class DialogTiming(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal["native-close-v2"]
    entry: Literal["sidebar", "home", "history", "unknown"]
    browser: str = Field(max_length=256)
    event_delay_ms: float = Field(ge=0, le=3_600_000)
    native_close_ms: float = Field(ge=0, le=3_600_000)
    state_update_ms: float = Field(ge=0, le=3_600_000)
    next_frame_ms: float = Field(ge=0, le=3_600_000)
    unmount_ms: float | None = Field(default=None, ge=0, le=3_600_000)


@router.post("/dialog-close", status_code=204)
async def dialog_close(payload: DialogTiming, user: CurrentUser) -> None:
    logger.info("UI_DIALOG_CLOSE user=%s %s", user.id, payload.model_dump_json())
