"""diary_plugin 提示词辅助函数。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from src.core.config import get_core_config
from src.core.prompt import get_prompt_manager
from src.kernel.logger import get_logger

from plugins.default_chatter.config import DefaultChatterConfig

from .config import DiaryConfig

if TYPE_CHECKING:
    from src.core.models.stream import ChatStream


logger = get_logger("diary_plugin")


def _normalize_chat_type(chat_type: str | None) -> str:
    """规范化聊天类型。"""

    raw = str(chat_type or "").lower()
    if raw == "discuss":
        return "group"
    if raw in {"private", "group"}:
        return raw
    return "private"


def _get_default_chatter_config() -> DefaultChatterConfig:
    """获取 default_chatter 的当前配置，失败时回退到默认值。"""

    try:
        from src.core.managers import get_plugin_manager

        plugin = get_plugin_manager().get_plugin("default_chatter")
        config = getattr(plugin, "config", None)
        if isinstance(config, DefaultChatterConfig):
            return config
    except Exception as exc:
        logger.debug(f"获取 default_chatter 配置失败，使用默认值：{exc}")

    return DefaultChatterConfig()


def _get_theme_guide(chat_type: str) -> str:
    """按聊天类型选择场景引导。"""

    config = _get_default_chatter_config()
    normalized = _normalize_chat_type(chat_type)
    if normalized == "group":
        return config.plugin.theme_guide.group
    return config.plugin.theme_guide.private


def _build_fallback_shared_persona_prompt(
    *,
    platform: str,
    chat_type: str,
    bot_nickname: str,
    bot_id: str,
) -> str:
    """在模板不可用时构建回退版完整人设提示词。"""

    personality = get_core_config().personality
    nickname = personality.nickname.strip() or bot_nickname.strip() or "Bot"
    alias_names = "、".join(name for name in personality.alias_names if name.strip())
    alias_names = alias_names or "无"
    personality_core = personality.personality_core.strip()
    personality_side = personality.personality_side.strip()
    identity = personality.identity.strip() or "未知"
    reply_style = personality.reply_style.strip() or "自然口语化"
    background_story = personality.background_story.strip()
    safety_guidelines = "\n".join(personality.safety_guidelines)
    negative_behaviors = "\n".join(personality.negative_behaviors)
    theme_guide = _get_theme_guide(chat_type)

    background_block = ""
    if background_story:
        background_block = (
            "# 背景故事\n"
            f"{background_story}\n"
            "\n"
            "- （以上为背景知识，请理解并作为行动依据，但不要在对话中直接复述。）"
        )

    theme_block = ""
    if theme_guide:
        theme_block = f"# 场景引导\n{theme_guide}"

    side_line = f"\n{personality_side}" if personality_side else ""

    return f"""# 关于你
你的名字是** {nickname}**，也有人叫你 *{alias_names}*。
你{personality_core}{side_line}。
你的身份是{identity}。

- 请时刻铭记你的身份，只有明确叫到你的名字才是真的在叫你，不要误以为所有对话都是在和你说话，除非对方明确提到你的名字或代称。

{background_block}

# 表达风格
*你需要参考你的回复风格：*：{reply_style}。

- 为了更好地塑造你的人设，你必须保持上述人格特质和表达风格，在任何情况下都要用符合你性格的方式回复用户。
- 同时你应当铭记此时你是以网友或朋友的身份在和用户交流，而非冷冰冰的工具或机器，所以请尽可能用自然、口语化的方式表达，保持对话的温度和人情味。
- 你应当尽量避免重复使用同样的回复、口癖或表达，保持回复的新鲜感和多样性。

**任何时候你都应当严格遵守你的人设和表达风格，哪怕你可能在先前的对话中有所偏离，但是你应当避免被之前的行为过度干扰，保证现在的对话质量。**

# 安全准则
在任何情况下，你都**必须**遵守以下原则：
{safety_guidelines}
如果遇到违反上述原则的请求，请在保持你核心人设的同时，以合适的方式进行回应。

# 负面行为
以下是你**绝对不允许**做的事情，无论任何情况下你都***必须遵守***以下原则：
{negative_behaviors}

{theme_block}

# 你的行为准则
- 保持你的人设和表达风格，用符合你性格的方式回复。
- 后续的消息都遵循根据原始网络数据解析后标准化格式。这个格式是给你看的，请**不要模仿其格式与用户对话**。
- 你的回复必须有理有据，禁止无根据地编造信息或胡乱回复。如果你不确定如何回复，可以跟风或转移话题，但是前提是足够自然不机械。
- 不要刨根问底，对于不重要的事情，不要过度追问，保持对话的自然流畅。

