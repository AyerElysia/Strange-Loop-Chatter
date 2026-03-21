"""
Biometric Sleep System - 提示词注入组件 v2

根据状态和唤醒度注入对应的提示词：
- DROWSY: 困倦提示词
- SLEEPING (唤醒度 < 阈值): 睡眠提示词
- SLEEPING (唤醒度 >= 阈值): 被吵醒提示词
"""

from src.core.components.base import BasePrompt
from src.core.components.base.base.component_types import InjectionRule, InjectionType
from src.kernel.logger import get_logger
from ..core.sleep_manager import SleepState, SleepStateManager

logger = get_logger("sleep_prompt")


class SleepStatusPrompt(BasePrompt):
    prompt_name = "sleep_status_prompt"
    prompt_description = "根据睡眠状态注入特定的语气提示词"
    
    # 注入到主提示词中 - 同时支持 KFC（私聊）和 AFC（群聊）
    # 参考 period_plugin 的实现，使用正确的提示词目标名称
    injection_rules = [
        # AFC 群聊场景 - s4u 模式（注：normal 模式已弃用）
        InjectionRule(
            target_prompt="s4u_style_prompt",
            injection_type=InjectionType.PREPEND,
            priority=200
        ),
        # KFC 私聊场景 - 主提示词
        InjectionRule(
            target_prompt="kfc_main",
            injection_type=InjectionType.PREPEND,
            priority=200
        ),
        # KFC 私聊场景 - 回复提示词
        InjectionRule(
            target_prompt="kfc_replyer",
            injection_type=InjectionType.PREPEND,
            priority=200
        ),
        # KFC 私聊场景 - 统一提示词（新版KFC使用）
        InjectionRule(
            target_prompt="kfc_unified_prompt",
            injection_type=InjectionType.PREPEND,
            priority=200
        ),
    ]

    manager: SleepStateManager = None  # type: ignore

    def __init__(self, params, plugin_config: dict, target_prompt_name: str | None = None):
        super().__init__(params, plugin_config, target_prompt_name)

    async def execute(self) -> str:
        if not self.get_config("prompt.enable_injection", True):
            logger.debug("[SleepPrompt] 提示词注入已禁用")
            return ""

        # 获取 session_id（异步方式，从 ChatStream 获取 group_id）
        session_id, user_id, group_id = await self._extract_session_id_async()
        
        # 检查是否在忽略名单中
        # 如果用户/群组在忽略名单中（例如黑名单），则不注入任何睡眠相关的提示词
        # 这样即使全局状态是 SLEEPING，被忽略的用户也不会收到睡眠提示词
        if self.manager.is_ignored(user_id or "", group_id):
            logger.debug(f"[SleepPrompt] 用户/群组在忽略名单中，跳过提示词注入: user_id={user_id}, group_id={group_id}")
            return ""

        # 获取当前状态
        state = self.manager.get_current_state(session_id)
        
        logger.debug(f"[SleepPrompt] execute 被调用: session_id={session_id}, state={state}")
        
        # 根据状态和唤醒度选择提示词
        prompt = self._select_prompt(state, session_id)
        
        if prompt:
            logger.info(f"[SleepPrompt] 注入提示词: {prompt[:50]}...")
            return f"\n[状态感应: {prompt}]\n"
        
        logger.debug("[SleepPrompt] 无需注入提示词（AWAKE状态）")
        return ""
    
    async def _extract_session_id_async(self) -> tuple[str | None, str | None, str | None]:
        """从 params 中提取 session_id、user_id 和 group_id
        
        Returns:
            tuple: (session_id, user_id, group_id)
        """
        session_id = None
        
        params = self.params
        if not params:
            params = getattr(self, "context", {})

        if not params:
            return None, None, None

        # 兼容字典和对象访问
        if isinstance(params, dict):
            user_id = params.get("user_id")
            chat_id = params.get("chat_id")
            is_group_chat = params.get("is_group_chat", False)
        else:
            user_id = getattr(params, "user_id", None)
            chat_id = getattr(params, "chat_id", None)
            is_group_chat = getattr(params, "is_group_chat", False)

        group_id = None
        
        # 尝试从 ChatStream 获取 group_id
        if chat_id and is_group_chat:
            try:
                from src.chat.message_receive.chat_stream import get_chat_manager
                chat_manager = get_chat_manager()
                chat_stream = await chat_manager.get_stream(chat_id)
                if chat_stream and chat_stream.group_info:
                    group_id = chat_stream.group_info.group_id
                    logger.debug(f"[SleepPrompt] 从 ChatStream 获取 group_id: {group_id}")
            except Exception as e:
                logger.warning(f"[SleepPrompt] 无法从 ChatStream 获取 group_id: {e}")

        # 构建 session_id（必须与 sleep_interceptor 格式一致）
        if is_group_chat:
            # 优先使用从 ChatStream 获取的 group_id
            if group_id:
                session_id = f"group_{group_id}"
            elif user_id:
                session_id = f"group_unknown_{user_id}"
            else:
                logger.debug(f"[SleepPrompt] 群聊但无法获取 group_id，chat_id={chat_id}")
        else:
            # 私聊使用 user_id
            if user_id:
                session_id = f"private_{user_id}"
            elif chat_id:
                if chat_id.startswith("private_"):
                    session_id = chat_id
                else:
                    session_id = f"private_{chat_id}"

        # 只在 session_id 为 None 时记录警告，其他情况用 debug
        if session_id is None:
            # 降低日志级别，避免干扰
            logger.debug(f"[SleepPrompt] session_id=None (group_id={group_id}, user_id={user_id}, chat_id={chat_id})")
        else:
            logger.debug(f"[SleepPrompt] 提取 session_id: {session_id}")
            
        return session_id, user_id, group_id
    
    def _select_prompt(self, state: SleepState, session_id: str | None) -> str | None:
        """
        根据状态和唤醒度选择合适的提示词
        
        - AWAKE: 无提示词
        - DROWSY: 困倦提示词
        - SLEEPING: 统一使用被吵醒提示词（因为消息能到达这里说明已经被放行了）
        
        简化逻辑：如果消息能够触发提示词注入，说明拦截器已经放行了这条消息，
        也就意味着要么不在睡眠时间，要么已经被吵醒了。所以 SLEEPING 状态下
        直接使用 woken_prompt 即可。
        """
        if state == SleepState.AWAKE:
            return None
        
        if state == SleepState.DROWSY:
            prompt = self.get_config('prompt.drowsy_prompt')
            logger.debug(f"[SleepPrompt] 状态=DROWSY, 返回困倦提示词")
            return prompt
        
        if state == SleepState.SLEEPING:
            # 简化逻辑：消息能到达这里说明拦截器已经放行，即已被吵醒
            # 直接使用 woken_prompt
            prompt = self.get_config('prompt.woken_prompt')
            logger.info(f"[SleepPrompt] 状态=SLEEPING(消息被放行说明已吵醒), 返回被吵醒提示词")
            return prompt
        
        return None