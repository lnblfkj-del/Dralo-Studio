"""Read-only existing-model sources and explicitly saved workspace releases."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.models import Provider, ProviderModel, VideoConfigurationPreset
from app.services.provider_service import get_provider
from app.services.video_tools_service import digest, export_package, validate_package
from app.services.video_builtin_presets import packages


def entry(key, package, *, name, version, source, model_ids, provider_id=None):
    return {"key": key, "name": name, "version": version, "source": source,
            "model_ids": model_ids, "protocol": package["protocol"], "package": package,
            "origin_provider_id": provider_id, "read_only": True,
            "fingerprint": digest([package, model_ids, version, provider_id]), "verification": "declared"}


async def model_entry(session, model):
    provider = await session.get(Provider, model.provider_id)
    if provider is None or model.model_type != "video":
        raise NotFoundError("视频配置不存在")
    package = export_package(provider, model)
    return entry(f"model:{model.id}", package, name=model.name, version=digest(package)[:12],
                 source=f"已保存模型 · {provider.name}", model_ids=[model.model_id], provider_id=provider.id)


def saved_entry(row):
    return entry(f"saved:{row.id}", row.package, name=row.name, version=row.version,
                 source=row.source, model_ids=row.model_ids)


def builtin_entries():
    result = {}
    for key, value in packages().items():
        package = validate_package(value["package"])
        result[key] = entry(f"builtin:{key}", package, name=package["name"], version=digest(package)[:12],
                            source="内置协议登记（未实测）", model_ids=value["model_ids"])
    return result


async def catalog(session):
    items = []
    for model in await session.scalars(select(ProviderModel).where(ProviderModel.model_type == "video").order_by(ProviderModel.id.desc())):
        try:
            items.append(await model_entry(session, model))
        except (ConflictError, ValidationError, ValueError):
            # Incomplete/unsafe packages stay editable on their original model.
            continue
    items += [saved_entry(row) for row in await session.scalars(select(VideoConfigurationPreset).order_by(VideoConfigurationPreset.id.desc()))]
    return {"items": items + list(builtin_entries().values())}


async def preview(session, payload):
    await get_provider(session, payload.provider_id)
    kind, identifier = payload.key.split(":")
    if kind == "builtin":
        item = builtin_entries().get(identifier)
        if item is None:
            raise NotFoundError("视频配置不存在")
    else:
        row = await session.get(ProviderModel if kind == "model" else VideoConfigurationPreset, int(identifier))
        if row is None:
            raise NotFoundError("视频配置不存在")
        item = await model_entry(session, row) if kind == "model" else saved_entry(row)
    if item["fingerprint"] != payload.fingerprint:
        raise ConflictError("来源配置已更新，请刷新目录后重新选择")
    if payload.model_identifier not in item["model_ids"]:
        raise ConflictError("此配置不适用于当前模型，请选择匹配配置，或手动维护适用模型后另存副本")
    return {**item, "confirm_interface": item["origin_provider_id"] != payload.provider_id}


async def save(session, payload):
    package = validate_package(payload.package.model_dump(mode="json"))
    values = {"name": payload.name.strip(), "version": payload.version, "source": payload.source.strip(),
              "model_ids": payload.model_ids, "package": package}
    release_key = digest([values["name"], values["version"]])
    fingerprint = digest(values)
    existing = await session.scalar(select(VideoConfigurationPreset).where(VideoConfigurationPreset.release_key == release_key))
    if existing is not None:
        if existing.fingerprint != fingerprint:
            raise ConflictError("此名称和版本已存在不同内容，请填写新版本号")
        return saved_entry(existing)
    try:
        async with session.begin_nested():
            row = VideoConfigurationPreset(**values, release_key=release_key, fingerprint=fingerprint)
            session.add(row)
            await session.flush()
    except IntegrityError as exc:
        raise ConflictError("该配置版本刚被保存，请刷新目录后检查") from exc
    return saved_entry(row)
