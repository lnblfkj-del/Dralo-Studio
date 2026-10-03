"""Google AI Studio Gemini 原生 REST Provider Adapter。"""

from time import perf_counter

import httpx
from app.core.outbound_http import outbound_client

from app.core.errors import ProviderError, TimeoutError_
from app.providers.http_errors import raise_for_provider_http_error


class GoogleGeminiProvider:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: int,
        proxy_url: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.proxy_url = proxy_url

    @property
    def headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self.api_key}

    async def discover_models(self) -> tuple[list[str], int]:
        started = perf_counter()
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=False,
            ) as client:
                response = await client.get(
                    f"{self.base_url}/models", headers=self.headers
                )
                self._raise_for_provider_error(response)
                payload = response.json()
                if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
                    raise ValueError
        except ValueError as exc:
            raise ProviderError("Google AI Studio 模型列表格式不兼容") from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接 Google AI Studio，请检查网络") from exc
        try:
            models = sorted(
                item["name"].removeprefix("models/")
                for item in payload.get("models", [])
                if isinstance(item, dict)
                and isinstance(item.get("name"), str)
                and "generateContent" in item.get("supportedGenerationMethods", [])
            )
        except (TypeError, ValueError) as exc:
            raise ProviderError("Google AI Studio 返回的数据格式不兼容") from exc
        return models, round((perf_counter() - started) * 1000)

    async def generate_text(
        self, *, model: str, prompt: str, parameters: dict[str, object]
    ) -> dict[str, object]:
        model_name = model.removeprefix("models/")
        parts = [{"text": prompt}]
        for image in parameters.get("style_reference_images", []):
            header, data = image.split(",", 1)
            parts.append({"inlineData": {"mimeType": header[5:].split(";")[0], "data": data}})
        payload: dict[str, object] = {
            "contents": [{"role": "user", "parts": parts}]
        }
        if parameters:
            aliases = {"max_tokens": "maxOutputTokens", "max_completion_tokens": "maxOutputTokens", "top_p": "topP", "top_k": "topK", "stop": "stopSequences"}
            allowed = {"temperature", "maxOutputTokens", "topP", "topK", "stopSequences", "responseMimeType", "responseSchema", "thinkingConfig"}
            config = {aliases.get(key, key): value for key, value in parameters.items() if aliases.get(key, key) in allowed}
            if isinstance(config.get("stopSequences"), str):
                config["stopSequences"] = [config["stopSequences"]]
            payload["generationConfig"] = config
        try:
            async with outbound_client(
                timeout=self.timeout_seconds,
                proxy=self.proxy_url,
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    f"{self.base_url}/models/{model_name}:generateContent",
                    headers=self.headers,
                    json=payload,
                )
                self._raise_for_provider_error(response)
                data = response.json()
        except ValueError as exc:
            raise ProviderError("Google AI Studio 返回的数据格式不兼容") from exc
        except httpx.TimeoutException as exc:
            raise TimeoutError_() from exc
        except httpx.HTTPError as exc:
            raise ProviderError("无法连接 Google AI Studio，请检查网络") from exc
        try:
            candidate = data["candidates"][0]
            finish_reason = candidate.get("finishReason")
            parts = (candidate.get("content") or {}).get("parts") or []
            text = "".join(
                part["text"]
                for part in parts
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            )
            if not text and str(finish_reason or "").lower() not in {"max_tokens", "max_output_tokens"}:
                raise TypeError
            metadata = data.get("usageMetadata", {})
            usage = {
                "prompt_tokens": metadata.get("promptTokenCount", 0),
                "completion_tokens": metadata.get("candidatesTokenCount", 0),
                "total_tokens": metadata.get("totalTokenCount", 0),
            }
            if "promptTokenCount" not in metadata or "candidatesTokenCount" not in metadata:
                usage = {}
            else:
                usage["completion_tokens"] += metadata.get("thoughtsTokenCount", 0)
                if "cachedContentTokenCount" in metadata:
                    usage["cached_tokens"] = metadata["cachedContentTokenCount"]
        except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
            raise ProviderError("Google AI Studio 返回的数据格式不兼容") from exc
        return {"text": text, "usage": usage, "finish_reason": finish_reason}

    async def generate_image(self, **_kwargs: object) -> dict[str, object]:
        raise ProviderError("Google Gemini 原生协议当前仅接入文本生成")

    @staticmethod
    def _raise_for_provider_error(response: httpx.Response) -> None:
        raise_for_provider_http_error(response)
