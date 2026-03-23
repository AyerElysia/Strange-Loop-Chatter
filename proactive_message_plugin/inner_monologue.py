"""内心独白生成模块。

生成内心独白 prompt 并处理 LLM 决策。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from src.app.plugin_system.api import llm_api
from src.core.components.base.chatter import BaseChatter
from src.core.components.types import ChatType
from src.core.config import get_core_config
from src.core.managers import get_plugin_manager
from src.kernel.logger import get_logger
from src.kernel.llm import LLMPayload, ROLE, Text

from default_chatter.prompt_builder import DefaultChatterPromptBuilder
from thinking_plugin.tools.think_tool import ThinkTool
from emoji_sender.action import SendEmojiMemeAction
from .tools.wait_longer import WaitLongerTool
from default_chatter.plugin import SendTextAction

if TYPE_CHECKING:
    from src.core.models.stream import ChatStream
    from src.core.managers.stream_manager import StreamContext

logger = get_logger("proactive_inner_monologue", display="内心独白")


@dataclass
class InnerMonologueResult:
    """内心独白结果"""

    decision: str  # "send_message" 或 "wait_longer"
    thought: str  # 内心想法
    content: str | None = None  # 如果要发消息，消息内容
    wait_minutes: float | None = None  # 如果要等待，等待时长


# 内心独白 prompt 模板
INNER_MONOLOGUE_PROMPT = """# 关于你
你是**{nickname}**，{identity}。
{personality_core}
{personality_side}

# 场景引导
{theme_guide}

# 等待状态
你上次收到 {user_name} 的消息已经是{elapsed_minutes:.0f}分钟了。
他一直没有回复你。

你记得你们之前的对话：
{conversation_history}

{monologue_history_section}

先快速回顾最近的独白核心点，避免原句复述，要有连续性，基于之前的有递进的新想法。
先分析当前场景（比如他刚才的状态、你已经等了多久，我之前怎么想的，我现在是什么想法），再决定是主动发消息还是继续等待。

你现在心里是什么感觉？
你在想他吗？还是担心他在忙？
或者有什么话想对他说？

你必须二选一，并且必须调用工具，不允许只输出纯文本：
- 如果想主动发一条消息，调用 send_text
- 如果觉得还想再等等，先调用think工具作为你的内心独白，然后调用 wait_longer，并给出 wait_minutes 和 thought

如果你没有调用工具，这次回答会被视为无效。

