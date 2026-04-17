"""LifeChatter — 生命中枢统一对话器。

直接访问 life_engine 内部状态（event_history, inner_state），
取代 DFC + bridge 的间接架构。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, AsyncGenerator

from src.core.components.types import ChatType
from src.core.components.base.chatter import BaseChatter, Wait, Success, Failure, Stop
from src.core.components.base.action import BaseAction
from src.core.models.message import Message, MessageType
from src.kernel.llm import LLMPayload, ROLE, Text, ToolResult
from src.kernel.logger import get_logger, COLOR

if TYPE_CHECKING:
    from src.core.components.base.plugin import BasePlugin
    from src.core.models.stream import ChatStream
    from src.kernel.llm import LLMRequest, ToolRegistry, ToolCall
    from ..service.core import LifeEngineService
    from ..service.event_builder import LifeEngineEvent

logger = get_logger("life_chatter", display="生命对话器", color=COLOR.MAGENTA)

# ── 控制流常量 ────────────────────────────────────────────────
_PASS_AND_WAIT = "action-life_pass_and_wait"
_SEND_TEXT = "action-life_send_text"
_SUSPEND_TEXT = "__SUSPEND__"
_REASON_LEAK_PATTERN = re.compile(
    r'[,，]?\s*["\']?reason["\']?\s*[:：]',
    re.IGNORECASE,
)

# ── FSM 相位 ──────────────────────────────────────────────────

class _Phase(str, Enum):
    WAIT_USER = "wait_user"
    MODEL_TURN = "model_turn"
    TOOL_EXEC = "tool_exec"
    FOLLOW_UP = "follow_up"


@dataclass
class _WorkflowRuntime:
    """enhanced 模式运行时状态。"""
    response: Any  # LLMRequest | LLMResponse
    phase: _Phase
    history_merged: bool
    unreads: list[Message]
    cross_round_seen_signatures: set[str]
    unread_msgs_to_flush: list[Message]
    plain_text_retry_count: int = 0


# ── Actions ───────────────────────────────────────────────────

class LifeSendTextAction(BaseAction):
    """发送文本消息（life_chatter 专用）。"""

    action_name = "life_send_text"
    action_description = (
        "发送文本消息给用户。"
        "content 只能是字符串或字符串数组（分段发送），例如"
        "\"content\": [\"你好\", \"请问你是谁？\", \"找我有什么事吗？\"]。"
        "content 中只能包含要发给用户的纯文本正文。"
        "严禁把 reason/thought/expected_reaction 等元信息写进 content。"
        "分段消息会按顺序发送，并自动模拟段间打字延迟。"
        "私聊场景下 reply_to 默认不要使用，除非确实需要引用某条历史消息来避免歧义。"
    )

    chatter_allow: list[str] = ["life_chatter"]

    # ── segment helpers ─────────────────────────────────────

    @staticmethod
    def _to_non_empty_segments(raw: list[object]) -> list[str]:
        return [s.strip() for s in raw if isinstance(s, str) and s.strip()]

    @staticmethod
    def _extract_leading_json_array(text: str) -> str | None:
        if not text.startswith("["):
            return None
        depth = 0
        in_string = False
        escaped = False
        for index, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                    continue
                if char == "\\":
                    escaped = True
                    continue
                if char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
                continue
            if char == "[":
                depth += 1
                continue
            if char == "]":
                depth -= 1
                if depth == 0:
                    return text[: index + 1]
        return None

    @classmethod
    def _try_parse_segments_from_text(cls, text: str) -> list[str] | None:
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return cls._to_non_empty_segments(parsed)
        if isinstance(parsed, dict):
            content = parsed.get("content")
            if isinstance(content, list):
                return cls._to_non_empty_segments(content)
            if isinstance(content, str):
                stripped = content.strip()
                return [stripped] if stripped else []
        leading_array = cls._extract_leading_json_array(text)
        if leading_array:
            try:
                parsed_array = json.loads(leading_array)
                if isinstance(parsed_array, list):
                    return cls._to_non_empty_segments(parsed_array)
            except Exception:
                return None
        return None

    @classmethod
    def _normalize_content_segments(cls, content: str | list[str]) -> list[str]:
        if isinstance(content, list):
            return cls._to_non_empty_segments(content)
        if not isinstance(content, str):
            return []
        stripped = content.strip()
        if not stripped:
            return []
        first_block = re.split(r"<br\s*/?>", stripped, maxsplit=1, flags=re.IGNORECASE)[0].strip()
        if not first_block:
            return []
        parsed_segments = cls._try_parse_segments_from_text(first_block)
        if parsed_segments is not None:
            return parsed_segments
        return [first_block]

    @staticmethod
    def _sanitize_segment(content: str) -> str:
        if not content:
            return ""
        return _REASON_LEAK_PATTERN.split(content, maxsplit=1)[0].strip()

    @staticmethod
    def _calculate_typing_delay(content: str) -> float:
        chars_per_sec = 15.0
        min_delay = 0.8
        max_delay = 4.0
        base_delay = len(content) / chars_per_sec
        return max(min_delay, min(base_delay, max_delay))

    async def _send_one_segment(
        self,
        content: str,
        reply_to: str | None = None,
    ) -> bool:
        if reply_to:
            target_stream_id = self.chat_stream.stream_id
            platform = self.chat_stream.platform
            chat_type = self.chat_stream.chat_type
            context = self.chat_stream.context

            from src.core.managers.adapter_manager import get_adapter_manager
            from uuid import uuid4

            bot_info = await get_adapter_manager().get_bot_info_by_platform(platform)

            target_user_id = None
            target_group_id = None
            target_user_name = None
            target_group_name = None

            def _get_last_context_message() -> Message | None:
                if context.unread_messages:
                    return context.unread_messages[-1]
                if context.history_messages:
                    return context.history_messages[-1]
                return context.current_message

            last_msg = _get_last_context_message()

            if chat_type == "group":
                if last_msg:
                    target_group_id = last_msg.extra.get("group_id")
                    target_group_name = last_msg.extra.get("group_name")
            else:
                target_user_id, target_user_name = await self._resolve_private_target_from_context(
                    context,
                    last_msg,
                )

            extra: dict[str, str] = {}
            if target_user_id:
                extra["target_user_id"] = target_user_id
            if target_user_name:
                extra["target_user_name"] = target_user_name
            if target_group_id:
                extra["target_group_id"] = target_group_id
            if target_group_name:
                extra["target_group_name"] = target_group_name

            message = Message(
                message_id=f"action_{self.action_name}_{uuid4().hex}",
                content=content,
                processed_plain_text=content,
                message_type=MessageType.TEXT,
                sender_id=bot_info.get("bot_id", "") if bot_info else "",
                sender_name=bot_info.get("bot_nickname", "Bot") if bot_info else "Bot",
                platform=platform,
                chat_type=chat_type,
                stream_id=target_stream_id,
                reply_to=reply_to,
            )
            message.extra.update(extra)

            from src.core.transport.message_send import get_message_sender

            sender = get_message_sender()
            return await sender.send_message(message)

        return await self._send_to_stream(content)

    async def execute(
        self,
        content: Annotated[
            str | list[str],
            "要发送给用户的纯文本内容。仅允许 string 或 string[]；"
            "禁止把 reason/thought 等元信息写进 content。",
        ],
        reply_to: Annotated[
            str | None,
            "可选，要引用回复的目标消息 ID。私聊默认留空。",
        ] = None,
    ) -> tuple[bool, str]:
        segments = self._normalize_content_segments(content)
        cleaned_segments = [self._sanitize_segment(s) for s in segments]
        cleaned_segments = [s for s in cleaned_segments if s]

        if not cleaned_segments:
            return True, "内容为空，跳过发送"

        sent_count = 0
        for index, segment in enumerate(cleaned_segments):
            if index > 0:
                delay = self._calculate_typing_delay(segment)
                if delay > 0:
                    await asyncio.sleep(delay)

            segment_reply_to = reply_to if index == 0 else None
            success = await self._send_one_segment(segment, segment_reply_to)
            if not success:
                return False, f"第{index + 1}条消息发送失败"
            sent_count += 1

        preview = cleaned_segments[0][:80] if cleaned_segments else ""
        return True, f"已发送{sent_count}条消息: {preview}"


class LifePassAndWaitAction(BaseAction):
    """跳过本次动作，等待新消息（life_chatter 专用）。"""

    action_name = "life_pass_and_wait"
    action_description = (
        "跳过本次动作，不进行任何操作，但保持对话继续，等待用户新消息。"
        "若当前不需要回复，就使用本工具等待用户的下一条消息。"
    )

    chatter_allow: list[str] = ["life_chatter"]

    async def execute(self) -> tuple[bool, str]:
        return True, "已跳过，等待新消息"


# ── LifeChatter ───────────────────────────────────────────────

class LifeChatter(BaseChatter):
    """生命中枢统一对话器 — 直接访问内在状态。"""

    chatter_name: str = "life_chatter"
    chatter_description: str = "生命中枢统一对话器 — 直接访问内在状态"
    associated_platforms: list[str] = []
    chat_type: ChatType = ChatType.ALL
    dependencies: list[str] = []

    # ── helpers ──────────────────────────────────────────────

    def _get_life_service(self) -> LifeEngineService | None:
        """获取 life_engine 服务实例。"""
        service = getattr(self.plugin, "_service", None)
        if service is not None:
            return service
        # Fallback: 通过 service 属性
        service_prop = getattr(self.plugin, "service", None)
        if service_prop is not None:
            return service_prop
        return None

    def _get_config(self) -> Any:
        """获取 LifeEngineConfig。"""
        return getattr(self.plugin, "config", None)

    def _get_max_rounds(self) -> int:
        """获取单轮最大工具调用轮数。"""
        cfg = self._get_config()
        if cfg is None:
            return 5
        chatter_cfg = getattr(cfg, "chatter", None)
        if chatter_cfg is not None:
            return int(getattr(chatter_cfg, "max_rounds_per_chat", 5))
        return 5

    # ── system prompt ────────────────────────────────────────

    def _build_chat_system_prompt(
        self,
        chat_stream: ChatStream,
        service: LifeEngineService | None,
    ) -> str:
        """构建 100% 静态可缓存系统提示词。"""
        parts: list[str] = []

        # 1) SOUL.md
        soul_text = self._load_soul_md(service)
        if soul_text:
            parts.append(soul_text)

        # 2) 固定对话框架
        parts.append(self._build_fixed_chat_framework(chat_stream))

        # 3) 场景引导
        scene = self._build_scene_guide(chat_stream)
        if scene:
            parts.append(scene)

        return "\n\n".join(parts)

    def _load_soul_md(self, service: LifeEngineService | None) -> str:
        """读取 SOUL.md。"""
        cfg = self._get_config()
        workspace = ""
        if cfg is not None:
            workspace = getattr(getattr(cfg, "settings", None), "workspace_path", "")
        if not workspace and service is not None:
            workspace = getattr(service, "_workspace_path", "")
        if not workspace:
            return ""

        soul_path = Path(workspace) / "SOUL.md"
        try:
            if soul_path.exists():
                return soul_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            logger.warning(f"读取 SOUL.md 失败: {e}")
        return ""

    @staticmethod
    def _build_fixed_chat_framework(chat_stream: ChatStream) -> str:
        """固定对话框架文本（人格、安全准则、工具规则）。"""
        nickname = str(chat_stream.bot_nickname or "助手")

        return f"""# 对话框架

