"""Install data-only manifests for separately deployed HTTP adapters, never executable code."""

from sqlalchemy import select

from app.core.errors import ConflictError
from app.models import VideoAdapterRelease, Provider
from app.services.video_version_service import digest


def identity(package):
    return (package.get("video_config", {}).get("integration") or {}).get("service_adapter")


async def install(session, package, provider):
    from app.services.video_tools_service import validate_package
    package = validate_package(package)
    adapter = identity(package)
    if not adapter:
        raise ConflictError("这是一份普通接法配置，不需要安装适配服务")
    # Serialize installs in the local NULL-workspace catalog as well as PostgreSQL.
    from sqlalchemy import update
    await session.execute(update(Provider).where(Provider.id == provider.id).values(
        name=Provider.name, updated_at=Provider.updated_at))
    row = await session.scalar(select(VideoAdapterRelease).where(
        VideoAdapterRelease.adapter_key == adapter["id"], VideoAdapterRelease.version == adapter["version"]))
    fingerprint = digest(package["video_config"]["integration"])
    if row and row.fingerprint != fingerprint:
        raise ConflictError("同一适配器版本不能覆盖，请修改版本号后重新安装")
    if row is None:
        row = VideoAdapterRelease(adapter_key=adapter["id"], version=adapter["version"],
            package=package, fingerprint=fingerprint, enabled=True, workspace_id=provider.workspace_id)
        session.add(row)
        await session.flush()
    return {"id": row.id, "adapter": adapter, "fingerprint": row.fingerprint, "enabled": row.enabled}


async def require_installed(session, model, *, submitting=False):
    connection = (model.video_config or {}).get("integration") or {}
    adapter = connection.get("service_adapter")
    if adapter is None:
        return
    row = await session.scalar(select(VideoAdapterRelease).where(
        VideoAdapterRelease.adapter_key == adapter["id"], VideoAdapterRelease.version == adapter["version"]))
    if row is None or row.fingerprint != digest(connection):
        raise ConflictError("此适配服务版本尚未安装，或接口映射与安装包不一致")
    if submitting and not row.enabled:
        raise ConflictError("此适配服务已停止接收新任务；已提交的任务仍可查询和下载")
