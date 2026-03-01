"""Default Chatter 执行器模块。"""

from __future__ import annotations

from typing import Any, AsyncGenerator

from src.core.components.base import Wait, Success, Failure, Stop
from src.core.models.stream import ChatStream
from src.kernel.llm import LLMPayload, ROLE, Text

from .tool_flow import append_suspend_payload_if_action_only, process_tool_calls


async def run_enhanced(
    chatter: Any,
    chat_stream: ChatStream,
    logger: Any,
    pass_call_name: str,
    stop_call_name: str,
    send_text_call_name: str,
    suspend_text: str,
) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
    """enhanced 模式执行流程。"""
    try:
        request = chatter.create_request("actor")
    except (ValueError, KeyError) as error:
        logger.error(f"获取模型配置失败: {error}")
        yield Failure(f"模型配置错误: {error}")
        return

    system_prompt_text = await chatter._build_system_prompt(chat_stream)
    request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt_text)))

    history_text = chatter._build_enhanced_history_text(chat_stream)
    usable_map = await chatter.inject_usables(request)

    response = request
    history_merged = False
    has_pending_tool_results = False
    unreads: list[Any] = []

    while True:
        _, unread_msgs = await chatter.fetch_unreads()
        # 仅在有新消息时更新 unreads，保证 has_pending_tool_results 续轮时
        # trigger_msg 仍指向上一轮触发消息，而非因列表清空变为 None。
        if unread_msgs:
            unreads = unread_msgs

        if unread_msgs:
            unread_lines = "\n".join(
                chatter.format_message_line(msg) for msg in unread_msgs
            )
            unread_user_prompt = await chatter._build_user_prompt(
                chat_stream,
                history_text=history_text if not history_merged else "",
                unread_lines=unread_lines,
                extra=chatter._build_negative_behaviors_extra(),
            )
            history_merged = True

            decision = await chatter.sub_agent(
                unread_lines,
                unread_msgs,
                chat_stream,
            )
            logger.info(
                f"Sub-agent 决策: {decision['reason']} (响应: {decision['should_respond']})"
            )

            chatter._upsert_pending_unread_payload(
                response=response,
                formatted_text=unread_user_prompt,
            )

            if not decision["should_respond"]:
                logger.info("Sub-agent 决定不响应，继续等待...")
                yield Wait()
                continue
        elif not has_pending_tool_results:
            yield Wait()
            continue

        has_pending_tool_results = False

        try:
            response = await response.send(stream=False)
            await response
            await chatter.flush_unreads(unread_msgs)
        except Exception as error:
            logger.error(f"LLM 请求失败: {error}", exc_info=True)
            yield Failure("LLM 请求失败", error)
            continue

        if not response.call_list:
            if response.message and response.message.strip():
                logger.warning(
                    "LLM 返回了纯文本而非 tool call: "
                    f"{response.message[:100]}"
                )
                yield Stop(0)
                return

        logger.info(f"本轮调用列表：{[call.name for call in response.call_list or []]}")
        
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
            stop_call_name=stop_call_name,
            send_text_call_name=send_text_call_name,
            break_on_send_text=False,
        )
        has_pending_tool_results = call_outcome.has_pending_tool_results

        if not call_outcome.has_pending_tool_results:
            append_suspend_payload_if_action_only(
                calls=response.call_list or [],
                response=response,
                suspend_text=suspend_text,
                logger=logger,
            )

        if call_outcome.should_stop:
            logger.info(f"对话已结束，冷却 {call_outcome.stop_minutes} 分钟")
            yield Stop(call_outcome.stop_minutes * 60)
            return

        if call_outcome.should_wait:
            has_pending_tool_results = False
            yield Wait()
            continue


async def run_classical(
    chatter: Any,
    chat_stream: ChatStream,
    logger: Any,
    pass_call_name: str,
    stop_call_name: str,
    send_text_call_name: str,
    suspend_text: str,
) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
    """classical 模式执行流程。"""
    try:
        base_request = chatter.create_request("actor")
    except (ValueError, KeyError) as error:
        logger.error(f"获取模型配置失败: {error}")
        yield Failure(f"模型配置错误: {error}")
        return

    usable_map = await chatter.inject_usables(base_request)

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

        request = chatter.create_request("actor")
        request.add_payload(
            LLMPayload(
                ROLE.SYSTEM,
                Text(await chatter._build_system_prompt(chat_stream)),
            )
        )
        request.add_payload(LLMPayload(ROLE.USER, Text(classical_user_text)))
        if usable_map.get_all():
            request.add_payload(LLMPayload(ROLE.TOOL, usable_map.get_all()))  # type: ignore[arg-type]

        response = request

        while True:
            try:
                response = await response.send(stream=False)
                await response
            except Exception as error:
                logger.error(f"LLM 请求失败: {error}", exc_info=True)
                yield Failure("LLM 请求失败", error)
                break

            if not response.call_list:
                if response.message and response.message.strip():
                    logger.warning(
                        "LLM 返回了纯文本而非 tool call: "
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
                stop_call_name=stop_call_name,
                send_text_call_name=send_text_call_name,
                break_on_send_text=True,
            )
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

            if call_outcome.should_stop:
                logger.info(f"对话已结束，冷却 {call_outcome.stop_minutes} 分钟")
                await chatter.flush_unreads(unread_msgs)
                yield Stop(call_outcome.stop_minutes * 60)
                return

            if call_outcome.should_wait:
                await chatter.flush_unreads(unread_msgs)
                yield Wait()
                break