你正在以 **{nickname}** 的身份与用户对话。

## 行为准则
- 保持你的人设和表达风格，用符合你性格的方式回复。
- 消息遵循标准化格式，请**不要模仿其格式与用户对话**。
- 回复必须有理有据，禁止无根据地编造信息。
- 不要刨根问底，保持对话的自然流畅。

## 工具使用
- send_text: 发送文本消息。content 只能写纯文本正文，禁止塞入元信息。
- pass_and_wait: 不需要回复时使用，等待用户新消息。
- 其他 tool/agent: 查询信息或执行功能。收到结果后，继续回复或进一步调用。
- 可以一次调用多个工具组合使用。回复动作应当优先。

## 安全准则
- 保护用户隐私，不泄露个人信息。
- 不生成有害、暴力、歧视性内容。
- 遇到违规请求，以合适方式回应。

## 内在状态说明
- 你的内在状态（情绪、驱力等）会以 <inner_state> 标签呈现在用户消息中。
- 这些状态反映你当前的心理感受，请自然地融入对话，而非机械地报告。
- 近期事件会以 <recent_context> 标签呈现，帮助你了解最近发生了什么。"""

    @staticmethod
    def _build_scene_guide(chat_stream: ChatStream) -> str:
        """构建场景引导。"""
        platform = str(chat_stream.platform or "unknown")
        chat_type_str = str(chat_stream.chat_type or "unknown")
        nickname = str(chat_stream.bot_nickname or "unknown")
        bot_id = str(chat_stream.bot_id or "unknown")

        lines = [
            "## 会话场景",
            f"- 平台：{platform}",
            f"- 聊天类型：{chat_type_str}",
            f"- 当前机器人昵称：{nickname}",
            f"- 当前机器人ID：{bot_id}",
        ]

        if chat_type_str == "group":
            lines.append("- 群聊注意：保持克制简洁，避免刷屏与过度热情。只在被明确提及或话题相关时回复。")
        else:
            lines.append("- 私聊注意：自然亲近，可以更加放松。")

        return "\n".join(lines)

    # ── user prompt ──────────────────────────────────────────

    def _build_chat_user_prompt(
        self,
        chat_stream: ChatStream,
        service: LifeEngineService | None,
        unread_lines: str,
        history_text: str = "",
    ) -> str:
        """构建包含动态状态的用户提示词。"""
        parts: list[str] = []

        stream_name = str(getattr(chat_stream, "stream_name", "") or chat_stream.stream_id[:16])
        parts.append(f'你当前正在名为"{stream_name}"的对话中。')
        parts.append("消息格式说明：【时间】<群组角色> [平台ID] 昵称$群名片 [消息ID]： 消息内容\n")

        # 1) 内在状态
        inner_state_text = self._read_inner_state(service)
        if inner_state_text:
            parts.append(f"<inner_state>\n{inner_state_text}\n</inner_state>\n")

        # 2) 近期事件上下文
        recent_context = self._read_recent_events(service, chat_stream.stream_id)
        if recent_context:
            parts.append(f"<recent_context>\n{recent_context}\n</recent_context>\n")

        # 3) 聊天历史
        if history_text:
            parts.append(f"<chat_history>\n{history_text}\n</chat_history>\n")

        # 4) 新未读消息
        if unread_lines:
            parts.append(f"<new_messages>\n{unread_lines}\n</new_messages>\n")

        parts.append("---\n请基于上述信息决定接下来的动作。")
        return "\n".join(parts)

    @staticmethod
    def _read_inner_state(service: LifeEngineService | None) -> str:
        """直接读取 inner_state（neuromod）。"""
        if service is None:
            return ""
        inner_state = getattr(service, "_inner_state", None)
        if inner_state is None:
            return ""
        try:
            from datetime import datetime
            today_str = datetime.now().strftime("%Y-%m-%d")
            return inner_state.format_full_state_for_prompt(today_str)
        except Exception as e:
            logger.debug(f"读取 inner_state 失败: {e}")
            # Fallback: get_full_state dict
            try:
                state_dict = inner_state.get_full_state()
                if isinstance(state_dict, dict):
                    items = []
                    for k, v in state_dict.items():
                        items.append(f"{k}: {v}")
                    return "\n".join(items)
            except Exception:
                pass
        return ""

    @staticmethod
    def _read_recent_events(
        service: LifeEngineService | None,
        current_stream_id: str,
        max_events: int = 15,
    ) -> str:
        """从 event_history 中提取近期事件摘要。"""
        if service is None:
            return ""

        event_history: list[LifeEngineEvent] = getattr(service, "_event_history", [])
        if not event_history:
            return ""

        recent = event_history[-max_events:]
        lines: list[str] = []
        for event in recent:
            # Skip MESSAGE events from current stream (they'll be in chat_history)
            if (
                getattr(event, "stream_id", None) == current_stream_id
                and str(getattr(event, "event_type", "")).lower() in ("message",)
            ):
                continue

            event_type = str(getattr(event, "event_type", "")).upper()
            if hasattr(event.event_type, "value"):
                event_type = str(event.event_type.value).upper()

            timestamp = str(getattr(event, "timestamp", ""))
            # Extract just time portion
            time_part = timestamp
            if "T" in timestamp:
                time_part = timestamp.split("T")[-1][:8]

            content = str(getattr(event, "content", ""))
            if len(content) > 100:
                content = content[:97] + "..."

            source = str(getattr(event, "source", ""))
            sender = str(getattr(event, "sender", "") or "")

            if event_type == "MESSAGE":
                lines.append(f"[{time_part}] 消息({source}) {sender}: {content}")
            elif event_type == "HEARTBEAT":
                lines.append(f"[{time_part}] 内心独白: {content}")
            elif event_type == "TOOL_CALL":
                tool_name = str(getattr(event, "tool_name", "") or "")
                lines.append(f"[{time_part}] 工具调用: {tool_name}")
            elif event_type == "TOOL_RESULT":
                lines.append(f"[{time_part}] 工具结果: {content}")
            else:
                lines.append(f"[{time_part}] {event_type}: {content}")

        return "\n".join(lines)

    # ── sub-agent decision ───────────────────────────────────

    async def _should_respond(
        self,
        unread_lines: str,
        unread_msgs: list[Message],
        chat_stream: ChatStream,
    ) -> dict[str, Any]:
        """多层决策：是否需要响应。"""
        chat_type_str = str(chat_stream.chat_type or "").lower()

        # Layer 1: 私聊 → 始终响应
        if chat_type_str == "private":
            return {"reason": "私聊场景，直接响应", "should_respond": True}

        # Layer 2: @mention
        bot_nickname = str(chat_stream.bot_nickname or "").strip()
        bot_id = str(chat_stream.bot_id or "").strip()
        for msg in unread_msgs:
            text = str(getattr(msg, "processed_plain_text", "") or getattr(msg, "content", "") or "")
            if bot_nickname and bot_nickname in text:
                return {"reason": f"消息中提到了 {bot_nickname}", "should_respond": True}
            if bot_id and f"@{bot_id}" in text:
                return {"reason": "消息中 @提及了机器人", "should_respond": True}

        # Layer 3: 简单关键词启发
        keywords = [bot_nickname] if bot_nickname else []
        # Also check common nicknames
        for msg in unread_msgs:
            text = str(getattr(msg, "processed_plain_text", "") or getattr(msg, "content", "") or "").lower()
            for kw in keywords:
                if kw and kw.lower() in text:
                    return {"reason": f"消息中包含关键词 {kw}", "should_respond": True}

        # Layer 4: LLM sub_agent fallback
        try:
            from plugins.default_chatter.decision_agent import decide_should_respond
            result = await decide_should_respond(
                chatter=self,
                logger=logger,
                unreads_text=unread_lines,
                chat_stream=chat_stream,
            )
            return result
        except Exception as e:
            logger.warning(f"sub_agent 决策失败, 默认不响应: {e}")
            return {"reason": f"sub_agent 异常: {e}", "should_respond": False}

    # ── history builder ──────────────────────────────────────

    @staticmethod
    def _build_history_text(chat_stream: ChatStream) -> str:
        """从 chat_stream 构建历史消息文本。"""
        context = chat_stream.context
        history_msgs = list(context.history_messages) if context.history_messages else []
        if not history_msgs:
            return ""

        lines = [BaseChatter.format_message_line(msg) for msg in history_msgs[-30:]]
        return "\n".join(lines)

    # ── FSM helpers ──────────────────────────────────────────

    @staticmethod
    def _transition(rt: _WorkflowRuntime, to_phase: _Phase, reason: str) -> None:
        if rt.phase == to_phase:
            return
        logger.debug(f"[FSM] {rt.phase.value} -> {to_phase.value}: {reason}")
        rt.phase = to_phase

    @staticmethod
    def _upsert_pending_unread_payload(
        response: Any,
        formatted_content: object,
    ) -> None:
        """合并未读消息到最后一个 USER payload。"""
        if isinstance(formatted_content, list):
            new_content = list(formatted_content)
        elif isinstance(formatted_content, Text):
            new_content = [formatted_content]
        else:
            new_content = [Text(str(formatted_content))]

        if response.payloads:
            last_payload = response.payloads[-1]
            if last_payload.role == ROLE.USER:
                last_payload.content.extend(new_content)
                return

        payload_content = new_content[0] if len(new_content) == 1 else new_content
        response.add_payload(LLMPayload(ROLE.USER, payload_content))

    @staticmethod
    def _has_tool_result_tail(response: Any) -> bool:
        payloads = getattr(response, "payloads", None)
        return bool(payloads and payloads[-1].role == ROLE.TOOL_RESULT)

    # ── main execute ─────────────────────────────────────────

    async def execute(self) -> AsyncGenerator[Wait | Success | Failure | Stop, None]:
        """执行聊天器的主要逻辑。"""
        from src.core.managers.stream_manager import get_stream_manager
        from src.kernel.concurrency import get_watchdog

        stream_manager = get_stream_manager()
        chat_stream = await stream_manager.activate_stream(self.stream_id)
        if chat_stream is None:
            logger.error(f"无法激活聊天流: {self.stream_id}")
            yield Failure("无法激活聊天流")
            return

        service = self._get_life_service()

        # 创建 LLM 请求
        try:
            request = self.create_request("actor", request_name="life_chatter")
        except (ValueError, KeyError) as e:
            logger.error(f"获取模型配置失败: {e}")
            yield Failure(f"模型配置错误: {e}")
            return

        # System prompt: 100% 静态可缓存
        system_text = self._build_chat_system_prompt(chat_stream, service)
        request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_text)))

        # 场景引导 SYSTEM 固定块
        scene_guide = self._build_scene_guide(chat_stream)
        if scene_guide:
            request.add_payload(LLMPayload(ROLE.SYSTEM, Text(scene_guide)))

        # 历史文本（首轮合并）
        history_text = self._build_history_text(chat_stream)

        # 注入工具
        usable_map = await self.inject_usables(request)

        # 初始化运行时
        rt = _WorkflowRuntime(
            response=request,
            phase=_Phase.WAIT_USER,
            history_merged=False,
            unreads=[],
            cross_round_seen_signatures=set(),
            unread_msgs_to_flush=[],
        )

        max_rounds = self._get_max_rounds()

        while True:
            _, unread_msgs = await self.fetch_unreads()

            # 安全兜底
            if rt.phase == _Phase.WAIT_USER and self._has_tool_result_tail(rt.response):
                self._transition(rt, _Phase.FOLLOW_UP, "context tail is TOOL_RESULT")

            # ── WAIT_USER ────────────────────────────────
            if rt.phase == _Phase.WAIT_USER:
                if not unread_msgs:
                    yield Wait()
                    continue

                rt.cross_round_seen_signatures.clear()
                rt.plain_text_retry_count = 0
                rt.unreads = unread_msgs

                unread_lines = "\n".join(
                    self.format_message_line(msg) for msg in unread_msgs
                )

                # 决策：是否响应
                decision = await self._should_respond(
                    unread_lines, unread_msgs, chat_stream,
                )
                logger.info(
                    f"决策: {decision.get('reason', '')} (响应: {decision.get('should_respond', False)})"
                )

                if not decision.get("should_respond", False):
                    logger.info("决定不响应，继续等待...")
                    yield Wait()
                    continue

                # 构建 user prompt
                user_prompt_text = self._build_chat_user_prompt(
                    chat_stream,
                    service,
                    unread_lines=unread_lines,
                    history_text=history_text if not rt.history_merged else "",
                )

                self._upsert_pending_unread_payload(
                    response=rt.response,
                    formatted_content=Text(user_prompt_text),
                )
                rt.history_merged = True
                self._transition(rt, _Phase.MODEL_TURN, "accepted unread batch")
                rt.unread_msgs_to_flush = unread_msgs
                continue

            # ── MODEL_TURN / FOLLOW_UP ───────────────────
            if rt.phase in (_Phase.MODEL_TURN, _Phase.FOLLOW_UP):
                try:
                    rt.response = await rt.response.send(stream=False)
                    await rt.response

                    if rt.phase == _Phase.MODEL_TURN:
                        if rt.unread_msgs_to_flush:
                            await self.flush_unreads(rt.unread_msgs_to_flush)
                        rt.unread_msgs_to_flush = []

                except Exception as error:
                    logger.error(f"LLM 请求失败: {error}", exc_info=True)
                    yield Failure("LLM 请求失败", error)
                    self._transition(rt, _Phase.WAIT_USER, "request failed")
                    continue

                self._transition(rt, _Phase.TOOL_EXEC, "model responded")
                continue

            # ── TOOL_EXEC ────────────────────────────────
            if rt.phase == _Phase.TOOL_EXEC:
                llm_response = rt.response

                call_list = getattr(llm_response, "call_list", None) or []
                response_msg = getattr(llm_response, "message", None)

                if not call_list:
                    if response_msg and str(response_msg).strip():
                        logger.warning(
                            f"LLM 返回了纯文本而非 tool call: {str(response_msg)[:100]}"
                        )
                        yield Stop(0)
                        return
                    yield Wait()
                    self._transition(rt, _Phase.WAIT_USER, "no call_list")
                    continue

                logger.info(f"本轮调用: {[c.name for c in call_list]}")

                should_wait = False
                has_pending_tool_results = False
                seen_sigs: set[str] = set()

                for call in call_list:
                    get_watchdog().feed_dog(self.stream_id)

                    # 去重
                    args = dict(call.args) if isinstance(call.args, dict) else {}
                    dedupe_args = {k: v for k, v in args.items() if k != "reason"}
                    try:
                        dedupe_key = f"{call.name}:{json.dumps(dedupe_args, ensure_ascii=False, sort_keys=True, default=str)}"
                    except TypeError:
                        dedupe_key = f"{call.name}:{dedupe_args}"

                    if dedupe_key in seen_sigs or dedupe_key in rt.cross_round_seen_signatures:
                        llm_response.add_payload(
                            LLMPayload(
                                ROLE.TOOL_RESULT,
                                ToolResult(value="检测到重复工具调用，已跳过", call_id=call.id, name=call.name),
                            )
                        )
                        continue
                    seen_sigs.add(dedupe_key)
                    rt.cross_round_seen_signatures.add(dedupe_key)

                    # pass_and_wait
                    if call.name == _PASS_AND_WAIT:
                        llm_response.add_payload(
                            LLMPayload(
                                ROLE.TOOL_RESULT,
                                ToolResult(value="已跳过，等待用户新消息", call_id=call.id, name=call.name),
                            )
                        )
                        should_wait = True
                        continue

                    # 执行工具
                    appended, success = await self.run_tool_call(
                        call, llm_response, usable_map,
                        rt.unreads[-1] if rt.unreads else None,
                    )

                    if appended and not call.name.startswith("action-"):
                        has_pending_tool_results = True

                # pass_and_wait 最高优先级
                if should_wait:
                    # 补 ASSISTANT 占位防止下一轮误判
                    if self._has_tool_result_tail(llm_response):
                        llm_response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(_SUSPEND_TEXT)))
                    yield Wait()
                    self._transition(rt, _Phase.WAIT_USER, "pass_and_wait")
                    continue

                if has_pending_tool_results:
                    self._transition(rt, _Phase.FOLLOW_UP, "pending tool results")
                    continue

                # 全部为 action 时补 SUSPEND
                if call_list and all(c.name.startswith("action-") for c in call_list):
                    llm_response.add_payload(LLMPayload(ROLE.ASSISTANT, Text(_SUSPEND_TEXT)))

                self._transition(rt, _Phase.WAIT_USER, "tool exec done")
                continue
