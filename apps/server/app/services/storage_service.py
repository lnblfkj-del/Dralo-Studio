"""Host storage settings. Local files remain authoritative; cloud copies are private."""
import asyncio
import hashlib
import json
import os
import re
import shutil
from datetime import timedelta
from pathlib import Path
from uuid import uuid4
from urllib.parse import urlsplit

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings
from app.core.errors import ConflictError
from app.core.provider_crypto import encrypt_api_key, decrypt_api_key


class StorageUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    vendor: str = "aliyun"
    enabled: bool = False
    mirror_enabled: bool = False
    endpoint: str = ""
    region: str = ""
    bucket: str = ""
    prefix: str = "works"
    addressing_style: str = "virtual"
    allow_http: bool = False
    access_key: str = Field(default="", max_length=1024)
    secret_key: str = Field(default="", max_length=2048)
    clear_credentials: bool = False
    signed_url_seconds: int = Field(default=3600, ge=60, le=86400)
    local_path: str | None = Field(default=None, max_length=2048)


def config_path():
    return settings.storage_config_path


def read_config():
    if settings.runtime_execution_location == "cloud":
        raise ConflictError("云端不得读取安装级共享对象存储，请使用本人配置")
    path = config_path()
    if not path.exists():
        return {"revision": 0, "vendor": "aliyun", "enabled": False, "mirror_enabled": False,
                "endpoint": "", "region": "", "bucket": "", "prefix": "works", "addressing_style": "virtual",
                "allow_http": False, "signed_url_seconds": 3600}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ConflictError("存储配置无法读取，请恢复配置备份；不会回退到其他存储位置") from None


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep the temporary name short so atomic marker writes remain below the
    # common Win32 path limit even when the final content-addressed name is long.
    temporary = path.with_name(uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def public_config():
    value = read_config()
    result = {k: v for k, v in value.items() if k not in {"credentials", "previous_roots"}}
    result.update(local_path=str(settings.storage_path.resolve()), credentials_configured=bool(value.get("credentials")),
                  execution_location=settings.runtime_execution_location,
                  restart_required=bool(value.get("pending_local_path")),
                  previous_roots=value.get("previous_roots", []))
    status_path = config_path().with_name("storage-sync-status.json")
    try:
        result["sync"] = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    except (OSError, ValueError):
        result["sync"] = {}
    if settings.runtime_execution_location == "cloud":
        result["restart_required"] = False
        result.pop("pending_local_path", None)
    return result


def validate_local_target(value, source):
    target = Path(value).expanduser()
    if not target.is_absolute():
        raise ConflictError("存储位置必须为绝对路径或已挂载的 NAS 共享目录")
    target = target.resolve()
    source = source.resolve()
    if target == source:
        return target
    if target == Path(target.anchor) or target == Path.home().resolve() or target.is_relative_to(source) or source.is_relative_to(target):
        raise ConflictError("不能选择磁盘根目录、用户主目录或当前存储的父子目录")
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise ConflictError("请选择空目录，迁移不会覆盖已有文件")
    return target


def list_local_directories(value=""):
    """Read directories only, for authenticated loopback administration."""
    if settings.runtime_execution_location != "local":
        raise ConflictError("云端存储目录由运维管理")
    if len(value) > 2048:
        raise ConflictError("目录路径过长")
    if not value:
        roots = ([Path(f"{letter}:/") for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
                  if Path(f"{letter}:/").is_dir()] if os.name == "nt" else [Path("/")])
        return {"path": "", "parent": None, "directories": [str(root) for root in roots], "truncated": False}
    path = Path(value).expanduser()
    if not path.is_absolute() or value.startswith(("\\\\", "//")):
        raise ConflictError("请选择本机磁盘或已挂载的 NAS；UNC 路径请手动填写")
    try:
        path = path.resolve(strict=True)
        directories = []
        truncated = False
        with os.scandir(path) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    directories.append(str(path / entry.name))
                    if len(directories) > 500:
                        directories.pop()
                        truncated = True
                        break
        return {"path": str(path), "parent": str(path.parent) if path.parent != path else "",
                "directories": sorted(directories, key=str.casefold), "truncated": truncated}
    except (OSError, ValueError, RuntimeError):
        raise ConflictError("无法读取此目录，请检查目录是否存在以及本地服务的访问权限") from None


def create_local_directory(parent: str, name: str):
    """在本机已存在的目录下创建一个安全的单层子目录。"""
    if settings.runtime_execution_location != "local":
        raise ConflictError("云端存储目录由运维管理")
    if len(parent) > 2048 or len(name) > 120:
        raise ConflictError("目录名称或路径过长")
    if not name or name in {".", ".."} or Path(name).name != name:
        raise ConflictError("新文件夹名称只能是单层目录名")
    if any(ord(char) < 32 for char in name) or any(char in name for char in '<>:"/\\|?*'):
        raise ConflictError("新文件夹名称包含不支持的字符")
    base = Path(parent).expanduser()
    if not base.is_absolute() or parent.startswith(("\\\\", "//")):
        raise ConflictError("请选择本机磁盘或已挂载的 NAS；UNC 路径请手动填写")
    try:
        base = base.resolve(strict=True)
        if not base.is_dir():
            raise ConflictError("只能在已存在的文件夹内创建新文件夹")
        target = (base / name).resolve(strict=False)
        if not target.parent.is_relative_to(base):
            raise ConflictError("新文件夹路径不安全")
        target.mkdir()
        return str(target)
    except ConflictError:
        raise
    except FileExistsError:
        raise ConflictError("同名文件夹已存在，请换一个名称") from None
    except (OSError, ValueError, RuntimeError):
        raise ConflictError("新文件夹创建失败，请检查目录权限") from None


def build_object_config(update, old):
    value = update.model_dump(exclude={"access_key", "secret_key", "clear_credentials", "local_path"})
    if update.vendor not in {"aliyun", "tencent", "qiniu", "minio", "s3"}:
        raise ConflictError("不支持的存储协议")
    if update.addressing_style not in {"path", "virtual"}:
        raise ConflictError("S3 寻址方式无效")
    if update.vendor == "aliyun" and update.addressing_style != "virtual":
        raise ConflictError("阿里云 OSS 使用 virtual 寻址及原生 V4 签名")
    if not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", update.prefix):
        raise ConflictError("对象前缀只允许字母、数字、下划线、短横线及分隔斜杠")
    if update.endpoint:
        try:
            parsed = urlsplit(update.endpoint)
            if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError()
            _ = parsed.port
        except ValueError:
            raise ConflictError("Endpoint 必须为不带桶名、凭证或路径的 HTTP(S) 服务地址") from None
        if parsed.scheme == "http" and not update.allow_http:
            raise ConflictError("HTTP 仅用于可信局域网，请明确启用不加密连接；云服务应使用 HTTPS")
        if parsed.hostname in {"169.254.169.254", "metadata.google.internal"}:
            raise ConflictError("不能使用云元数据地址")
    creds = None if update.clear_credentials else old.get("credentials")
    if bool(update.access_key) != bool(update.secret_key):
        raise ConflictError("Access Key 和 Secret Key 必须同时填写，留空则保留已保存密钥")
    if update.access_key and update.secret_key:
        creds = encrypt_api_key(json.dumps([update.access_key, update.secret_key]))
    if update.enabled and (not update.endpoint or not update.bucket or not update.region or not creds):
        raise ConflictError("启用对象存储前请填写 Endpoint、区域、桶及双密钥")
    if update.bucket and not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", update.bucket):
        raise ConflictError("桶名格式无效，请填写已有桶；腾讯云桶名需包含 APPID 后缀")
    if update.mirror_enabled and not update.enabled:
        raise ConflictError("请先启用对象存储，再启用自动云端副本")
    value["credentials"] = creds
    return value


def save_config(update):
    if settings.runtime_execution_location == "cloud":
        raise ConflictError("云端不得修改安装级共享对象存储，请使用本人配置")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=10):
        old = read_config()
        if update.revision != old["revision"]:
            raise ConflictError("存储设置已被其他页面修改，请刷新后重试")
        value = build_object_config(update, old)
        identity = ("vendor", "endpoint", "bucket", "prefix", "region")
        value["profile_id"] = old.get("profile_id") if all(old.get(k) == value.get(k) for k in identity) else uuid4().hex
        value["profile_id"] = value["profile_id"] or uuid4().hex
        for key in ("active_local_path", "pending_local_path", "previous_roots"):
            if key in old:
                value[key] = old[key]
        if update.local_path is not None:
            if settings.runtime_execution_location != "local" and update.local_path != str(settings.storage_path.resolve()):
                raise ConflictError("云端共享存储目录由运维配置 STORAGE_PATH，不允许网页修改服务器磁盘位置")
            target = validate_local_target(update.local_path, settings.storage_path)
            value["pending_local_path"] = str(target) if target != settings.storage_path.resolve() else None
        value["revision"] += 1
        atomic_json(path, value)
    return public_config()


