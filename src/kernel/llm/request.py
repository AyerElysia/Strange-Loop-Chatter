"""LLM 请求模块

提供 LLMRequest 类，用于构建和执行 LLM 请求。

LLMRequest 支持：
- 构建 LLMPayload 列表
- 负载均衡和重试策略
- 指标收集
- 流式和非流式响应
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, field
from typing import Any, Self

from src.kernel.llm.payload.tooling import LLMUsable

from .context import LLMContextManager
from .exceptions import LLMConfigurationError, classify_exception
from .model_client import ModelClientRegistry
from .monitor import RequestMetrics, RequestTimer, get_global_collector
from .payload import LLMPayload, Text, ToolResult
from .policy import RoundRobinPolicy
from .policy.base import Policy
from .response import LLMResponse
from .roles import ROLE
from .types import ModelEntry, ModelSet, RequestType
from .token_counter import count_payload_tokens


def _normalize_tool_result_payload(payload: LLMPayload) -> LLMPayload:
    if payload.role != ROLE.TOOL_RESULT:
        return payload

    # 允许 ToolResult 或任意对象。
    # 重要：ToolResult 需要保留 call_id（用于 OpenAI tool message 的 tool_call_id）。
    out_content: list[Any] = []
    for part in payload.content:
        if isinstance(part, ToolResult):
            out_content.append(part)
        elif isinstance(part, Text):
            out_content.append(part)
        else:
            out_content.append(Text(str(part)))

    return LLMPayload(ROLE.TOOL_RESULT, out_content)  # type: ignore[arg-type]


def _extract_tools(payloads: list[LLMPayload]) -> list[LLMUsable]:
    tools: list[LLMUsable] = []
    for payload in payloads:
        if payload.role != ROLE.TOOL:
            continue
        for part in payload.content:
            if isinstance(part, LLMUsable):
                tools.append(part)
    return tools


@dataclass(slots=True)
class LLMRequest:
    """LLMRequest：构建 payload 并执行请求。"""

    model_set: ModelSet
    request_name: str = ""

    payloads: list[LLMPayload] = field(default_factory=list)
    policy: Policy | None = None
    clients: ModelClientRegistry | None = None
    context_manager: LLMContextManager | None = None
    enable_metrics: bool = True  # 是否启用指标收集
    request_type: RequestType = RequestType.COMPLETIONS

    def __post_init__(self) -> None:
        if self.payloads is None:
            self.payloads = []
        if self.policy is None:
            self.policy = RoundRobinPolicy()
        if self.clients is None:
            self.clients = ModelClientRegistry()
        if self.context_manager is None:
            self.context_manager = LLMContextManager()

    def add_payload(self, payload: LLMPayload, position=None) -> Self:
        if position is not None:
            self.payloads.insert(int(position), payload)
        else:
            self.payloads.append(payload)
        self._maybe_trim_payloads()
        return self

    def _compute_effective_context_budget(self, model: ModelEntry) -> int | None:
        max_context = model.get("max_context")
        if not isinstance(max_context, int) or max_context <= 0:
            return None

        extra_params = model.get("extra_params")
        if not isinstance(extra_params, dict):
            extra_params = {}

        reserve_tokens = extra_params.get("context_reserve_tokens")
        fixed_reserve = reserve_tokens if isinstance(reserve_tokens, int) and reserve_tokens > 0 else 0

        reserve_ratio = extra_params.get("context_reserve_ratio")
        ratio = 0.0
        if isinstance(reserve_ratio, (int, float)):
            ratio = max(0.0, float(reserve_ratio))
        ratio_reserve = int(math.floor(max_context * ratio))

        reserve = max(fixed_reserve, ratio_reserve)
        effective_budget = max_context - reserve
        return effective_budget if effective_budget > 0 else 1

    def _maybe_trim_payloads_for_model(self, payloads: list[LLMPayload], model: ModelEntry) -> list[LLMPayload]:
        if not self.context_manager:
            return payloads

        budget = self._compute_effective_context_budget(model)
        model_identifier = model.get("model_identifier")

        if budget is None or not isinstance(model_identifier, str) or not model_identifier:
            return self.context_manager.maybe_trim(payloads)

        try:
            if count_payload_tokens(payloads, model_identifier=model_identifier) <= budget:
                return self.context_manager.maybe_trim(payloads)
        except RuntimeError:
            return self.context_manager.maybe_trim(payloads)

        def token_counter(items: list[LLMPayload]) -> int:
            try:
                return count_payload_tokens(items, model_identifier=model_identifier)
            except RuntimeError:
                return 0

        return self.context_manager.maybe_trim(
            payloads,
            max_token_budget=budget,
            token_counter=token_counter,
        )

    async def send(self, auto_append_response: bool = True, *, stream: bool = True) -> LLMResponse:
        self._maybe_trim_payloads()
        model_set = _validate_model_set(self.model_set)

        # TOOL_RESULT payload 规范化（确保 provider 端可读）
        payloads = [_normalize_tool_result_payload(p) for p in self.payloads]
        tools = _extract_tools(payloads)

        assert self.policy is not None
        session = self.policy.new_session(model_set=model_set, request_name=self.request_name)

        last_error: BaseException | None = None
        retry_count = 0
        step = session.first()
        
        while step.model is not None:
            model = _validate_model_entry(step.model)

            model_identifier = model.get("model_identifier")
            if not isinstance(model_identifier, str) or not model_identifier:
                raise LLMConfigurationError("model.model_identifier 必须是非空字符串")

            if step.delay_seconds and step.delay_seconds > 0:
                await asyncio.sleep(step.delay_seconds)

            payloads = self._maybe_trim_payloads_for_model(payloads, model)
            self.payloads = list(payloads)

            assert self.clients is not None
            client = self.clients.get_client_for_model(model)

            # 开始计时
            timer = RequestTimer()

            try:
                with timer:
                    timeout_seconds = model.get("timeout")
                    create_task = client.create(
                        model_name=model_identifier,
                        payloads=payloads,
                        tools=tools,
                        request_name=self.request_name,
                        model_set=model,
                        stream=stream,
                    )

                    if isinstance(timeout_seconds, (int, float)) and timeout_seconds > 0:
                        message, tool_calls, stream_iter = await asyncio.wait_for(
                            create_task,
                            timeout=float(timeout_seconds),
                        )
                    else:
                        message, tool_calls, stream_iter = await create_task

                resp = LLMResponse(
                    _stream=stream_iter,
                    _upper=self,
                    _auto_append_response=auto_append_response,
                    payloads=list(self.payloads),
                    model_set=model_set,
                    context_manager=self.context_manager,
                    tool_call_compat=bool(model.get("tool_call_compat", False)),
                    message=message,
                    call_list=[],
                )

                # 非流：立即解析 tool_calls
                if tool_calls:
                    from .payload import ToolCall

                    resp.call_list = [
                        ToolCall(id=tc.get("id"), name=tc.get("name", ""), args=tc.get("args", {})) for tc in tool_calls
                    ]

                # 记录成功指标
                if self.enable_metrics:
                    metrics = RequestMetrics(
                        model_name=model_identifier,
                        request_name=self.request_name,
                        latency=timer.elapsed,
                        success=True,
                        stream=stream,
                        retry_count=retry_count,
                        model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    )
                    get_global_collector().record_request(metrics)

                return resp
                
            except BaseException as e:
                # 将原始异常转换为标准化 LLM 异常
                classified_error = classify_exception(e, model=model_identifier)
                last_error = classified_error

                # 记录失败指标
                if self.enable_metrics:
                    metrics = RequestMetrics(
                        model_name=model_identifier,
                        request_name=self.request_name,
                        latency=timer.elapsed,
                        success=False,
                        error=str(classified_error),
                        error_type=type(classified_error).__name__,
                        stream=stream,
                        retry_count=retry_count,
                        model_index=step.meta.get("model_index", 0) if step.meta else 0,
                    )
                    get_global_collector().record_request(metrics)
                
                retry_count += 1
                step = session.next_after_error(classified_error)

        assert last_error is not None
        raise last_error

    def _maybe_trim_payloads(self) -> None:
        if not self.context_manager:
            return
        self.payloads = self.context_manager.maybe_trim(self.payloads)


def _validate_model_entry(model: dict[str, Any]) -> ModelEntry:
    required = [
        "api_provider",
        "base_url",
        "model_identifier",
        "api_key",
        "client_type",
        "max_retry",
        "timeout",
        "retry_interval",
        "price_in",
        "price_out",
        "temperature",
        "max_tokens",
        "extra_params",
    ]

    missing = [k for k in required if k not in model]
    if missing:
        raise LLMConfigurationError(f"model_set 元素缺少字段: {missing}")

    if not isinstance(model.get("extra_params"), dict):
        raise LLMConfigurationError("model.extra_params 必须是 dict")

    if "tool_call_compat" in model and not isinstance(model.get("tool_call_compat"), bool):
        raise LLMConfigurationError("model.tool_call_compat 必须是 bool")
    if "max_context" in model and not isinstance(model.get("max_context"), int):
        raise LLMConfigurationError("model.max_context 必须是 int")

    extra_params = model.get("extra_params", {})
    if isinstance(extra_params, dict):
        if "context_reserve_ratio" in extra_params and not isinstance(extra_params.get("context_reserve_ratio"), (int, float)):
            raise LLMConfigurationError("model.extra_params.context_reserve_ratio 必须是 number")
        if "context_reserve_tokens" in extra_params and not isinstance(extra_params.get("context_reserve_tokens"), int):
            raise LLMConfigurationError("model.extra_params.context_reserve_tokens 必须是 int")

    model.setdefault("tool_call_compat", False)
    model.setdefault("max_context", 0)

    return model  # type: ignore[return-value]


def _validate_model_set(model_set: Any) -> ModelSet:
    if not isinstance(model_set, list) or not model_set:
        raise LLMConfigurationError("model_set 必须是非空 list[dict]")
    if not all(isinstance(x, dict) for x in model_set):
        raise LLMConfigurationError("model_set 必须是 list[dict]")
    return [
        _validate_model_entry(x) for x in model_set
    ]
