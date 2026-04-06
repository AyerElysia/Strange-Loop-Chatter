"""Default Chatter 执行器模块。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import AsyncGenerator, TypeGuard

from src.core.components.base import Wait, Success, Failure, Stop
from src.core.models.message import Message
from src.core.models.stream import ChatStream
from src.kernel.logger import Logger
from src.kernel.llm import LLMPayload, ROLE, Text

from .config import DefaultChatterConfig
from .debug import format_prompt_for_log, log_dc_result
from .multimodal import build_multimodal_content, extract_media_from_messages
from .type_defs import DefaultChatterRuntime, LLMConversationState, LLMResponseLike
from .tool_flow import append_suspend_payload_if_action_only, process_tool_calls

# LLM 返回纯文本（非 tool call）时的最大重试次数
_MAX_PLAIN_TEXT_RETRIES = 0

# 重试时注入的提醒文本
_PLAIN_TEXT_RETRY_REMINDER = (
    "（系统提示：你刚才返回了纯文本而非工具调用。"
    "请务必通过工具调用来完成任务，不要直接输出文字回复。）"
)


class _ToolCallWorkflowPhase(str, Enum):
    """default_chatter 的 toolcall 工作流相位（简化 FSM）。

    约束目标：强制会话严格遵守
    USER → ASSISTANT(tool_calls) → TOOL_RESULT → ASSISTANT(follow-up) → USER

    - 仅在 WAIT_USER 阶段允许注入新的 USER payload
    - 仅在 MODEL_TURN/FOLLOW_UP 阶段允许向模型发起 send
    - TOOL_EXEC 阶段只执行工具并写回 TOOL_RESULT，不发起新的 USER
    """

    WAIT_USER = "wait_user"
    MODEL_TURN = "model_turn"
    TOOL_EXEC = "tool_exec"
    FOLLOW_UP = "follow_up"


@dataclass
class _EnhancedWorkflowRuntime:
    """enhanced 模式运行时状态。"""

    response: LLMConversationState
    phase: _ToolCallWorkflowPhase
    history_merged: bool
    unreads: list[Message]
    cross_round_seen_signatures: set[str]
    unread_msgs_to_flush: list[Message]
    plain_text_retry_count: int = 0
    last_continuous_memory_updated_at: str = ""

    def has_tool_result_tail(self) -> bool:
        """当前上下文尾部是否为 TOOL_RESULT。"""
        payloads = getattr(self.response, "payloads", None)
        return bool(payloads and payloads[-1].role == ROLE.TOOL_RESULT)


def _is_response_like(response: LLMConversationState) -> TypeGuard[LLMResponseLike]:
    """判断当前会话状态是否已经进入响应阶段。"""
    return hasattr(response, "call_list") and hasattr(response, "message")


def _require_response(response: LLMConversationState) -> LLMResponseLike:
    """将会话状态收窄为已完成的 LLM 响应。"""
    if _is_response_like(response):
        return response
    raise TypeError("当前会话状态尚未进入响应阶段")


def _get_multimodal_settings(chatter: DefaultChatterRuntime) -> tuple[bool, int, int]:
    """读取 default_chatter 的原生多模态配置。"""
    plugin = getattr(chatter, "plugin", None)
    config = getattr(plugin, "config", None)
    if isinstance(config, DefaultChatterConfig):
        enable_video = bool(getattr(config.plugin, "native_video_multimodal", True))
        return (
            config.plugin.native_multimodal,
            max(0, config.plugin.max_images_per_payload),
            max(0, getattr(config.plugin, "max_videos_per_payload", 1)) if enable_video else 0,
        )
    return False, 0, 0


def _log_response_debug(chatter: DefaultChatterRuntime, response: LLMConversationState) -> None:
    """按配置输出 LLM 调试摘要。"""
    plugin = getattr(chatter, "plugin", None)
    config = getattr(plugin, "config", None)
    if isinstance(config, DefaultChatterConfig):
        log_dc_result(response, config)
    else:
        return


def _log_prompt_debug(
    chatter: DefaultChatterRuntime,
    response: LLMConversationState,
    logger: Logger,
) -> None:
    """按配置输出完整提示词上下文。"""
    plugin = getattr(chatter, "plugin", None)
    config = getattr(plugin, "config", None)
    if not isinstance(config, DefaultChatterConfig):
        return
    plugin_cfg = getattr(config, "plugin", None)
    debug_cfg = getattr(plugin_cfg, "debug", None)
    if debug_cfg is None or not getattr(debug_cfg, "show_prompt", False):
        return

    prompt_text = format_prompt_for_log(response)
    logger.print_panel(
        prompt_text,
        title=f"DefaultChatter 提示词 (stream={getattr(chatter, 'stream_id', '')[:8]})",
        border_style="cyan",
    )


def _build_multimodal_payload(
    prompt_text: str,
    unread_msgs: list[Message],
    *,
    history_msgs: list[Message] | None = None,
    max_images: int = 0,
    max_videos: int = 0,
    include_history_media: bool = False,
) -> list[object]:
    """将文本提示与媒体组装为原生多模态 content。"""
    unread_media = extract_media_from_messages(
        unread_msgs,
        max_images=max_images,
        max_videos=max_videos,
    )
    content = build_multimodal_content(prompt_text, unread_media)

    unread_image_count = sum(1 for item in unread_media if item.media_type in ("image", "emoji"))
    unread_video_count = sum(1 for item in unread_media if item.media_type == "video")

    if include_history_media:
        remaining_images = max(0, max_images - unread_image_count)
        remaining_videos = max(0, max_videos - unread_video_count)
        if history_msgs and (remaining_images > 0 or remaining_videos > 0):
            history_media = extract_media_from_messages(
                list(reversed(history_msgs)),
                max_images=remaining_images,
                max_videos=remaining_videos,
            )
            if history_media:
                content.extend(build_multimodal_content("[历史媒体参考]", history_media))

    return content


def _transition(
    *,
    rt: _EnhancedWorkflowRuntime,
    to_phase: _ToolCallWorkflowPhase,
    logger: Logger,
    reason: str,
) -> None:
    """执行状态机相位切换，并记录调试日志。"""
    if rt.phase == to_phase:
        return
    debug_fn = getattr(logger, "debug", None)
    if callable(debug_fn):
        debug_fn(f"[FSM] {rt.phase.value} -> {to_phase.value}: {reason}")
    rt.phase = to_phase


def _append_suspend_if_tool_result_tail(
    response: LLMConversationState,
    suspend_text: str,
    logger: Logger,
) -> None:
    """若当前尾部是 TOOL_RESULT，补一条 ASSISTANT 占位，阻止无消息 follow-up 空转。"""
    payloads = getattr(response, "payloads", None)
    if payloads and payloads[-1].role == ROLE.TOOL_RESULT:
        response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(suspend_text)))
        logger.debug("已注入 SUSPEND 占位符（pass_and_wait 优先结束本轮）")


def _inject_runtime_assistant_payloads(
    rt: _EnhancedWorkflowRuntime,
    chat_stream: ChatStream,
    logger: Logger,
) -> None:
    """将外部插件注入的运行时 assistant 文本写入当前上下文。"""
    payloads = getattr(rt.response, "payloads", None)
    if not isinstance(payloads, list) or not payloads:
        return

    # 对话不能以 assistant 开始，必须已有 user 才可注入。
    has_user = any(payload.role == ROLE.USER for payload in payloads)
    if not has_user:
        return

    try:
        from default_chatter import plugin as default_chatter_plugin_module

        consume_runtime_assistant_injections = getattr(
            default_chatter_plugin_module,
            "consume_runtime_assistant_injections",
            None,
        )
        if not callable(consume_runtime_assistant_injections):
            return

        texts = consume_runtime_assistant_injections(
            chat_stream.stream_id,
            max_items=8,
        )
        if not texts:
            return

        injected_count = 0
        for text in texts:
            normalized = str(text or "").strip()
            if not normalized:
                continue
            rt.response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(normalized)))
            injected_count += 1

        if injected_count > 0:
            logger.info(f"[payload] 已注入运行时 assistant 上下文 {injected_count} 条")
    except Exception as exc:
        logger.debug(f"注入运行时 assistant 上下文失败：{exc}")


def _drop_oldest_conversation_payloads(
    response: LLMConversationState,
    max_drop_count: int,
) -> int:
    """按会话顺序裁剪最旧对话 payload，保留 SYSTEM/TOOL 前缀。"""
    if max_drop_count <= 0:
        return 0

    payloads = getattr(response, "payloads", None)
    if not isinstance(payloads, list) or not payloads:
        return 0

    pinned_roles = {ROLE.SYSTEM, ROLE.TOOL}
    pinned = [p for p in payloads if p.role in pinned_roles]
    convo = [p for p in payloads if p.role not in pinned_roles]
    if not convo:
        return 0

    groups: list[list[LLMPayload]] = []
    current: list[LLMPayload] = []
    for payload in convo:
        if payload.role == ROLE.USER:
            if current:
                groups.append(current)
            current = [payload]
            continue
        if not current:
            groups.append([payload])
            continue
        current.append(payload)
    if current:
        groups.append(current)

    # 至少保留一段最近上下文（按 max_drop_count 作为保留基线），
    # 避免裁剪后会话内容被清空。
    removable = max(0, len(convo) - max_drop_count)
    if removable <= 0:
        return 0

    target_drop = min(max_drop_count, removable)
    dropped = 0
    while groups and dropped < target_drop:
        dropped += len(groups.pop(0))

    remaining = [payload for group in groups for payload in group]
    while remaining and remaining[0].role != ROLE.USER:
        remaining.pop(0)
        dropped += 1

    response.payloads = pinned + remaining
    return dropped


def _trim_payloads_if_continuous_memory_updated(
    chatter: DefaultChatterRuntime,
    chat_stream: ChatStream,
    rt: _EnhancedWorkflowRuntime,
    logger: Logger,
) -> None:
    """连续记忆更新后，裁剪当前请求中的最旧对话 payload。"""
    try:
        # 避免干扰 tool 调用闭环阶段。
        if rt.phase != _ToolCallWorkflowPhase.WAIT_USER:
            return

        from src.app.plugin_system.api.service_api import get_service

        service = get_service("diary_plugin:service:diary_service")
        if service is None or not hasattr(service, "get_continuous_memory_summary"):
            return

        summary = service.get_continuous_memory_summary(  # type: ignore[attr-defined]
            chat_stream.stream_id,
            chat_stream.chat_type,
        )
        updated_at = str(summary.get("updated_at", "") or "").strip()
        if not updated_at or updated_at == rt.last_continuous_memory_updated_at:
            return
        rt.last_continuous_memory_updated_at = updated_at

        trim_count = 0
        cfg = getattr(getattr(service, "plugin", None), "config", None)
        if cfg is not None:
            cm_cfg = getattr(cfg, "continuous_memory", None)
            trim_count = int(
                getattr(cm_cfg, "payload_history_trim_count_on_update", 0) or 0
            )
        if trim_count <= 0:
            return

        dropped = _drop_oldest_conversation_payloads(rt.response, trim_count)
        if dropped > 0:
            # 下一次采纳用户消息时重新带一次历史块，避免“历史区看起来空了”。
            rt.history_merged = False
            logger.info(
                f"[cache] 检测到连续记忆更新，已裁剪最旧 payloads：{dropped} 条"
            )
    except Exception as exc:
        logger.debug(f"连续记忆更新触发 payloads 裁剪失败：{exc}")


async def run_enhanced(
    chatter: DefaultChatterRuntime,
    chat_stream: ChatStream,
    logger: Logger,
    pass_call_name: str,
    send_text_call_name: str,
    suspend_text: str,
) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
    """enhanced 模式执行流程。"""
    try:
        request = chatter.create_request("actor", with_reminder="actor")
    except (ValueError, KeyError) as error:
        logger.error(f"获取模型配置失败: {error}")
        yield Failure(f"模型配置错误: {error}")
        return

    system_prompt_text = await chatter._build_system_prompt(chat_stream)
    request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt_text)))

    history_text = chatter._build_enhanced_history_text(chat_stream)
    native_multimodal, max_images, max_videos = _get_multimodal_settings(chatter)
    usable_map = await chatter.inject_usables(request)

    rt = _EnhancedWorkflowRuntime(
        response=request,
        phase=_ToolCallWorkflowPhase.FOLLOW_UP if request.payloads and request.payloads[-1].role == ROLE.TOOL_RESULT else _ToolCallWorkflowPhase.WAIT_USER,
        history_merged=False,
        unreads=[],
        cross_round_seen_signatures=set(),
        unread_msgs_to_flush=[],
    )

    while True:
        _, unread_msgs = await chatter.fetch_unreads()
        _trim_payloads_if_continuous_memory_updated(chatter, chat_stream, rt, logger)

        # 安全兜底：若上下文尾部为 TOOL_RESULT，必须进入 FOLLOW_UP
        if rt.phase == _ToolCallWorkflowPhase.WAIT_USER and rt.has_tool_result_tail():
            _transition(
                rt=rt,
                to_phase=_ToolCallWorkflowPhase.FOLLOW_UP,
                logger=logger,
                reason="context tail is TOOL_RESULT; must follow-up before new USER",
            )

        # 在 WAIT_USER 且链路闭合时，消费外部运行时 assistant 注入。
        if rt.phase == _ToolCallWorkflowPhase.WAIT_USER and not rt.has_tool_result_tail():
            _inject_runtime_assistant_payloads(rt, chat_stream, logger)

        # FSM 驱动：每次循环只推进一个相位（或 yield）
        if rt.phase == _ToolCallWorkflowPhase.WAIT_USER:
            if not unread_msgs:
                yield Wait()
                continue

            # 仅在采纳新未读消息时清空跨轮去重状态；FOLLOW_UP 阶段不应清空。
            rt.cross_round_seen_signatures.clear()
            rt.plain_text_retry_count = 0
            rt.unreads = unread_msgs

            unread_lines = "\n".join(
                chatter.format_message_line(msg) for msg in unread_msgs
            )
            unread_user_prompt = await chatter._build_user_prompt(
                chat_stream,
                history_text=history_text if not rt.history_merged else "",
                unread_lines=unread_lines,
                extra=chatter._build_negative_behaviors_extra(),
            )

            if native_multimodal:
                unread_user_content = _build_multimodal_payload(
                    unread_user_prompt,
                    unread_msgs,
                    history_msgs=chat_stream.context.history_messages,
                    max_images=max_images,
                    max_videos=max_videos,
                    include_history_media=not rt.history_merged,
                )
            else:
                unread_user_content = Text(unread_user_prompt)

            decision = await chatter.sub_agent(
                unread_lines,
                unread_msgs,
                chat_stream,
            )
            logger.info(
                f"Sub-agent 决策: {decision['reason']} (响应: {decision['should_respond']})"
            )

            if not decision["should_respond"]:
                logger.info("Sub-agent 决定不响应，继续等待...")
                yield Wait()
                continue

            chatter._upsert_pending_unread_payload(
                response=rt.response,
                formatted_content=unread_user_content,
            )
            rt.history_merged = True
            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.MODEL_TURN, logger=logger, reason="accepted unread batch")

            # MODEL_TURN 阶段发送后才 flush 本轮采纳的 unread
            rt.unread_msgs_to_flush = unread_msgs
            continue

        if rt.phase in (_ToolCallWorkflowPhase.MODEL_TURN, _ToolCallWorkflowPhase.FOLLOW_UP):
            # FOLLOW_UP 阶段严禁 flush 新未读；MODEL_TURN 才 flush 本轮采纳的 unread。
            try:
                _log_prompt_debug(chatter, rt.response, logger)
                rt.response = await rt.response.send(stream=False)
                await rt.response
                _log_response_debug(chatter, rt.response)
                if rt.phase == _ToolCallWorkflowPhase.MODEL_TURN:
                    if rt.unread_msgs_to_flush:
                        await chatter.flush_unreads(rt.unread_msgs_to_flush)
                    rt.unread_msgs_to_flush = []
            except Exception as error:
                logger.error(f"LLM 请求失败: {error}", exc_info=True)
                yield Failure("LLM 请求失败", error)
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="request failed")
                continue

            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.TOOL_EXEC, logger=logger, reason="model responded")
            continue

        if rt.phase == _ToolCallWorkflowPhase.TOOL_EXEC:
            llm_response = _require_response(rt.response)

            if not llm_response.call_list:
                if llm_response.message and llm_response.message.strip():
                    logger.warning(
                        f"LLM 返回了纯文本而非 tool call: "
                        f"{llm_response.message[:100]}"
                    )
                    yield Stop(0)
                    return
                yield Wait()
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="no call_list")
                continue

            logger.info(f"本轮调用列表：{[call.name for call in llm_response.call_list or []]}")
            for call in llm_response.call_list or []:
                args = call.args if isinstance(call.args, dict) else {}
                reason = args.pop("reason", "未提供原因")
                logger.info(f"LLM 调用 {call.name}，原因: {reason}，参数: {args}")

            call_outcome = await process_tool_calls(
                stream_id=chat_stream.stream_id,
                calls=llm_response.call_list or [],
                response=llm_response,
                run_tool_call=chatter.run_tool_call,
                usable_map=usable_map,
                trigger_msg=rt.unreads[-1] if rt.unreads else None,
                pass_call_name=pass_call_name,
                send_text_call_name=send_text_call_name,
                break_on_send_text=False,
                cross_round_seen_signatures=rt.cross_round_seen_signatures,
            )

            # pass_and_wait 具有最高优先级：即使同轮有非 action 工具结果，也直接等待用户。
            # 为避免下一轮被“尾部 TOOL_RESULT”强制 FOLLOW_UP，这里补一个 ASSISTANT 占位。
            if call_outcome.should_wait:
                _append_suspend_if_tool_result_tail(rt.response, suspend_text, logger)
                yield Wait()
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="pass_and_wait priority")
                continue

            if call_outcome.has_pending_tool_results:
                _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.FOLLOW_UP, logger=logger, reason="pending tool results")
                continue

            append_suspend_payload_if_action_only(
                calls=llm_response.call_list or [],
                response=llm_response,
                suspend_text=suspend_text,
                logger=logger,
            )

            # 工具链已闭合，可以进入等待或接受新 user。
            _transition(rt=rt, to_phase=_ToolCallWorkflowPhase.WAIT_USER, logger=logger, reason="tool exec done")
            continue


async def run_classical(
    chatter: DefaultChatterRuntime,
    chat_stream: ChatStream,
    logger: Logger,
    pass_call_name: str,
    send_text_call_name: str,
    suspend_text: str,
) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
    """classical 模式执行流程。"""
    try:
        base_request = chatter.create_request("actor", with_reminder="actor")
    except (ValueError, KeyError) as error:
        logger.error(f"获取模型配置失败: {error}")
        yield Failure(f"模型配置错误: {error}")
        return

    usable_map = await chatter.inject_usables(base_request)
    native_multimodal, max_images, max_videos = _get_multimodal_settings(chatter)
    history_media_injected = False

    while True:
        _, unread_msgs = await chatter.fetch_unreads()
        unreads = unread_msgs

        if not unread_msgs:
            yield Wait()
            continue

        classical_user_text = await chatter._build_classical_user_text(
            chat_stream,
            unread_msgs,
        )
        unread_lines = "\n".join(
            chatter.format_message_line(msg) for msg in unread_msgs
        )
        decision = await chatter.sub_agent(
            unread_lines,
            unread_msgs,
            chat_stream,
        )
        logger.info(
            f"Sub-agent 决策: {decision['reason']} (响应: {decision['should_respond']})"
        )

        if not decision["should_respond"]:
            logger.info("Sub-agent 决定不响应，继续等待...")
            yield Wait()
            continue

        request = chatter.create_request("actor", with_reminder="actor")
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text(await chatter._build_system_prompt(chat_stream)),
            )
        )
        if native_multimodal:
            user_content = _build_multimodal_payload(
                classical_user_text,
                unread_msgs,
                history_msgs=chat_stream.context.history_messages,
                max_images=max_images,
                max_videos=max_videos,
                include_history_media=not history_media_injected,
            )
            request.add_payload(LLMPayload(ROLE.USER, user_content))
            history_media_injected = True
        else:
            request.add_payload(LLMPayload(ROLE.USER, Text(classical_user_text)))
        if usable_map.get_all():
            request.add_payload(LLMPayload(ROLE.TOOL, usable_map.get_all()))  # type: ignore[arg-type]

        response = request
        cross_round_seen_signatures: set[str] = set()
        has_pending_tool_results = False
        plain_text_retry_count = 0

        while True:
            try:
                _log_prompt_debug(chatter, response, logger)
                response = await response.send(stream=False)
                await response
                _log_response_debug(chatter, response)
            except Exception as error:
                logger.error(f"LLM 请求失败: {error}", exc_info=True)
                yield Failure("LLM 请求失败", error)
                break

            if not response.call_list:
                if response.message and response.message.strip():
                    logger.warning(
                        f"LLM 返回了纯文本而非 tool call: "
                        f"{response.message[:100]}"
                    )
                await chatter.flush_unreads(unread_msgs)
                yield Stop(0)
                return

            for call in response.call_list or []:
                args = call.args if isinstance(call.args, dict) else {}
                reason = args.pop("reason", "未提供原因")
                logger.info(f"LLM 调用 {call.name}，原因: {reason}，参数: {args}")

            call_outcome = await process_tool_calls(
                stream_id=chat_stream.stream_id,
                calls=response.call_list or [],
                response=response,
                run_tool_call=chatter.run_tool_call,
                usable_map=usable_map,
                trigger_msg=unreads[-1] if unreads else None,
                pass_call_name=pass_call_name,
                send_text_call_name=send_text_call_name,
                break_on_send_text=True,
                cross_round_seen_signatures=cross_round_seen_signatures,
            )
            has_pending_tool_results = call_outcome.has_pending_tool_results
            if not call_outcome.has_pending_tool_results:
                append_suspend_payload_if_action_only(
                    calls=response.call_list or [],
                    response=response,
                    suspend_text=suspend_text,
                    logger=logger,
                )

            if call_outcome.sent_once:
                logger.info("classical 模式已发送一次消息，强制结束当前对话")
                await chatter.flush_unreads(unread_msgs)
                yield Stop(0)
                return

            if call_outcome.should_wait:
                _append_suspend_if_tool_result_tail(response, suspend_text, logger)
                await chatter.flush_unreads(unread_msgs)
                yield Wait()
                break

            # 未要求等待时，若存在 pending 工具结果则继续 follow-up。
            if has_pending_tool_results:
                continue
