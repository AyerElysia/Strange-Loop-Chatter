"""
Biometric Sleep System - 事件拦截器 v2

简化版逻辑：
- AWAKE: 全部放行
- DROWSY: 只放行私聊/@消息
- SLEEPING: 基于唤醒度判断是否放行

关键设计：
- 被拦截的消息仍会存入数据库（保留历史记录）
- 只是阻止消息触发后续的聊天流程

提及类型说明：
- 弱提及（mention_type=1）：消息文本包含机器人名字/别名，不触发唤醒
- 强提及（mention_type=2）：真正的@/回复/私聊，触发唤醒
"""

from typing import Any

from src.kernel.logger import get_logger
from src.core.components.base.event_handler import BaseEventHandler
from src.core.components.types import EventType

from ..core.sleep_manager import SleepState, SleepStateManager

logger = get_logger("sleep_interceptor")


class SleepInterceptor(BaseEventHandler):
    handler_name = "sleep_interceptor"
    handler_description = "睡眠系统消息拦截器"
    weight = 1000  # 高权重，优先拦截
    intercept_message = True
    init_subscribe = [EventType.ON_MESSAGE_RECEIVED, EventType.ON_NOTICE_RECEIVED]

    manager: SleepStateManager = None  # type: ignore

    def __init__(self):
        super().__init__()

    async def _store_message_before_intercept(self, message_obj) -> None:
        """
        在拦截消息之前，先将消息存入数据库并添加到聊天上下文
        
        这确保被拦截的消息：
        1. 会持久化到数据库
        2. 会出现在聊天上下文的历史记录中
        
        让机器人醒来后能看到睡眠期间发生了什么。
        
        注意：只添加到上下文，不启动 stream_loop 处理流程。
        """
        try:
            from src.chat.message_receive.chat_stream import get_chat_manager
            from src.chat.message_receive.storage import MessageStorage
            
            # 获取 stream_id
            stream_id = None
            if hasattr(message_obj, "chat_info") and message_obj.chat_info:
                stream_id = getattr(message_obj.chat_info, "stream_id", None)
            
            if not stream_id:
                logger.warning("[SleepInterceptor] 无法获取 stream_id，跳过消息存储")
                return
            
            # 通过 ChatManager 获取 ChatStream
            chat_manager = get_chat_manager()
            chat_stream = await chat_manager.get_stream(stream_id)
            
            if not chat_stream:
                logger.warning(f"[SleepInterceptor] 无法获取 ChatStream: {stream_id}，跳过消息存储")
                return
            
            # 1. 存储消息到数据库
            await MessageStorage.store_message(message_obj, chat_stream)
            
            # 2. 添加消息到聊天上下文（直接操作 context，不触发 stream_loop）
            # 这样消息会出现在历史记录中，但不会触发回复处理
            await chat_stream.context.add_message(message_obj)
            
            logger.debug(f"[SleepInterceptor] 拦截前已存储消息并添加到上下文: {getattr(message_obj, 'message_id', 'unknown')}")
            
        except Exception as e:
            logger.error(f"[SleepInterceptor] 存储消息失败: {e}")

    async def execute(self, kwargs: dict | None) -> Any:
        from src.core.components.base.base.base_event import HandlerResult
        
        if not kwargs:
            return HandlerResult(True, True, None)

        # 尝试获取消息对象（普通消息）或 notice 对象
        message_obj = kwargs.get("message")
        notice_obj = kwargs.get("notice")
        
        # 如果是 notice 事件，提取相关信息
        if notice_obj and not message_obj:
            return await self._handle_notice(notice_obj, kwargs)
        
        if not message_obj:
            return HandlerResult(True, True, None)

        # 统一转换为字典处理
        msg_dict = {}
        if isinstance(message_obj, dict):
            msg_dict = message_obj
        elif hasattr(message_obj, "flatten"):
            msg_dict = message_obj.flatten()
        elif hasattr(message_obj, "__dict__"):
            msg_dict = message_obj.__dict__
        else:
            logger.warning(f"[SleepInterceptor] 无法解析消息对象类型: {type(message_obj)}")
            return HandlerResult(True, True, None)

        # 提取关键字段
        user_id = str(msg_dict.get("chat_info_user_id") or msg_dict.get("user_id") or "")
        group_id = str(msg_dict.get("chat_info_group_id") or msg_dict.get("group_id") or "")
        
        if not group_id or group_id == "None":
            group_id = None

        # 判定是否为私聊
        chat_platform = msg_dict.get("chat_info_platform", "")
        message_type = msg_dict.get("message_type", "")
        is_private = (message_type == "private") or (chat_platform == "private") or (group_id is None)
        
        # 检查是否被强提及（真正的@自己/回复自己），需要精确判断
        # 强提及: @机器人（精确匹配QQ号）、回复消息
        # 弱提及: 消息文本包含机器人名字/别名（不应触发唤醒）
        #
        # 注意：不使用 is_mentioned_bot_in_message 函数，因为它有"祖传问题"——
        # 当消息对象已有 is_mentioned=True 时，会直接返回 mention_type=2，
        # 不区分是强提及还是弱提及。所以这里自己实现精确判断。
        is_strong_mention = False
        
        # 获取机器人QQ号，用于精确匹配
        from src.config.config import global_config
        bot_qq = str(global_config.bot.qq_account) if global_config else ""
        
        # 获取消息文本
        processed_text = msg_dict.get("processed_plain_text") or msg_dict.get("display_message") or ""
        
        # 方法1: 检查 processed_plain_text 中是否@了机器人
        # 格式: @<昵称:QQ号>，例如 @<爱莉希雅:3910007334>
        if bot_qq:
            import re
            at_pattern = rf"@<[^>]+:{bot_qq}>"
            if re.search(at_pattern, processed_text):
                is_strong_mention = True
                logger.info(f"[SleepInterceptor] 检测到@机器人 (匹配: {at_pattern})")
        
        # 方法2: 检查是否是回复机器人的消息
        # 格式: [回复 xxx(QQ号)：xxx]，说：xxx
        if not is_strong_mention and bot_qq:
            import re
            reply_pattern = rf"\[回复.*?\({bot_qq}\).*?\]"
            reply_pattern2 = rf"\[回复<[^>]+:{bot_qq}>.*?\]"
            if re.search(reply_pattern, processed_text) or re.search(reply_pattern2, processed_text):
                is_strong_mention = True
                logger.info(f"[SleepInterceptor] 检测到回复机器人的消息")
        
        # 方法3: 检查 reply_to 字段并验证是否回复的是机器人
        # 暂时不使用这个方法，因为需要查询数据库确认原消息发送者
        # if not is_strong_mention and "reply_to" in msg_dict and msg_dict["reply_to"]:
        #     pass
        
        # 兼容旧的 CQ 码格式
        if not is_strong_mention and not is_private:
            if "[CQ:reply,id=" in processed_text:
                is_strong_mention = True
                logger.info(f"[SleepInterceptor] 检测到CQ码回复")
        
        # 调试日志
        logger.debug(f"[SleepInterceptor] 强提及判断: is_strong_mention={is_strong_mention}, processed_text={processed_text[:100] if len(processed_text) > 100 else processed_text}")

        # 白名单/黑名单检查
        if self.manager.is_ignored(user_id, group_id):
            return HandlerResult(True, True, None)

        # 获取当前状态
        session_id = f"group_{group_id}" if group_id else f"private_{user_id}"
        state = self.manager.get_current_state(session_id)

        # 有效交互 = 私聊 或 强提及（真正的@/回复）
        # 注意：弱提及（名字匹配）不算有效交互，不会累计唤醒度
        is_effective = is_private or is_strong_mention
        
        logger.debug(f"[SleepInterceptor] session={session_id}, state={state}, is_private={is_private}, is_strong_mention={is_strong_mention}, is_effective={is_effective}")

        # ========== 状态处理 ==========
        
        if state == SleepState.AWAKE:
            # 清醒状态：全部放行
            return HandlerResult(True, True, None)

        if state == SleepState.DROWSY:
            # 困倦状态：只放行有效交互，触发延时
            if is_effective:
                self.manager.update_activity(True)
                return HandlerResult(True, True, None)
            else:
                # 群聊闲聊消息拦截（但仍存入数据库）
                await self._store_message_before_intercept(message_obj)
                return HandlerResult(True, False, None)

        if state == SleepState.SLEEPING:
            # 睡眠状态：基于唤醒度判断
            if not is_effective:
                # 普通群聊消息直接拦截，不累计唤醒度（但仍存入数据库）
                await self._store_message_before_intercept(message_obj)
                return HandlerResult(True, False, None)
            
            # 有效交互，累计唤醒度
            new_val, just_woken = self.manager.add_wake_value(session_id)
            
            # 判断是否应该放行
            if self.manager.is_woken(session_id):
                # 唤醒度 >= 阈值，放行消息
                if just_woken:
                    logger.info(f"[SleepInterceptor] 会话 {session_id} 刚被吵醒，放行消息")
                else:
                    logger.debug(f"[SleepInterceptor] 会话 {session_id} 处于被吵醒状态，继续放行")
                return HandlerResult(True, True, None)
            else:
                # 唤醒度 < 阈值，拦截消息（但仍存入数据库）
                await self._store_message_before_intercept(message_obj)
                logger.info(f"[SleepInterceptor] 会话 {session_id} 唤醒度未达阈值，拦截消息（已存入数据库）")
                return HandlerResult(True, False, None)

        # 默认放行
        return HandlerResult(True, True, None)

    async def _handle_notice(self, notice_obj, kwargs: dict) -> Any:
        """
        处理 notice 事件（戳一戳、禁言等）
        
        Notice 消息视为有效交互，可以累计唤醒度
        """
        from src.core.components.base.base.base_event import HandlerResult
        
        # 提取 notice 信息
        if isinstance(notice_obj, dict):
            notice_dict = notice_obj
        elif hasattr(notice_obj, "__dict__"):
            notice_dict = notice_obj.__dict__
        else:
            return HandlerResult(True, True, None)
        
        # 提取关键字段
        user_id = str(notice_dict.get("user_id") or notice_dict.get("sender_id") or "")
        group_id = str(notice_dict.get("group_id") or "")
        
        if not group_id or group_id == "None":
            group_id = None
        
        # 白名单/黑名单检查
        if self.manager.is_ignored(user_id, group_id):
            return HandlerResult(True, True, None)
        
        # 获取当前状态
        session_id = f"group_{group_id}" if group_id else f"private_{user_id}"
        state = self.manager.get_current_state(session_id)
        
        # Notice 消息视为有效交互
        is_effective = True
        
        logger.debug(f"[SleepInterceptor] Notice事件: session={session_id}, state={state}")
        
        # 状态处理（与普通消息相同）
        if state == SleepState.AWAKE:
            return HandlerResult(True, True, None)
        
        if state == SleepState.DROWSY:
            self.manager.update_activity(True)
            return HandlerResult(True, True, None)
        
        if state == SleepState.SLEEPING:
            # 累计唤醒度
            new_val, just_woken = self.manager.add_wake_value(session_id)
            
            if self.manager.is_woken(session_id):
                if just_woken:
                    logger.info(f"[SleepInterceptor] Notice事件吵醒了 {session_id}")
                return HandlerResult(True, True, None)
            else:
                logger.info(f"[SleepInterceptor] Notice事件未达唤醒阈值，拦截")
                return HandlerResult(True, False, None)
        
        return HandlerResult(True, True, None)