"""Shared imports for split asset production services."""
# The split modules intentionally consume this shared dependency surface.
# ruff: noqa: F401

import hashlib
import json
from typing import Any

from sqlalchemy import exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import (
    Asset,
    AssetProductionReceipt,
    AssetUsage,
    AssetVersion,
    CanvasDocument,
    CanvasNode,
    Episode,
    Job,
    MediaFile,
    Project,
    ProjectAssetLink,
    Scene,
    SegmentScriptSnapshot,
    Shot,
    VideoSegment,
)
from app.schemas.production_contract import (
    AssetProductionOut,
    AssetProductionPatch,
    AssetProfile,
    ProductionSource,
)
from app.services import asset_service
from app.services.team_access import same_team