def apply_pending_local_path():
    """Supervisor only, before any API or worker process starts. Never delete source."""
    if settings.runtime_execution_location != "local":
        return
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(path) + ".lock", timeout=10):
        value = read_config()
        if not value.get("pending_local_path"):
            return
        source = settings.storage_path.resolve()
        target = Path(value["pending_local_path"]).resolve()
        if not source.is_dir():
            raise ConflictError("原存储目录不可访问，可能是 NAS 未挂载；不会切换到空目录")
        # A failed partial copy can resume; never overwrite a mismatching file.
        if target == source or target == Path(target.anchor) or target.is_relative_to(source) or source.is_relative_to(target):
            raise ConflictError("存储迁移目标不安全")
        target.mkdir(parents=True, exist_ok=True)
        for item in source.rglob("*"):
            if item.is_symlink() or (hasattr(item, "is_junction") and item.is_junction()):
                raise ConflictError("存储内存在链接目录，迁移已停止，请人工核对")
            if not item.is_file():
                continue
            destination = target / item.relative_to(source)
            if not destination.resolve().is_relative_to(target):
                raise ConflictError("迁移目标目录存在链接，已停止")
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.is_symlink() or not destination.resolve().is_relative_to(target):
                raise ConflictError("迁移目标存在链接，已停止")
            def digest(file):
                with file.open("rb") as stream:
                    return hashlib.file_digest(stream, "sha256").hexdigest()
            if not destination.exists():
                temporary = destination.with_name(destination.name + "." + uuid4().hex + ".storage-copy")
                shutil.copyfile(item, temporary)
                if digest(item) != digest(temporary):
                    raise ConflictError("存储迁移校验失败，原文件保留")
                os.replace(temporary, destination)
            if digest(item) != digest(destination):
                raise ConflictError("新目录文件冲突，停止迁移且保留原存储")
        value["active_local_path"] = str(target)
        value["pending_local_path"] = None
        value["previous_roots"] = list(dict.fromkeys([*value.get("previous_roots", []), str(source)]))
        value["revision"] += 1
        atomic_json(path, value)