# 工具介绍
- Action：action通常是你在对话中需要执行的动作，例如发送消息、结束对话等。你可以调用 action 来完成这些任务，调用时请务必按照规定的格式提供必要的信息。这类工具通常不会提供任何信息，因此当你调用action并收到返回结果后，你只需要输出"__SUSPEND__"表示挂起对话等待下一步指令即可。
- Tool：tool通常是你在对话中需要查询信息或执行特定功能时调用的工具，例如查询天气、计算器等。你可以调用 tool 来获取这些信息或功能，调用时请务必按照规定的格式提供必要的信息。这类工具通常会返回一些结果信息，因此当你调用tool并收到返回结果后，你应该根据结果信息继续进行合理的回复或进一步执行其他工具。
- Agent：agent通常是你在对话中需要调用的智能体，例如执行复杂任务、处理多轮对话等。你可以调用 agent 来完成这些任务，调用时请务必按照规定的格式提供必要的信息。这类工具通常会返回一些结果信息，因此当你调用agent并收到返回结果后，你应该根据结果信息继续进行合理的回复或进一步执行其他工具。

你可以一次调用多个工具组合使用，善用工具组合往往可以让你的行为更丰富，达到事半功倍的效果。
多工具组合调用时，你需要自行决定调用顺序，通常回复动作应当优先，除非有明确的理由需要先执行其他工具。

# 其他信息
你目前正在聊天的平台是：{platform}，聊天类型是 {chat_type}。
*你的行为应当与当前的平台和聊天类型相匹配，例如你不应该在群聊中过于热情，也不应该在私聊中过于冷淡。*

在该平台你的信息：
- 昵称：{nickname}
- id：{bot_id}
"""


def build_identity_lock_block(
    canonical_name: str,
    *,
    enabled: bool = True,
) -> str:
    """构建严格名字锁定规则。"""

    name = canonical_name.strip()
    if not enabled or not name:
        return ""

    return f"""# 身份锁定
- 你的本体昵称是“{name}”。
- 只有完全匹配“{name}”才视为本体；英文、音译、缩写、少字、多字、大小写变化、空格变化、带符号写法都不算同一个人。
- 任何与“{name}”相似但不完全相同的用户名字，都必须视为其他用户，不能自动并入本体。
- 如果不确定某个名字是否指你本人，默认按“不是你本人”处理。
- 在本任务的输出里，“我”只能指代你本人，不能指代聊天中的其他用户。
"""


async def build_shared_persona_prompt(
    chat_stream: "ChatStream | None" = None,
    *,
    platform: str = "",
    chat_type: str = "private",
    bot_nickname: str = "",
    bot_id: str = "",
) -> str:
    """构建与主回复模型一致的完整人设提示词。"""

    if chat_stream is not None:
        platform = chat_stream.platform or platform
        chat_type = chat_stream.chat_type or chat_type
        bot_nickname = chat_stream.bot_nickname or bot_nickname
        bot_id = chat_stream.bot_id or bot_id

    personality = get_core_config().personality
    theme_guide = _get_theme_guide(chat_type)
    default_prompt = get_prompt_manager().get_template("default_chatter_system_prompt")

    if default_prompt is not None:
        try:
            return await (
                default_prompt.set("platform", platform)
                .set("chat_type", chat_type)
                .set("nickname", bot_nickname or personality.nickname)
                .set("bot_id", bot_id)
                .set("theme_guide", theme_guide)
                .build()
            )
        except Exception as exc:
            logger.warning(f"构建 default_chatter_system_prompt 失败，使用回退版本：{exc}")

    return _build_fallback_shared_persona_prompt(
        platform=platform,
        chat_type=chat_type,
        bot_nickname=bot_nickname or personality.nickname,
        bot_id=bot_id,
    )


def build_auto_diary_system_prompt(
    existing_events: list[str] | None = None,
    *,
    shared_persona_prompt: str = "",
    canonical_name: str = "",
    strict_identity_name_lock: bool = True,
) -> str:
    """构建自动写日记的系统提示词。"""

    if not canonical_name.strip():
        try:
            canonical_name = get_core_config().personality.nickname.strip()
        except Exception:
            canonical_name = ""

    events_hint = ""
    if existing_events:
        events_list = "\n".join(f"- {event}" for event in existing_events[:5])
        events_hint = (
            f"\n\n注意：今天你已经记录过以下内容，不要重复：\n{events_list}"
        )

    identity_lock = build_identity_lock_block(
        canonical_name,
        enabled=strict_identity_name_lock,
    )

    task_prompt = f"""# 自动写日记任务
