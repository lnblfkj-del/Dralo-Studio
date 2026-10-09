"""Safe failure explanations shared by job serialization and manual recovery."""

from datetime import UTC, datetime
from typing import Any

OUTPUT_ERRORS = {
    "MODEL_OUTPUT_SCHEMA_INVALID", "MODEL_OUTPUT_TRUNCATED",
    "MODEL_OUTPUT_POLLUTED", "MODEL_OUTPUT_BUSINESS_VALIDATION_FAILED",
}


def failure_detail(job: Any) -> dict[str, Any] | None:
    if job.status not in {"failed", "cancelled"}:
        return None
    payload = job.payload or {}
    history = payload.get("attempt_history") or []
    last = history[-1] if history else {}
    recovery = payload.get("response_recovery") or {}
    submission = payload.get("text_submission") or {}
    code = recovery.get("last_reprocess_error_code") or last.get("error_code") or job.error_code
    reason = recovery.get("last_reprocess_error_message") or last.get("error_message") or job.error_message or "任务未完成，尚未记录具体原因。"
    diagnostic = last.get("provider_diagnostic") or {}
    output = recovery.get("output_diagnostic") or last.get("output_diagnostic") or {}
    saved = bool(submission.get("response_received") or recovery.get("response_id"))
    expired = recovery.get("status") == "expired"
    if recovery.get("expires_at"):
        try:
            expires = datetime.fromisoformat(recovery["expires_at"]).replace(tzinfo=UTC)
            expired = expired or expires <= datetime.now(UTC)
        except (ValueError, TypeError):
            pass
    category, action = "unknown", "retry"
    title, hint = "生成失败", "调整要求已保留，可以重新尝试。"
    if saved:
        action = "reprocess"
        category, title = "local_processing", "结果已保存，处理失败"
        hint = "可使用已保存响应重新处理，不调用模型；若仍失败，需要修复对应处理逻辑。"
        if code in OUTPUT_ERRORS:
            category, title, action = "model_output", "模型返回内容未通过校验", "retry"
            hint = "原始响应已保存。相同内容重复解析不能补全缺失信息，可以重新生成。"
            if code == "MODEL_OUTPUT_TRUNCATED":
                title = "模型返回内容不完整"
                hint = "已保存收到的内容，但缺失部分无法通过本地处理补全。"
                if job.job_type == "text":
                    finish_reason = str(submission.get("finish_reason") or "").lower()
                    if finish_reason in {"length", "max_tokens", "max_output_tokens", "max_output", "max_completion_tokens"}:
                        title = "模型输出预算不足"
                        hint = "渠道明确报告达到输出上限。请在模型管理中调整该模型的默认输出预算、上限或思考等级后重新生成；已完成的内容保留。"
                        budget = ((getattr(job, "execution_policy_snapshot", None) or {}).get("text_model") or {}).get("effective_output_tokens")
                        if budget is None:
                            hint = "该次请求没有配置明确输出预算，使用的是渠道默认限制，并不代表无限输出。请先在模型管理中配置，再重新生成失败分集；已完成正文保留。"
                        else:
                            hint = f"该次请求预算为 {budget} tokens。渠道报告输出达到上限；需核对思考消耗、渠道支持上限及参数是否生效。已完成正文保留。"
                    elif submission.get("stream_terminal_seen") is False:
                        title = "模型流式响应中断"
                        hint = "未收到渠道的完整结束标记，无法确认是否由输出预算导致。请检查渠道连接并重新生成；已完成的内容保留。"
                    else:
                        hint = "响应 JSON 未完整结束，但渠道未提供明确的输出上限原因；请核对模型设置或渠道返回后重新生成。"
                if job.target_type == "script_asset_breakdown_batch":
                    action = "recall"
                    hint += "重新生成时将缩小失败范围，保留成功批次。"
        elif code in {"LOCAL_DATABASE_TRANSIENT", "LOCAL_DATABASE_CONSTRAINT", "LOCAL_DATABASE_ERROR"}:
            category, title = "storage", "结果已保存，写入失败"
            hint = "数据库暂时不可用，可重试本地保存。" if code == "LOCAL_DATABASE_TRANSIENT" else "需要修复写入问题后重新处理保存的响应，不调用模型。"
        elif code == "LOCAL_PROCESSING_ERROR":
            category, title = "system", "结果已保存，系统处理异常"
            hint = "需要修复系统后重新处理保存的响应；反复生成无法解决该系统错误。"
        elif code == "CONFLICT":
            category, title, action = "conflict", "当前内容或任务状态已变化", "none"
            hint = "请刷新并核对当前内容，再决定是否生成新方案。"
        if (job.target_type in {"episode_outline", "outline_agent"}
                and (payload.get("parameters") or {}).get("long_form_work_id")
                and (code == "OUTLINE_CAST_IDENTITY_UNRESOLVED" or "分集角色不属于当前故事设定：" in str(reason))):
            category, title, action = "cast_identity", "已保存结果，需要核对角色称呼", "reprocess"
            hint = "请核对未识别称呼对应的已有角色，再重新处理本批结果；不重新调用模型，已完成批次保留。"
        if (job.target_type == "episode_script_generation" and code == "MODEL_OUTPUT_POLLUTED"
                and not recovery.get("last_reprocess_error_code")):
            action = "reprocess"
            hint = "可先重新处理已保存响应：仅在能唯一确认有效正文时恢复，不调用模型；有歧义或缺失则停止。"
        if expired:
            action, hint = "retry", "保存的响应已超过保留期限，需要重新生成。"
    elif diagnostic.get("http_status"):
        category, title = "provider", "模型渠道请求失败"
        status = diagnostic["http_status"]
        if diagnostic.get("provider_error_code") == "get_channel_failed":
            reason = f"中转站获取可用渠道失败（HTTP {status}）。"
        else:
            reason = f"渠道返回 HTTP {status}：{diagnostic.get('provider_error_message') or reason}"
        if status in {400, 401, 402, 403, 404, 405, 415, 422}:
            hint = "请检查渠道地址、密钥、模型和请求参数，修正后重试。重试使用原任务模型及请求快照。"
        elif status == 429:
            hint = "渠道限流，请稍后重试。"
            if diagnostic.get("retry_after_seconds"):
                hint += f"渠道建议等待 {diagnostic['retry_after_seconds']} 秒。"
        else:
            hint = "未取得可用结果，可手动重试。"
    elif submission.get("status") == "submitted":
        category, title = "uncertain", "未收到可用模型结果"
        hint = "上游是否完成尚不确定。手动重试将发起新请求，可能产生重复生成。"
    elif code == "CONFLICT":
        category, title, action = "conflict", "当前内容或任务状态已变化", "none"
        hint = "请刷新并核对当前内容。"
    if code == "PROVIDER_CONTENT_BLOCKED":
        category, title, action = "provider", "模型渠道拦截了内容", "none"
        hint = "请核对请求内容与渠道规则。没有可恢复的有效脚本，系统不会自动重新调用或更换模型。"
    if job.job_type != "text":
        action = "none"
    if (payload.get("resolution") or {}).get("status") in {"superseded", "replacement_pending"}:
        action = "none"
        hint = "该任务已被后续任务替代，请查看最新结果。"
    return {
        "category": category, "title": title, "reason": str(reason)[:2000],
        "hint": hint, "action": action, "response_saved": saved,
        "error_code": code, "http_status": diagnostic.get("http_status"),
        "provider_error_code": diagnostic.get("provider_error_code"),
        "request_id": diagnostic.get("request_id"),
        "field_errors": output.get("field_errors", []),
        "invalid_fields": output.get("invalid_fields", []),
        "json_error_line": output.get("json_error_line"),
        "json_error_column": output.get("json_error_column"),
    }


def local_exception(exc: Exception) -> tuple[str, str, dict[str, Any]]:
    """Do not expose SQL, exception arguments, model content or credentials."""
    from sqlalchemy.exc import IntegrityError, OperationalError, SQLAlchemyError

    if isinstance(exc, IntegrityError):
        return "LOCAL_DATABASE_CONSTRAINT", "数据库约束校验失败，可能存在重复记录、无效关联或必填数据缺失；需核查写入逻辑。", {}
    if isinstance(exc, OperationalError):
        return "LOCAL_DATABASE_TRANSIENT", "数据库连接或执行失败，请恢复数据库服务后重试本地保存。", {}
    if isinstance(exc, SQLAlchemyError):
        return "LOCAL_DATABASE_ERROR", "数据库写入异常，需要检查数据库结构和写入逻辑。", {}
    return "LOCAL_PROCESSING_ERROR", f"系统处理程序异常（{type(exc).__name__}），需要修复后重新处理。", {}