class ObjectStore:
    def __init__(self, value):
        if not value.get("enabled") or not value.get("credentials"):
            raise ConflictError("对象存储未启用或未配置密钥")
        self.value = value
        cloud = settings.runtime_execution_location == "cloud"
        if cloud:
            from app.core.object_store_transport import require_https
            require_https(value["endpoint"])
        self.bucket = value["bucket"]
        ak, sk = json.loads(decrypt_api_key(value["credentials"]))
        self.oss = value["vendor"] == "aliyun"
        if self.oss:
            import alibabacloud_oss_v2 as oss
            cfg = oss.config.load_default()
            cfg.credentials_provider = oss.credentials.StaticCredentialsProvider(ak, sk)
            cfg.region, cfg.endpoint = value["region"], value["endpoint"]
            cfg.connect_timeout, cfg.readwrite_timeout = 10, 60
            cfg.enabled_redirect = False
            if cloud:
                from app.core.object_store_transport import oss_transport
                cfg.http_client = oss_transport()
            else:
                from alibabacloud_oss_v2.transport import RequestsHttpClient
                cfg.http_client = RequestsHttpClient(connect_timeout=10, readwrite_timeout=60, enabled_redirect=False)
            self.transport = cfg.http_client
            self.client = oss.Client(cfg)
        else:
            import boto3
            from botocore.config import Config
            self.client = boto3.client("s3", endpoint_url=value["endpoint"], region_name=value["region"],
                aws_access_key_id=ak, aws_secret_access_key=sk,
                config=Config(signature_version="s3v4", connect_timeout=10, read_timeout=60,
                    retries={"max_attempts":2}, request_checksum_calculation="when_required",
                    response_checksum_validation="when_required", s3={"addressing_style":value["addressing_style"]}))
            if cloud:
                from app.core.object_store_transport import protect_s3
                protect_s3(self.client)

    def close(self):
        if self.oss:
            self.transport.close()
        else:
            self.client.close()

    def check(self):
        if self.oss:
            import alibabacloud_oss_v2 as oss
            self.client.get_bucket_info(oss.GetBucketInfoRequest(bucket=self.bucket))
        else:
            self.client.head_bucket(Bucket=self.bucket)

    def upload(self, path, key):
        if self.oss:
            import alibabacloud_oss_v2 as oss
            self.client.put_object_from_file(oss.PutObjectRequest(bucket=self.bucket,key=key), str(path))
        else:
            from boto3.s3.transfer import TransferConfig
            self.client.upload_file(str(path), self.bucket, key, Config=TransferConfig(max_concurrency=2))

    def signed_url(self, key):
        seconds = self.value["signed_url_seconds"]
        if self.oss:
            import alibabacloud_oss_v2 as oss
            return self.client.presign(oss.GetObjectRequest(bucket=self.bucket,key=key),expires=timedelta(seconds=seconds)).url
        return self.client.generate_presigned_url("get_object",Params={"Bucket":self.bucket,"Key":key},ExpiresIn=seconds)