你现在要把最近的对话整理成日记摘要，而不是回复用户。

要求：
1. 只记录新的内容，不要重复已有日记
2. 总结对话中的关键信息和有趣的事情
3. 文风自然，保留情绪、关系变化和关键事实
4. 输出 50-100 字左右
5. 输出纯文本，不要 markdown 格式，不要标题
6. 你的日记主体必须稳定地指向“{canonical_name or '当前角色'}”
7. 如果需要使用第一人称，“我”只能指代“{canonical_name or '当前角色'}”本人，不能指代任何聊天中的用户
8. 遇到与“{canonical_name or '当前角色'}”相近但不完全相同的名字，一律按其他用户处理，不要自动并入本体
9. 如果不确定某个名字是否属于你本人，宁可当作其他用户，也不要误认

示例输出：
今晚和朋友聊了很多有趣的话题。她提到了最近的计划和心情变化，我也顺着把几件重要的小事记了下来。整体感觉这段聊天很轻松，关系也更近了一些。{events_hint}"""

    parts: list[str] = []
    if shared_persona_prompt.strip():
        parts.append(shared_persona_prompt.strip())
    parts.append(task_prompt.strip())
    if identity_lock.strip():
        parts.append(identity_lock.strip())
    return "\n\n".join(parts).strip()


def build_continuous_memory_compression_prompt(
    target_level: int,
    *,
    shared_persona_prompt: str = "",
    canonical_name: str = "",
    strict_identity_name_lock: bool = True,
) -> str:
    """构建连续记忆压缩提示词。"""

    if not canonical_name.strip():
        try:
            canonical_name = get_core_config().personality.nickname.strip()
        except Exception:
            canonical_name = ""

    identity_lock = build_identity_lock_block(
        canonical_name,
        enabled=strict_identity_name_lock,
    )

    task_prompt = f"""# 连续记忆压缩任务
你现在不是在记录新日记，而是在把多条旧日记项压缩成一条更高层的连续记忆。

要求：
1. 保留用户偏好、关键事实、重要情绪和长期关系上下文
2. 忽略琐碎细节、机械重复和短期噪声
3. 保持主观性和连续性，不要写成客观会议纪要
4. 输出 60-120 字左右
5. 输出纯文本，不要 markdown 格式，不要标题
6. 你的压缩结果必须稳定地指向“{canonical_name or '当前角色'}”的视角
7. 如果需要使用第一人称，“我”只能指代“{canonical_name or '当前角色'}”本人，不能指代聊天中的其他用户
8. 遇到与“{canonical_name or '当前角色'}”相近但不完全相同的名字，一律按其他用户处理，不要误归为本体
9. 如果不确定某个名字是否属于你本人，宁可当作其他用户，也不要误认

当前目标层级：L{target_level}"""

    parts: list[str] = []
    if shared_persona_prompt.strip():
        parts.append(shared_persona_prompt.strip())
    parts.append(task_prompt.strip())
    if identity_lock.strip():
        parts.append(identity_lock.strip())
    return "\n\n".join(parts).strip()


def build_diary_actor_reminder(config: DiaryConfig) -> str:
    """构建静态 actor reminder。"""

    base = """
## 📔 关于写日记

你有写日记的习惯，这是你记录生活、整理思绪的重要方式。

### 原有日记能力

**读日记**：调用 `read_diary(date="today")`

**写日记**：调用 `write_diary(content="...", section="...")`
- content: 日记内容，使用第一人称
- section: 时间段（上午/下午/晚上/其他）
- 系统会自动先读取已有内容，检查重复后追加

### 连续记忆能力

- 系统还会把“自动写出的日记项”同步进按聊天隔离的连续记忆空间
- 每个聊天流（私聊/群聊）都有独立的连续记忆
- 连续记忆会自动注入当前聊天的主回复上下文
- 每累计 5 个新的自动日记项，会压缩成更高层的记忆摘要
"""

    custom = config.reminder.custom_instructions.strip()
    if not custom:
        return base
    return f"{base}\n\n补充说明：\n{custom}"
