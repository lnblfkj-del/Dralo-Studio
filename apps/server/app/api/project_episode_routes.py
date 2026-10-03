"""Aggregate project episode route groups under the stable router import."""

from fastapi import APIRouter

from app.api import (
    project_episode_basic_routes,
    project_episode_production_routes,
    project_episode_script_routes,
    project_episode_segment_routes,
)

router = APIRouter(tags=["projects"])
router.include_router(project_episode_basic_routes.router)
router.include_router(project_episode_segment_routes.router)
router.include_router(project_episode_production_routes.router)
router.include_router(project_episode_script_routes.router)