请将你的内心独白控制在 50~100 字左右，避免长篇大论。"""


async def generate_inner_monologue(
    chat_stream: "ChatStream",
    elapsed_minutes: float,
    user_name: str,
    model_set: str = "actor",
) -> InnerMonologueResult | None:
    """生成内心独白并获取决策。

    Args:
        chat_stream: 聊天流对象
        elapsed_minutes: 已等待分钟数
        user_name: 用户昵称
        model_set: 使用的模型配置名称

    Returns:
        InnerMonologueResult | None: 独白结果，如果失败返回 None
    """
    stream_id = chat_stream.stream_id

    # 获取人设信息（从 core.toml 读取）
    core_config = get_core_config()
    nickname = chat_stream.bot_nickname or core_config.personality.nickname
    identity = core_config.personality.identity
    personality_core = core_config.personality.personality_core
    personality_side = core_config.personality.personality_side

    # 获取场景引导（优先使用 default_chatter 配置）
    chat_type_raw = str(getattr(chat_stream, "chat_type", "") or "").lower()
    try:
        dc_plugin = get_plugin_manager().get_plugin("default_chatter")
        dc_cfg = getattr(dc_plugin, "config", None)
        theme_cfg = getattr(getattr(dc_cfg, "plugin", None), "theme_guide", None)
        theme_private = getattr(theme_cfg, "private", "") if theme_cfg else ""
        theme_group = getattr(theme_cfg, "group", "") if theme_cfg else ""
    except Exception:
        theme_private = ""
        theme_group = ""

    if chat_type_raw == ChatType.PRIVATE.value:
        theme_guide = theme_private
    elif chat_type_raw == ChatType.GROUP.value:
        theme_guide = theme_group
    else:
        theme_guide = ""

    # 构建 prompt
    conversation_history = extract_conversation_history(
        chat_stream.context.history_messages,
        limit=10,
    )
    monologue_limit = getattr(getattr(chat_stream, "_plugin_config", None), "settings", None)
    monologue_limit = getattr(monologue_limit, "monologue_history_limit", 5)
    monologue_history = extract_monologue_history(
        chat_stream.context.history_messages,
        limit=monologue_limit,
    )
    monologue_section = format_monologue_section(monologue_history)

    prompt_text = INNER_MONOLOGUE_PROMPT.format(
        nickname=nickname,
        identity=identity,
        personality_core=personality_core,
        personality_side=personality_side,
        theme_guide=theme_guide,
        user_name=user_name,
        elapsed_minutes=elapsed_minutes,
        conversation_history=conversation_history or "（没有历史对话）",
        monologue_history_section=monologue_section,
    )

    logger.info(f"生成内心独白：{stream_id[:8]}... 已等待{elapsed_minutes:.0f}分钟")

    try:
        model_config = llm_api.get_model_set_by_task(model_set)

        if not model_config:
            logger.error(f"模型配置不存在：{model_set}")
            return None

        llm_request = llm_api.create_llm_request(
            model_config,
            request_name=f"inner_monologue_{stream_id}",
        )

        default_chatter_plugin = get_plugin_manager().get_plugin("default_chatter")
        default_chatter_config = getattr(default_chatter_plugin, "config", None)

        system_prompt = await DefaultChatterPromptBuilder.build_system_prompt(
            default_chatter_config,
            chat_stream,
        )
        history_text = DefaultChatterPromptBuilder.build_enhanced_history_text(
            chat_stream,
            BaseChatter.format_message_line,
        )
        user_prompt = await DefaultChatterPromptBuilder.build_user_prompt(
            chat_stream,
            history_text=history_text,
            unread_lines=prompt_text,
            extra="",
        )

        if system_prompt:
            llm_request.add_payload(LLMPayload(ROLE.SYSTEM, [Text(system_prompt)]))

        tool_registry = llm_api.create_tool_registry(
            [ThinkTool, SendTextAction, SendEmojiMemeAction, WaitLongerTool]
        )
        llm_request.add_payload(LLMPayload(ROLE.TOOL, tool_registry.get_all()))
        llm_request.add_payload(LLMPayload(ROLE.USER, [Text(user_prompt)]))

        # 发送请求
        response = await llm_request.send(stream=False)

        # 解析响应
        message = response.message
        call_list = response.call_list or []
        thought_from_think = ""

        # 提取所有工具的 thought
        for call in call_list:
            if call.name in {"think", "tool-think"} and isinstance(call.args, dict):
                thought_from_think = str(call.args.get("thought", "") or "").strip()

        # 检查工具调用获取决策
        send_message_call = None
        wait_longer_call = None
        for call in call_list:
            if call.name in {"send_text", "action-send_text"}:
                send_message_call = call
            elif call.name in {"wait_longer", "tool-wait_longer"}:
                wait_longer_call = call

        # 处理发送消息决策
        if send_message_call:
            content = send_message_call.args.get("content", "") if isinstance(send_message_call.args, dict) else ""
            thought_text = (thought_from_think or message or "").strip()
            if thought_text:
                logger.info(f"内心独白内容：{thought_text}")
            logger.info(f"内心独白决策：send_message")
            return InnerMonologueResult(
                decision="send_message",
                thought=thought_text,
                content=content,
            )
        # 处理继续等待决策
        elif wait_longer_call:
            wait_minutes = wait_longer_call.args.get("wait_minutes", 30) if isinstance(wait_longer_call.args, dict) else 30
            wait_thought = wait_longer_call.args.get("thought", "") if isinstance(wait_longer_call.args, dict) else ""
            # 优先使用 wait_longer 的 thought，如果没有则用 think 的
            thought_text = (wait_thought or thought_from_think or message or "").strip()
            if thought_text:
                logger.info(f"内心独白内容：{thought_text}")
            logger.info(f"内心独白决策：wait_longer({float(wait_minutes):.1f} 分钟)")
            return InnerMonologueResult(
                decision="wait_longer",
                thought=thought_text,
                wait_minutes=float(wait_minutes),
            )

        # 如果没有工具调用，尝试从消息中提取
        fallback_thought = (thought_from_think or message or "").strip()
        if fallback_thought:
            logger.info(f"内心独白内容：{fallback_thought[:300]}")
        logger.warning(f"内心独白未返回工具调用：{message[:100] if message else 'None'}")
        if fallback_thought:
            logger.info("内心独白未调用工具，默认转为 wait_longer(30 分钟)")
            return InnerMonologueResult(
                decision="wait_longer",
                thought=fallback_thought,
                wait_minutes=30.0,
            )
        return None

    except Exception as e:
        logger.error(f"生成内心独白失败：{e}", exc_info=True)
        return None


def extract_conversation_history(
    history_messages: list,
    limit: int = 10,
) -> str:
    """从历史消息中提取对话历史。

    Args:
        history_messages: 历史消息列表
        limit: 最大提取数量

    Returns:
        str: 格式化的对话历史文本
    """
    if not history_messages:
        return ""

    # 取最近 N 条消息
    recent = history_messages[-limit:]

    lines = []
    for msg in recent:
        sender_name = getattr(msg, "sender_name", "未知")
        content = getattr(msg, "processed_plain_text", "") or getattr(msg, "content", "")

        # 跳过空消息
        if not content:
            continue

        # 简化时间
        send_time = getattr(msg, "send_time", None)
        time_str = ""
        if send_time:
            try:
                time_str = send_time.strftime("%H:%M")
            except Exception:
                pass

        line = f"[{time_str}] {sender_name}: {content}" if time_str else f"{sender_name}: {content}"
        lines.append(line)

    return "\n".join(lines)


def extract_monologue_history(
    history_messages: list,
    limit: int = 5,
) -> list[str]:
    """从历史消息中提取内心独白历史。

    Args:
        history_messages: 历史消息列表
        limit: 最大提取数量

    Returns:
        list[str]: 内心独白内容列表
    """
    if not history_messages:
        return []

    # 过滤出内心独白消息
    monologues = [
        msg for msg in history_messages
        if getattr(msg, "is_inner_monologue", False)
    ]

    # 取最近 N 条
    recent = monologues[-limit:]

    result = []
    for msg in recent:
        content = getattr(msg, "processed_plain_text", "") or getattr(msg, "content", "")
        if content:
            # 去除 [内心独白] 前缀
            thought = content.replace("[内心独白] ", "").strip()
            if thought:
                result.append(thought)

    return result


def format_monologue_section(monologue_history: list[str]) -> str:
    """格式化内心独白历史为 prompt 文本。

    Args:
        monologue_history: 内心独白内容列表

    Returns:
        str: 格式化的内心独白历史文本
    """
    if not monologue_history:
        return ""

    lines = []
    for i, thought in enumerate(monologue_history, 1):
        lines.append(f"独白 {i}: {thought}")

    return "你之前的内心活动：\n" + "\n".join(lines)
