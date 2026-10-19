"""Explicit non-streaming Responses/Ollama transports; no paid format fallback."""

# ruff: noqa: RUF001

from time import perf_counter

import httpx

from app.core.errors import ProviderError, TimeoutError_, ValidationError
from app.core.outbound_http import outbound_client
from app.providers.http_errors import raise_for_provider_http_error


class StructuredTextProvider:
    def __init__(self, *, protocol, base_url, api_key, timeout_seconds, proxy_url=None):
        self.protocol = protocol
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.proxy_url = proxy_url

    async def _request(self, method, path, payload=None):
        try:
            async with outbound_client(timeout=self.timeout_seconds, proxy=self.proxy_url,
                                       follow_redirects=False) as client:
                response = await client.request(method, self.base_url + path,
                                                headers={"Authorization": f"Bearer {self.api_key}"} if self.api_key else {},
                                                **({"json": payload} if payload is not None else {}))
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接文本模型渠道，请检查网络") from exc
        raise_for_provider_http_error(response)
        try:
            value = response.json()
            if not isinstance(value, dict):
                raise ValueError
        except (ValueError, TypeError) as exc:
            raise ProviderError("模型渠道返回了无效响应对象") from exc
        return value

    async def discover_models(self):
        started = perf_counter()
        local = self.protocol == "ollama_chat"
        result = await self._request("GET", "/tags" if local else "/models")
        rows = result.get("models" if local else "data")
        if not isinstance(rows, list):
            raise ProviderError("模型渠道返回了无效模型列表")
        key = "name" if local else "id"
        names = sorted({row[key] for row in rows if isinstance(row, dict) and isinstance(row.get(key), str)})
        return names, round((perf_counter() - started) * 1000)

    async def generate_text(self, *, model, prompt, parameters):
        self.validate_text_parameters(parameters)
        parameters = dict(parameters)
        if self.protocol == "ollama_chat":
            return await self._ollama(model, prompt, parameters)
        if self.protocol != "openai_responses":
            raise ValidationError("未知结构化文本协议")
        effort = parameters.pop("reasoning_effort", None)
        if effort is not None:
            parameters["reasoning"] = {"effort": effort}
        result = await self._request("POST", "/responses", {
            **parameters, "model": model, "input": prompt, "stream": False, "store": False,
        })
        output = result.get("output")
        if not isinstance(output, list):
            raise ProviderError("Responses 响应缺少 output 列表")
        texts, refusals = [], []
        for item in output:
            if not isinstance(item, dict):
                raise ProviderError("Responses 输出项格式无效")
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message" or not isinstance(item.get("content"), list):
                raise ProviderError("Responses 返回了未请求的工具或非文本输出")
            for part in item["content"]:
                if not isinstance(part, dict):
                    raise ProviderError("Responses 文本块格式无效")
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    texts.append(part["text"])
                elif part.get("type") == "refusal" and isinstance(part.get("refusal"), str):
                    refusals.append(part["refusal"])
                else:
                    raise ProviderError("Responses 返回了未支持的内容块")
        status = result.get("status")
        incomplete = result.get("incomplete_details")
        if incomplete is not None and not isinstance(incomplete, dict):
            raise ProviderError("Responses 未完成原因格式无效")
        reason = (incomplete or {}).get("reason") if status == "incomplete" else status
        return {"text": "".join(texts), "usage": result.get("usage") or {}, "finish_reason": reason,
                "stream_terminal_seen": status == "completed" or (status == "incomplete" and reason == "max_output_tokens"),
                **({"refusal": "\n".join(refusals)} if refusals else {}),
                **({"response_model": result["model"]} if isinstance(result.get("model"), str) else {}),
                **({"provider_request_id": result["id"]} if isinstance(result.get("id"), str) else {})}

    async def _ollama(self, model, prompt, parameters):
        options = {key: parameters[key] for key in ("temperature", "top_p", "top_k") if key in parameters}
        if "max_tokens" in parameters:
            options["num_predict"] = parameters["max_tokens"]
        payload = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": False,
                   "options": options, **{key: parameters[key] for key in ("format", "think") if key in parameters}}
        result = await self._request("POST", "/chat", payload)
        message = result.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise ProviderError("Ollama 响应缺少文本消息")
        if message.get("tool_calls"):
            raise ProviderError("Ollama 返回了未请求的工具调用，未执行工具")
        return {"text": message["content"], "finish_reason": result.get("done_reason"),
                "stream_terminal_seen": result.get("done") is True,
                "usage": {"prompt_tokens": result.get("prompt_eval_count", 0),
                          "completion_tokens": result.get("eval_count", 0)},
                **({"response_model": result["model"]} if isinstance(result.get("model"), str) else {})}

    def validate_text_parameters(self, parameters):
        if self.protocol not in {"ollama_chat", "openai_responses"}:
            raise ValidationError("未知结构化文本协议")
        allowed = ({"format", "max_tokens", "temperature", "top_p", "top_k", "think"}
                   if self.protocol == "ollama_chat" else
                   {"max_output_tokens", "reasoning_effort", "reasoning", "temperature", "top_p", "text"})
        unknown = set(parameters) - allowed
        if unknown:
            raise ValidationError("文本协议存在未适配参数，未发送请求：" + sorted(unknown)[0])
        if "reasoning_effort" in parameters and "reasoning" in parameters:
            raise ValidationError("Responses 推理参数重复，未发送请求")
        for key in ("max_tokens", "max_output_tokens"):
            if key in parameters and (type(parameters[key]) is not int or parameters[key] <= 0):
                raise ValidationError("文本输出预算必须为正整数，未发送请求")
