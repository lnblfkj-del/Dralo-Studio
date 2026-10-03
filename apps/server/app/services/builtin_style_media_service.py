"""Read-only product images, shared physically but scoped as normal media references."""

import json
from functools import lru_cache
from pathlib import Path
import shutil

from sqlalchemy import select, inspect
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.workspace_context import current_workspace

PREFIX = "_builtin/styles/"
PURPOSE = "builtin_style"


@lru_cache
def manifest():
    return json.loads(Path(__file__).with_name("builtin_style_media.json").read_text(encoding="utf-8"))["images"]


@lru_cache
def files():
    return {PREFIX + item["file"]: item for item in manifest()}


def protected_path(path):
    value = str(path).replace("\\", "/")
    return value == "_builtin" or value.startswith("_builtin/")


def validate(media):
    entry = files().get(media.file_path)
    if (entry is None or media.purpose != PURPOSE or media.hash != entry["sha256"]
            or media.size != entry["size"] or media.kind != "image"
            or media.mime_type != entry["mime_type"] or media.width != entry["width"]
            or media.height != entry["height"]
            or media.project_id is not None or media.source != "builtin"):
        raise ConflictError("内置风格资源不可修改或伪造")


@event.listens_for(Session, "before_flush")
def protect_records(session, flush_context, instances):
    from app.models import MediaFile

    for row in session.new | session.dirty | session.deleted:
        if not isinstance(row, MediaFile):
            continue
        state = inspect(row)
        was_builtin = PURPOSE in state.attrs.purpose.history.deleted or any(
            protected_path(value) for value in state.attrs.file_path.history.deleted)
        if was_builtin and session.is_modified(row):
            raise ConflictError("不能将内置风格资源修改为用户素材")
        if not (row.purpose == PURPOSE or protected_path(row.file_path)):
            continue
        validate(row)
        if row in session.deleted or (row in session.dirty and session.is_modified(row)):
            raise ConflictError("内置风格资源为只读，不能修改或删除")


async def references(session):
    from app.models import MediaFile, User

    root = settings.storage_path / "_builtin/styles"
    if not root.is_dir():
        if settings.is_production:
            raise RuntimeError("Builtin style media must be installed before starting the application")
        return {}
    context = current_workspace.get()
    owner = context.actor_id if context else await session.scalar(select(User.id).order_by(User.id).limit(1))
    if owner is None:
        return {}
    result = {}
    for entry in manifest():
        path = PREFIX + entry["file"]
        if not (settings.storage_path / path).is_file():
            raise RuntimeError("Builtin style media installation is incomplete")
        row = await session.scalar(select(MediaFile).where(MediaFile.file_path == path,
                                  MediaFile.workspace_id == (context.workspace_id if context else None)))
        if row is None:
            row = MediaFile(owner_id=owner, kind="image", source="builtin", purpose=PURPOSE,
                            file_path=path, original_name=entry["name"] + Path(entry["file"]).suffix,
                            mime_type=entry["mime_type"], size=entry["size"], hash=entry["sha256"],
                            width=entry["width"], height=entry["height"])
            session.add(row)
            await session.flush()
        validate(row)
        result[entry["name"]] = row.id
    return result


def install_files(storage_root):
    """Explicit deployment operation, not a user upload or per-account copy."""
    import hashlib
    source = Path(__file__).parents[1] / "resources/styles"
    target = storage_root / "_builtin/styles"
    if target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction()):
        raise RuntimeError("Builtin resource directory cannot be a link")
    if not target.resolve().is_relative_to(storage_root.resolve()):
        raise RuntimeError("Builtin resource directory escapes storage")
    target.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o755)
    target.chmod(0o755)
    for entry in manifest():
        src = source / entry["file"]
        if hashlib.sha256(src.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("Builtin image checksum mismatch")
        dst = target / entry["file"]
        if dst.is_symlink():
            raise RuntimeError("Builtin image cannot be a link")
        if not dst.exists():
            shutil.copyfile(src, dst)
        if dst.stat().st_size != entry["size"] or hashlib.sha256(dst.read_bytes()).hexdigest() != entry["sha256"]:
            raise RuntimeError("Installed builtin image checksum mismatch")
        dst.chmod(0o644)
