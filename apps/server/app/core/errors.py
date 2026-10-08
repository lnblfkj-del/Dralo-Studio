"""统一异常与错误码。

规范要求前端不得直接显示 `AxiosError: Request failed...`，
因此所有对外错误都必须携带稳定的 code 与中文可读 message。
"""

from typing import Any


class AppError(Exception):
    """应用异常基类。

    code:        稳定的机器可读标识，前端据此做分支处理
    message:     中文可读提示，可直接展示给用户
    status_code: HTTP 状态码
    details:     补充信息，不得包含 API Key 等敏感内容
    """

    code: str = "INTERNAL_ERROR"
    message: str = "服务内部错误，请稍后重试"
    status_code: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


# ---------- 通用 ----------


class ValidationError(AppError):
    code = "VALIDATION_ERROR"
    message = "请求参数不合法"
    status_code = 422


class NotFoundError(AppError):
    code = "NOT_FOUND"
    message = "请求的资源不存在"
    status_code = 404


class ConflictError(AppError):
    code = "CONFLICT"
    message = "资源状态冲突，请刷新后重试"
    status_code = 409


class ProjectDeletionConflictError(ConflictError):
    """项目删除仍存在未清理的数据库引用。"""

    code = "PROJECT_DELETE_CONFLICT"
    message = "项目仍有未清理的关联数据，删除未完成；请刷新后重试"


# ---------- 鉴权 ----------


class AuthError(AppError):
    code = "AUTH_ERROR"
    message = "登录状态无效或已过期，请重新登录"
    status_code = 401


class MissingTokenError(AuthError):
    code = "AUTH_TOKEN_MISSING"
    message = "缺少登录凭证，请先登录"


class MalformedTokenError(AuthError):
    code = "AUTH_TOKEN_MALFORMED"
    message = "登录凭证格式不正确"


class InvalidTokenError(AuthError):
    code = "AUTH_TOKEN_INVALID"
    message = "登录凭证无效或已过期，请重新登录"


class ResourceTokenRejectedError(AuthError):
    code = "AUTH_RESOURCE_TOKEN_REJECTED"
    message = "此凭证仅用于指定资源，不能作为登录凭证"


class UserUnavailableError(AuthError):
    code = "AUTH_USER_UNAVAILABLE"
    message = "账号不存在或已被停用，请重新登录"


class SessionRevokedError(AuthError):
    code = "AUTH_SESSION_REVOKED"
    message = "登录会话已撤销，请重新登录"


class InvalidCredentialsError(AuthError):
    code = "INVALID_CREDENTIALS"
    message = "账号或密码错误"


class PermissionDeniedError(AppError):
    code = "PERMISSION_DENIED"
    message = "没有执行该操作的权限"
    status_code = 403


# ---------- Provider（M2 使用，此处先定义完整族） ----------


class ProviderError(AppError):
    code = "PROVIDER_ERROR"
    message = "模型服务暂时不可用，请稍后重试"
    status_code = 502


class ProviderConfigurationError(AppError):
    code = "PROVIDER_CONFIGURATION_ERROR"
    message = "模型渠道加密配置不可用，请联系管理员"
    status_code = 503


class ProviderAuthError(ProviderError):
    code = "PROVIDER_AUTH_ERROR"
    message = "模型渠道认证失败，请检查 API Key"


class QuotaError(ProviderError):
    code = "QUOTA_ERROR"
    message = "模型渠道额度不足"


class ProviderCapacityError(ProviderError):
    code = "PROVIDER_CAPACITY_UNAVAILABLE"
    message = "渠道暂无可用图片资源或额度，请核对渠道状态后继续"


class RateLimitError(ProviderError):
    code = "RATE_LIMIT_ERROR"
    message = "请求过于频繁，请稍后重试"
    status_code = 429

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.retry_after_seconds = retry_after_seconds
        merged_details = dict(details or {})
        if retry_after_seconds is not None:
            merged_details["retry_after_seconds"] = retry_after_seconds
        super().__init__(
            message,
            details=merged_details,
        )


class TimeoutError_(ProviderError):
    code = "TIMEOUT_ERROR"
    message = "模型服务响应超时"
    status_code = 504


class GenerationFailedError(ProviderError):
    code = "GENERATION_FAILED"
    message = "生成失败，请调整参数后重试"


class ModelOutputTruncatedError(ProviderError):
    code = "MODEL_OUTPUT_TRUNCATED"
    message = "模型返回的结构化结果不完整，响应可能被截断"


class ModelOutputPollutedError(ProviderError):
    code = "MODEL_OUTPUT_POLLUTED"
    message = "模型返回的结构化结果包含 JSON 之外的内容"


class ModelOutputSchemaError(ProviderError):
    code = "MODEL_OUTPUT_SCHEMA_INVALID"
    message = "模型返回的结构化结果不符合字段要求"


class ModelOutputBusinessValidationError(ProviderError):
    code = "MODEL_OUTPUT_BUSINESS_VALIDATION_FAILED"
    message = "模型返回的结果未通过本地业务校验"


class ProviderParameterError(ProviderError):
    code = "PROVIDER_PARAMETER_ERROR"
    message = "渠道拒绝请求参数，请核对所选模型的协议、参数及参考素材限制"
    status_code = 422


class ProviderEndpointError(ProviderError):
    code = "PROVIDER_ENDPOINT_ERROR"
    message = "模型渠道端点不存在或协议路径不匹配，请检查 Base URL 和接口协议"


class ProviderModelNotFoundError(ProviderError):
    code = "PROVIDER_MODEL_NOT_FOUND"
    message = "模型渠道未找到所选模型，请检查模型名称和渠道可用范围"


class ProviderMethodError(ProviderError):
    code = "PROVIDER_METHOD_NOT_ALLOWED"
    message = "模型渠道不支持当前请求方法，请检查接口协议和端点配置"


class ProviderRoutingError(ProviderError):
    code = "MODEL_ROUTE_CHANGED"
    message = "原任务的模型、协议或地址已改变，请恢复原配置后查询；未重新提交"
    status_code = 409


class DownloadFailedError(ProviderError):
    code = "DOWNLOAD_FAILED"
    message = "生成结果下载失败"


class RemoteVideoTimeoutError(ProviderError):
    code = "VIDEO_REMOTE_TIMEOUT"
    message = "远端视频任务超过最长追踪时间，已停止自动轮询"


class RemoteJobDeferredError(Exception):
    """Internal control flow: remote work continues without a local lease."""


# ---------- 存储 ----------


class StorageError(AppError):
    code = "STORAGE_ERROR"
    message = "文件读写失败"


class StorageQuotaError(StorageError):
    code = "STORAGE_QUOTA_EXCEEDED"
    message = "存储空间不足，请清理后重试"
    status_code = 507
