"""Default Chatter 工具调用控制流模块。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from src.kernel.llm import LLMPayload, ROLE, Text, ToolResult
from src.kernel.concurrency import get_watchdog

@dataclass
class ToolCallOutcome:
    """一次 call_list 处理结果。"""

    should_wait: bool = False
    should_stop: bool = False
    stop_minutes: float = 0.0
    sent_once: bool = False
    has_pending_tool_results: bool = False


async def process_tool_calls(
    *,
    stream_id: str,
    calls: list[Any],
    response: Any,
    run_tool_call: Callable[[Any, Any, Any, Any], Awaitable[tuple[bool, bool]]],
    usable_map: Any,
    trigger_msg: Any,
    pass_call_name: str,
    stop_call_name: str,
    send_text_call_name: str | None,
    break_on_send_text: bool,
) -> ToolCallOutcome:
    """处理单轮 LLM 的 tool calls 并返回控制流结果。"""
    outcome = ToolCallOutcome()
    seen_call_signatures: set[str] = set()

    for call in calls:
        get_watchdog().feed_dog(stream_id)  # 喂狗，防止工具调用过久导致 Watchdog 误判超时
        args = call.args if isinstance(call.args, dict) else {}
        dedupe_args = (
            {key: value for key, value in args.items() if key != "reason"}
            if isinstance(args, dict)
            else args
        )
        dedupe_key = _build_call_dedupe_key(call.name, dedupe_args)
        if dedupe_key in seen_call_signatures:
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value="检测到同一轮重复工具调用，已自动跳过",
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            continue
        seen_call_signatures.add(dedupe_key)

        if call.name == pass_call_name:
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value="已跳过，等待用户新消息",
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            outcome.should_wait = True
            continue

        if call.name == stop_call_name:
            outcome.stop_minutes = float(args.get("minutes", 5.0))
            response.add_payload(
                LLMPayload(
                    ROLE.TOOL_RESULT,
                    ToolResult(  # type: ignore[arg-type]
                        value=f"对话已结束，将在 {outcome.stop_minutes} 分钟后允许新对话",
                        call_id=call.id,
                        name=call.name,
                    ),
                )
            )
            outcome.should_stop = True
            continue

        appended, success = await run_tool_call(call, response, usable_map, trigger_msg)
        if appended and not call.name.startswith("action-"):
            # 仅 tool/agent 等“有信息返回、通常需要后续推理”的调用，
            # 才标记为需要继续发起下一轮 LLM 请求。
            # action 调用（如 send_text）执行后通常应等待新消息，不应立即二次请求。
            outcome.has_pending_tool_results = True

        if (
            break_on_send_text
            and send_text_call_name
            and success
            and call.name == send_text_call_name
        ):
            outcome.sent_once = True
            break

    return outcome


def _build_call_dedupe_key(call_name: str, args: Any) -> str:
    """构建 tool call 去重键。"""
    try:
        serialized_args = json.dumps(
            args,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except TypeError:
        serialized_args = str(args)
    return f"{call_name}:{serialized_args}"


def append_suspend_payload_if_action_only(
    *,
    calls: list[Any],
    response: Any,
    suspend_text: str,
    logger: Any,
) -> None:
    """当本轮全是 action 调用时，补充 SUSPEND 占位 assistant 消息。"""
    if calls and all(call.name.startswith("action-") for call in calls):
        response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
        logger.debug("已注入 SUSPEND 占位符（本轮全部为 action 调用）")
