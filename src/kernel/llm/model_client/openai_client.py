from __future__ import annotations

import base64
import json
import asyncio
import math
import threading
import inspect
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, AsyncIterator

from src.kernel.llm.payload.tooling import LLMUsable
from src.kernel.llm.tool_call_compat import (
    build_tool_call_compat_prompt,
    parse_tool_call_compat_response,
)

from ..payload import Image, LLMPayload, Text, ToolCall, ToolResult
from ..roles import ROLE
from ..exceptions import LLMError
from .base import StreamEvent


def _build_httpx_timeout(timeout: float | None):
    import httpx

    if not isinstance(timeout, (int, float)):
        return None

    total = float(timeout)
    if total <= 0:
        return None

    connect_timeout = min(total, 10.0)
    pool_timeout = min(total, 5.0)
    return httpx.Timeout(
        timeout=total,
        connect=connect_timeout,
        read=total,
        write=total,
        pool=pool_timeout,
    )


def _is_data_url(value: str) -> bool:
    return value.startswith("data:")


def _image_to_data_url(value: str) -> str:
    if value.startswith("base64|"):
        # 兼容设计稿："base64|..."（不含 mime）
        b64 = value.split("|", 1)[1]
        return f"data:image/png;base64,{b64}"

    if _is_data_url(value):
        return value

    path = Path(value)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Image file not found: {value}")

    data = path.read_bytes()
    b64 = base64.b64encode(data).decode("ascii")
    # 简化：默认 png
    return f"data:image/png;base64,{b64}"


def _payloads_to_openai_messages(
    payloads: list[LLMPayload],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    messages: list[dict[str, Any]] = []
    tools: list[dict[str, Any]] = []

    for payload in payloads:
        if payload.role == ROLE.TOOL:
            # TOOL role 不进入 messages；只收集 tools schema
            def to_openai_tool(tool) -> dict[str, Any]:
                schema = tool.to_schema()
                # 兼容两类 schema：
                # 1) 已经是 OpenAI tools 格式：{"type":"function","function":{...}}
                # 2) 仅 function schema：{"name":...,"description":...,"parameters":...}
                if schema.get("type") == "function" and "function" in schema:
                    result = schema
                else:
                    result = {"type": "function", "function": schema}

                # 为所有 LLMUsable 自动注入 reason 必填参数
                func = result.get("function", {})
                params = func.get("parameters", {})
                props = params.get("properties", {})
                if "reason" not in props:
                    props["reason"] = {
                        "type": "string",
                        "description": "说明你选择此动作/工具的原因",
                    }
                    params["properties"] = props
                    required = params.get("required", [])
                    if "reason" not in required:
                        required.append("reason")
                    params["required"] = required
                    func["parameters"] = params
                    result["function"] = func

                return result

            for item in payload.content:
                tools.append(to_openai_tool(item))
            continue

        if payload.role == ROLE.TOOL_RESULT:
            # OpenAI tool message
            # content 里可能是 ToolResult；在 request.py 里会规范化成文本
            tool_call_id = None
            content_text = None
            for part in payload.content:
                if isinstance(part, ToolResult):
                    if tool_call_id is None and part.call_id:
                        tool_call_id = part.call_id
                    if content_text is None:
                        content_text = part.to_text()
                    continue

                if isinstance(part, Text) and content_text is None:
                    content_text = part.text

            if content_text is None:
                content_text = ""

            messages.append(
                {
                    "role": "tool",
                    "content": content_text,
                    **({"tool_call_id": tool_call_id} if tool_call_id else {}),
                }
            )
            continue

        role = payload.role.value

        if payload.role == ROLE.ASSISTANT:
            tool_calls: list[dict[str, Any]] = []
            text_parts: list[str] = []

            for idx, part in enumerate(payload.content):
                if isinstance(part, ToolCall):
                    if isinstance(part.args, dict):
                        args_text = json.dumps(part.args, ensure_ascii=False)
                    else:
                        args_text = str(part.args)
                    call_id = part.id or f"call_{idx}"
                    tool_calls.append(
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": part.name,
                                "arguments": args_text,
                            },
                        }
                    )
                    continue

                if isinstance(part, Text):
                    text_parts.append(part.text)
                    continue

            if tool_calls:
                messages.append(
                    {
                        "role": role,
                        "content": "".join(text_parts),
                        "tool_calls": tool_calls,
                    }
                )
                continue

        # content 支持 list[Content]，需转成 OpenAI 的多模态 content parts
        if len(payload.content) == 1 and isinstance(payload.content[0], Text):
            messages.append({"role": role, "content": payload.content[0].text})
            continue

        parts: list[dict[str, Any]] = []
        for part in payload.content:
            if isinstance(part, Text):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, Image):
                url = _image_to_data_url(part.value)
                parts.append({"type": "image_url", "image_url": {"url": url}})
            else:
                # 兜底：转成文本
                parts.append({"type": "text", "text": str(part)})

        messages.append({"role": role, "content": parts})

    return messages, tools