def check_connection(value=None):
    if value is None and settings.runtime_execution_location == "cloud":
        raise ConflictError("云端对象存储必须引用本人配置")
    store = ObjectStore(value if value is not None else read_config())
    try:
        store.check()
    finally:
        store.close()


def mirror_media(media, *, make_url=False, value=None):
    cloud = settings.runtime_execution_location == "cloud"
    if cloud and (value is None or value.get("owner_id") != media.owner_id or not value.get("version_id")):
        raise ConflictError("云端素材与对象存储账号或版本不匹配")
    value = value if value is not None else read_config()
    source = (settings.storage_path / media.file_path).resolve()
    if not source.is_relative_to(settings.storage_path.resolve()) or not source.is_file():
        raise ConflictError("本地素材不存在或路径不安全")
    # Content identity prevents accidentally treating changed files as an existing copy.
    with source.open("rb") as stream:
        digest = hashlib.file_digest(stream,"sha256").hexdigest()
    profile_id = value.get("profile_id", "disabled")
    marker = config_path().parent / "storage-replicas" / profile_id / f"{media.id}-{digest}.json"
    # Keep existing marker names on ordinary paths, but avoid Win32's common 260-char limit
    # when a deeply nested storage/config root is used. The marker itself retains the full hash.
    if len(str(marker) + ".lock") >= 240:
        compact = hashlib.sha256(f"{profile_id}:{media.id}:{digest}".encode()).hexdigest()[:32]
        marker = config_path().parent / "storage-replicas" / "compact" / f"{compact}.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    namespace = f'versions/{value["version_id"]}/' if cloud else ""
    key = f'{value["prefix"]}/{namespace}media/{media.owner_id}/{media.id}/{digest}{source.suffix.lower()}'
    with FileLock(str(marker)+".lock",timeout=120):
        store = ObjectStore(value)
        try:
            if not marker.exists():
                store.upload(source,key)
                atomic_json(marker,{"key":key,"media_id":media.id,"sha256":digest})
            return {"uploaded":True,"url":store.signed_url(key) if make_url else None,"expires_in":value["signed_url_seconds"]}
        except Exception as exc:
            raise ConflictError(object_error_message(exc, "对象存储上传或签名")) from None
        finally:
            store.close()


async def mirror_loop():
    from app.core.workspace_context import system_scope
    with system_scope():
        await _mirror_loop()


async def _mirror_loop():
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models import MediaFile
    cursor = 0
    while True:
        try:
            if settings.runtime_execution_location == "cloud":
                from app.services.private_storage_service import mirror_round
                await mirror_round()
                await asyncio.sleep(15)
                continue
            value = read_config()
            if value.get("enabled") and value.get("mirror_enabled"):
                async with SessionLocal() as session:
                    items = list((await session.scalars(select(MediaFile).where(MediaFile.id>cursor).order_by(MediaFile.id).limit(8))).all())
                    for item in items:
                        transfer = asyncio.create_task(asyncio.to_thread(mirror_media, item))
                        try:
                            await asyncio.shield(transfer)
                        except asyncio.CancelledError:
                            # Cancelling to_thread does not stop the upload. Drain
                            # it before releasing leadership on normal shutdown.
                            await asyncio.gather(transfer, return_exceptions=True)
                            raise
                        cursor = item.id
                    if not items:
                        cursor = 0
                atomic_json(config_path().with_name("storage-sync-status.json"), {"status":"ok","last_checked_media_id":cursor})
        except asyncio.CancelledError:
            raise
        except Exception:
            if settings.runtime_execution_location != "cloud":
                atomic_json(config_path().with_name("storage-sync-status.json"), {"status":"error","message":"云副本同步失败，本地文件保留，将自动重试"})
        await asyncio.sleep(15)


def object_error_message(exc, operation):
    """Classify SDK failures without returning their signed URLs or headers."""
    response = getattr(exc, "response", {})
    code = ((response.get("Error") or {}).get("Code", "") if isinstance(response, dict) else "") or getattr(exc, "code", "")
    if code in {"AccessDenied", "InvalidAccessKeyId", "SignatureDoesNotMatch", "403"}:
        reason = "密钥无效或桶权限不足，请检查凭据及读写权限"
    elif code in {"NoSuchBucket", "404"}:
        reason = "存储桶不存在或区域不匹配"
    elif code in {"ExpiredToken", "RequestExpired"}:
        reason = "凭据或签名已过期，请更新凭据并重新生成链接"
    elif "timeout" in type(exc).__name__.lower() or isinstance(exc, TimeoutError):
        reason = "连接超时，请检查 Endpoint、网络及桶区域"
    else:
        reason = "连接或操作失败，请检查 Endpoint、区域、桶名、鉴权及权限"
    return f"{operation}失败：{reason}；本地原件保留"
