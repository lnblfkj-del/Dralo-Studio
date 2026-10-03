"""Standalone distribution: private cloud operations are not included."""

from fastapi import APIRouter


def register_edition_routes(api: APIRouter, *, execution_location: str) -> None:
    if execution_location != "local":
        raise RuntimeError("This standalone source distribution only supports local execution")