class OpenAIChatClient:
    """OpenAI provider。

    依赖 openai>=2.x。

    配置来源：由上层传入的单个模型配置 dict（见 `LLMRequest` 的 model_set 约束）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: dict[
            tuple[str, str | None, int, float | None, bool, bool], Any
        ] = {}
        self._sync_clients: dict[
            tuple[str, str | None, float | None, bool, bool], Any
        ] = {}
        self._sync_http_executors: dict[int, ThreadPoolExecutor] = {}
        self._platform_info: Any = None

    def _get_loop_key(self) -> int:
        try:
            loop = asyncio.get_running_loop()
            return id(loop)
        except RuntimeError:
            return 0

    def _ensure_openai_platform_info(self) -> Any:
        with self._lock:
            if self._platform_info is not None:
                return self._platform_info

        try:
            from openai._base_client import get_platform

            platform_info = get_platform()
        except Exception:
            platform_info = None

        with self._lock:
            if self._platform_info is None:
                self._platform_info = platform_info
            return self._platform_info

    def _get_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
    ):
        loop_key = self._get_loop_key()
        timeout_key = float(timeout) if isinstance(timeout, (int, float)) else None
        cache_key = (api_key, base_url, loop_key, timeout_key, trust_env, force_ipv4)
        with self._lock:
            cached = self._clients.get(cache_key)
            if cached is not None:
                return cached

        from openai import AsyncOpenAI
        import httpx

        class _LoggingAsyncTransport(httpx.AsyncBaseTransport):
            def __init__(self, inner: "httpx.AsyncBaseTransport") -> None:
                self._inner = inner

            async def handle_async_request(
                self, request: "httpx.Request"
            ) -> "httpx.Response":
                response = await self._inner.handle_async_request(request)
                return response

        limits = httpx.Limits(
            max_connections=100,
            max_keepalive_connections=5,
            keepalive_expiry=10.0,
        )
        headers = None
        timeout_config = _build_httpx_timeout(timeout)

        base_transport = (
            httpx.AsyncHTTPTransport(local_address="0.0.0.0")
            if force_ipv4
            else httpx.AsyncHTTPTransport()
        )
        transport = _LoggingAsyncTransport(base_transport)
        http_client_kwargs: dict[str, Any] = {
            "transport": transport,
            "trust_env": trust_env,
        }
        if timeout_config is not None:
            http_client_kwargs["timeout"] = timeout_config
        if limits:
            http_client_kwargs["limits"] = limits
        if headers:
            http_client_kwargs["headers"] = headers
        http_client = httpx.AsyncClient(**http_client_kwargs)

        kwargs: dict[str, Any] = {"api_key": api_key, "http_client": http_client}
        if base_url:
            kwargs["base_url"] = base_url
        if isinstance(timeout, (int, float)):
            kwargs["timeout"] = float(timeout)
        # 重要：重试策略完全由 policy 控制，provider 侧必须禁用自动重试。
        kwargs["max_retries"] = 0

        client = AsyncOpenAI(**kwargs)
        try:
            platform_info = self._ensure_openai_platform_info()
            if platform_info is not None:
                client._platform = platform_info
        except Exception:
            pass
        with self._lock:
            self._clients[cache_key] = client
        return client

    def _evict_async_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
    ) -> Any | None:
        loop_key = self._get_loop_key()
        timeout_key = float(timeout) if isinstance(timeout, (int, float)) else None
        cache_key = (api_key, base_url, loop_key, timeout_key, trust_env, force_ipv4)
        with self._lock:
            return self._clients.pop(cache_key, None)

    def _get_sync_client(
        self,
        *,
        api_key: str,
        base_url: str | None,
        timeout: float | None,
        trust_env: bool,
        force_ipv4: bool,
    ):
        timeout_key = float(timeout) if isinstance(timeout, (int, float)) else None
        cache_key = (api_key, base_url, timeout_key, trust_env, force_ipv4)
        with self._lock:
            cached = self._sync_clients.get(cache_key)
            if cached is not None:
                return cached

        from openai import OpenAI
        import httpx

        transport = (
            httpx.HTTPTransport(local_address="0.0.0.0")
            if force_ipv4
            else httpx.HTTPTransport()
        )
        http_client_kwargs: dict[str, Any] = {
            "transport": transport,
            "trust_env": trust_env,
        }
        timeout_config = _build_httpx_timeout(timeout)
        if timeout_config is not None:
            http_client_kwargs["timeout"] = timeout_config
        http_client = httpx.Client(**http_client_kwargs)

        kwargs: dict[str, Any] = {"api_key": api_key, "http_client": http_client}
        if base_url:
            kwargs["base_url"] = base_url
        if isinstance(timeout, (int, float)):
            kwargs["timeout"] = float(timeout)
        kwargs["max_retries"] = 0

        client = OpenAI(**kwargs)
        try:
            platform_info = self._ensure_openai_platform_info()
            if platform_info is not None:
                client._platform = platform_info
        except Exception:
            pass
        with self._lock:
            self._sync_clients[cache_key] = client
        return client

    def _get_sync_http_executor(self) -> ThreadPoolExecutor:
        loop_key = self._get_loop_key()
        with self._lock:
            executor = self._sync_http_executors.get(loop_key)
            if executor is None:
                executor = ThreadPoolExecutor(max_workers=4)
                self._sync_http_executors[loop_key] = executor
            return executor

    async def create(
        self,
        *,
        model_name: str,
        payloads: list[LLMPayload],
        tools: list[LLMUsable],
        request_name: str,
        model_set: Any,
        stream: bool,
    ) -> tuple[
        str | None, list[dict[str, Any]] | None, AsyncIterator[StreamEvent] | None
    ]:
        if not isinstance(model_set, dict):
            raise TypeError("OpenAIChatClient 期望 model_set 为单个模型配置 dict")

        api_key = str(model_set.get("api_key") or "")
        if not api_key:
            raise ValueError("model.api_key 不能为空")

        base_url = model_set.get("base_url")
        base_url = str(base_url) if base_url else None
        timeout = model_set.get("timeout")

        max_tokens = model_set.get("max_tokens")
        temperature = model_set.get("temperature")
        extra_params = model_set.get("extra_params")
        if extra_params is None:
            extra_params = {}
        if not isinstance(extra_params, dict):
            raise ValueError("model.extra_params 必须是 dict")

        extra_params = dict(extra_params)
        trust_env = extra_params.pop("trust_env", None)
        trust_env = bool(trust_env) if trust_env is not None else True
        force_ipv4 = bool(extra_params.pop("force_ipv4", False))
        force_sync_http = bool(extra_params.pop("force_sync_http", False))
        extra_params.pop("context_reserve_ratio", None)
        extra_params.pop("context_reserve_tokens", None)

        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout) if isinstance(timeout, (int, float)) else None,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )
        messages, openai_tools = _payloads_to_openai_messages(payloads)
        tool_call_compat = bool(model_set.get("tool_call_compat", False))

        if tool_call_compat and openai_tools:
            compat_prompt = build_tool_call_compat_prompt(openai_tools)
            messages = list(messages)
            messages.append({"role": "user", "content": compat_prompt})

        params: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }
        if isinstance(max_tokens, int):
            params["max_tokens"] = max_tokens
        if isinstance(temperature, (int, float)):
            params["temperature"] = float(temperature)
        if openai_tools and not tool_call_compat:
            params["tools"] = openai_tools
            if "tool_choice" not in params:
                # Some providers require explicit auto tool choice to return tool_calls
                params["tool_choice"] = "required"

        # 允许每模型注入额外参数（如 top_p/response_format/tool_choice 等）
        params.update(extra_params)

        if not stream and force_sync_http:
            sync_client = self._get_sync_client(
                api_key=api_key,
                base_url=base_url,
                timeout=float(timeout) if isinstance(timeout, (int, float)) else None,
                trust_env=trust_env,
                force_ipv4=force_ipv4,
            )

            def _sync_create():
                return sync_client.chat.completions.create(**params)

            loop = asyncio.get_running_loop()
            resp = await loop.run_in_executor(
                self._get_sync_http_executor(), _sync_create
            )
            if not resp.choices:
                raise LLMError(f"OpenAI API returned an empty choices list. Response: {resp}")
            msg = resp.choices[0].message
            tool_calls = []
            message_content = msg.content or ""
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    try:
                        args = (
                            json.loads(tc.function.arguments)
                            if tc.function.arguments
                            else {}
                        )
                    except Exception:
                        args = tc.function.arguments
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "args": args,
                        }
                    )

            fn_call = getattr(msg, "function_call", None)
            fn_name = getattr(fn_call, "name", None) if fn_call is not None else None
            if not tool_calls and isinstance(fn_name, str) and fn_name:
                fn_args_raw = getattr(fn_call, "arguments", None)
                try:
                    args = json.loads(fn_args_raw) if fn_args_raw else {}
                except Exception:
                    args = fn_args_raw
                tool_calls.append(
                    {
                        "id": None,
                        "name": fn_name,
                        "args": args,
                    }
                )
            if tool_call_compat and openai_tools and not tool_calls:
                parsed_message, parsed_calls = parse_tool_call_compat_response(message_content)
                return parsed_message, parsed_calls, None
            return message_content, tool_calls, None

        if not stream:
            try:
                resp = await client.chat.completions.create(**params)
            except Exception as e:
                err_name = type(e).__name__.lower()
                err_text = str(e).lower()
                if (
                    "timeout" in err_name
                    or "timeout" in err_text
                    or "connect" in err_name
                    or "network" in err_name
                    or "transport" in err_name
                ):
                    stale = self._evict_async_client(
                        api_key=api_key,
                        base_url=base_url,
                        timeout=float(timeout)
                        if isinstance(timeout, (int, float))
                        else None,
                        trust_env=trust_env,
                        force_ipv4=force_ipv4,
                    )
                    if stale is not None:
                        try:
                            await stale.close()
                        except Exception:
                            pass
                raise
            if not resp.choices:
                raise LLMError(f"OpenAI API returned an empty choices list. Response: {resp}")
            msg = resp.choices[0].message
            tool_calls = []
            message_content = msg.content or ""
            if getattr(msg, "tool_calls", None):
                for tc in msg.tool_calls:
                    try:
                        args = (
                            json.loads(tc.function.arguments)
                            if tc.function.arguments
                            else {}
                        )
                    except Exception:
                        args = tc.function.arguments
                    tool_calls.append(
                        {
                            "id": tc.id,
                            "name": tc.function.name,
                            "args": args,
                        }
                    )

            fn_call = getattr(msg, "function_call", None)
            fn_name = getattr(fn_call, "name", None) if fn_call is not None else None
            if not tool_calls and isinstance(fn_name, str) and fn_name:
                fn_args_raw = getattr(fn_call, "arguments", None)
                try:
                    args = json.loads(fn_args_raw) if fn_args_raw else {}
                except Exception:
                    args = fn_args_raw
                tool_calls.append(
                    {
                        "id": None,
                        "name": fn_name,
                        "args": args,
                    }
                )
            if tool_call_compat and openai_tools and not tool_calls:
                parsed_message, parsed_calls = parse_tool_call_compat_response(message_content)
                return parsed_message, parsed_calls, None
            return message_content, tool_calls, None

        stream_resp = await client.chat.completions.create(**params, stream=True)

        async def iter_events() -> AsyncIterator[StreamEvent]:
            try:
                async for chunk in stream_resp:
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    content = getattr(delta, "content", None)
                    if content:
                        yield StreamEvent(text_delta=content)

                    # 工具调用增量：可能分段传 arguments
                    tool_calls_delta = getattr(delta, "tool_calls", None)
                    if tool_calls_delta:
                        for tc in tool_calls_delta:
                            fn = getattr(tc, "function", None)
                            yield StreamEvent(
                                tool_call_id=getattr(tc, "id", None),
                                tool_name=getattr(fn, "name", None) if fn else None,
                                tool_args_delta=(
                                    getattr(fn, "arguments", None) if fn else None
                                ),
                            )

                    function_call_delta = getattr(delta, "function_call", None)
                    if function_call_delta and not tool_calls_delta:
                        yield StreamEvent(
                            tool_call_id="function_call",
                            tool_name=getattr(function_call_delta, "name", None),
                            tool_args_delta=getattr(function_call_delta, "arguments", None),
                        )
            finally:
                close = getattr(stream_resp, "aclose", None)
                if callable(close):
                    maybe_awaitable = close()
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                    return

                close_sync = getattr(stream_resp, "close", None)
                if callable(close_sync):
                    close_sync()

        return None, None, iter_events()

    async def create_embedding(
        self,
        *,
        model_name: str,
        inputs: list[str],
        request_name: str,
        model_set: Any,
    ) -> list[list[float]]:
        """发起 embedding 请求。"""
        del request_name
        if not isinstance(model_set, dict):
            raise TypeError("OpenAIChatClient 期望 model_set 为单个模型配置 dict")
        if not inputs:
            raise ValueError("inputs 不能为空")

        api_key = str(model_set.get("api_key") or "")
        if not api_key:
            raise ValueError("model.api_key 不能为空")

        base_url = model_set.get("base_url")
        base_url = str(base_url) if base_url else None
        timeout = model_set.get("timeout")

        extra_params = model_set.get("extra_params")
        if extra_params is None:
            extra_params = {}
        if not isinstance(extra_params, dict):
            raise ValueError("model.extra_params 必须是 dict")

        extra_params = dict(extra_params)
        trust_env = extra_params.pop("trust_env", None)
        trust_env = bool(trust_env) if trust_env is not None else True
        force_ipv4 = bool(extra_params.pop("force_ipv4", False))
        extra_params.pop("context_reserve_ratio", None)
        extra_params.pop("context_reserve_tokens", None)

        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout) if isinstance(timeout, (int, float)) else None,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )

        params: dict[str, Any] = {
            "model": model_name,
            "input": inputs,
        }
        params.update(extra_params)

        resp = await client.embeddings.create(**params)
        data = getattr(resp, "data", None)
        if not data:
            return []

        out: list[list[float]] = []
        for item in data:
            vec = getattr(item, "embedding", None)
            if isinstance(vec, list):
                out.append([float(v) for v in vec])
        return out

    async def create_rerank(
        self,
        *,
        model_name: str,
        query: str,
        documents: list[Any],
        top_n: int | None,
        request_name: str,
        model_set: Any,
    ) -> list[dict[str, Any]]:
        """发起 rerank 请求。"""
        del request_name
        if not isinstance(model_set, dict):
            raise TypeError("OpenAIChatClient 期望 model_set 为单个模型配置 dict")
        if not query:
            raise ValueError("query 不能为空")
        if not documents:
            raise ValueError("documents 不能为空")

        api_key = str(model_set.get("api_key") or "")
        if not api_key:
            raise ValueError("model.api_key 不能为空")

        base_url = model_set.get("base_url")
        base_url = str(base_url) if base_url else None
        timeout = model_set.get("timeout")

        extra_params = model_set.get("extra_params")
        if extra_params is None:
            extra_params = {}
        if not isinstance(extra_params, dict):
            raise ValueError("model.extra_params 必须是 dict")

        extra_params = dict(extra_params)
        trust_env = extra_params.pop("trust_env", None)
        trust_env = bool(trust_env) if trust_env is not None else True
        force_ipv4 = bool(extra_params.pop("force_ipv4", False))
        extra_params.pop("context_reserve_ratio", None)
        extra_params.pop("context_reserve_tokens", None)

        client = self._get_client(
            api_key=api_key,
            base_url=base_url,
            timeout=float(timeout) if isinstance(timeout, (int, float)) else None,
            trust_env=trust_env,
            force_ipv4=force_ipv4,
        )

        rerank_api = getattr(client, "rerank", None)
        rerank_create = getattr(rerank_api, "create", None) if rerank_api is not None else None
        if callable(rerank_create):
            params: dict[str, Any] = {
                "model": model_name,
                "query": query,
                "documents": documents,
            }
            if isinstance(top_n, int) and top_n > 0:
                params["top_n"] = top_n
            maybe_resp = rerank_create(**params)
            if inspect.isawaitable(maybe_resp):
                resp = await maybe_resp
            else:
                resp = maybe_resp
            data = getattr(resp, "results", None) or getattr(resp, "data", None) or []
            out: list[dict[str, Any]] = []
            for rec in data:
                idx = getattr(rec, "index", None)
                score = getattr(rec, "relevance_score", None)
                if score is None:
                    score = getattr(rec, "score", None)
                index = int(idx) if isinstance(idx, int) else 0
                out.append(
                    {
                        "index": index,
                        "score": float(score) if isinstance(score, (int, float)) else 0.0,
                        "document": documents[index] if 0 <= index < len(documents) else None,
                    }
                )
            return out

        text_documents = [self._to_document_text(doc) for doc in documents]
        embeddings = await self.create_embedding(
            model_name=model_name,
            inputs=[query, *text_documents],
            request_name="",
            model_set=model_set,
        )
        if len(embeddings) < 2:
            return []

        query_vec = embeddings[0]
        doc_vecs = embeddings[1:]
        scored: list[dict[str, Any]] = []
        for idx, vec in enumerate(doc_vecs):
            scored.append(
                {
                    "index": idx,
                    "score": self._cosine_similarity(query_vec, vec),
                    "document": documents[idx],
                }
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        if isinstance(top_n, int) and top_n > 0:
            return scored[:top_n]
        return scored

    @staticmethod
    def _to_document_text(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    @staticmethod
    def _cosine_similarity(left: list[float], right: list[float]) -> float:
        if not left or not right:
            return 0.0
        size = min(len(left), len(right))
        if size == 0:
            return 0.0

        dot = 0.0
        left_norm = 0.0
        right_norm = 0.0
        for i in range(size):
            lv = float(left[i])
            rv = float(right[i])
            dot += lv * rv
            left_norm += lv * lv
            right_norm += rv * rv

        denom = math.sqrt(left_norm) * math.sqrt(right_norm)
        if denom == 0:
            return 0.0
        return dot / denom
