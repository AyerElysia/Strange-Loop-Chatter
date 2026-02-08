"""发送处理器 - 将 MessageEnvelope 转换并发送到 Napcat"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any

from mofox_wire import GroupInfoPayload, MessageEnvelope, MessageInfoPayload, SegPayload, UserInfoPayload

from src.app.plugin_system.api.log_api import get_logger

from ...event_models import CommandType
from ..utils import convert_image_to_gif, get_image_format

logger = get_logger("napcat_adapter")

if TYPE_CHECKING:
    from ....plugin import NapcatAdapter


class SendHandler:
    """负责向 Napcat 发送消息"""

    def __init__(self, adapter: "NapcatAdapter"):
        self.adapter = adapter

    async def handle_message(self, envelope: MessageEnvelope) -> None:
        """
        处理来自核心的消息，将其转换为 Napcat 可接受的格式并发送
        """
        logger.debug("接收到来自MoFox-Bot的消息，处理中")

        if not envelope:
            logger.warning("空的消息，跳过处理")
            return

        message_segment = envelope.get("message_segment")
        if isinstance(message_segment, list):
            segment: SegPayload = {"type": "seglist", "data": message_segment}
        else:
            segment = message_segment or {}

        if segment:
            seg_type = segment.get("type")

            if seg_type == "command":
                logger.debug("处理命令")
                return await self.send_command(envelope)
            if seg_type == "adapter_command":
                logger.debug("处理适配器命令")
                return await self.handle_adapter_command(envelope)
            if seg_type == "adapter_response":
                logger.debug("收到adapter_response消息，此消息应该由Bot端处理，跳过")
                return None

        return await self.send_normal_message(envelope)

    async def send_normal_message(self, envelope: MessageEnvelope) -> None:
        """
        处理普通消息发送
        """
        message_info: MessageInfoPayload = envelope.get("message_info", {})
        message_segment: SegPayload = envelope.get("message_segment", {})  # type: ignore[assignment]

        if isinstance(message_segment, list):
            seg_data: SegPayload = {"type": "seglist", "data": message_segment}
        else:
            seg_data = message_segment

        group_info: GroupInfoPayload | None = message_info.get("group_info")
        user_info: UserInfoPayload | None = message_info.get("user_info")
        target_id: int | None = None
        action: str | None = None
        id_name: str | None = None
        processed_message: list = []
        try:
            processed_message = await self.handle_seg_recursive(seg_data, user_info or {})
        except Exception as e:
            logger.error(f"处理消息时发生错误: {e}")
            return None

        if not processed_message:
            logger.critical("现在暂时不支持解析此回复！")
            return None

        # 🔧 确保 reply 消息段始终在列表最前面
        # 排序原则：reply 类型优先级最高（排序值为 0），其他类型保持原有顺序（排序值为 1）
        # 使用 stable sort 确保非 reply 元素的相对顺序不变
        processed_message.sort(key=lambda seg: 0 if isinstance(seg, dict) and seg.get("type") == "reply" else 1)

        if group_info and group_info.get("group_id"):
            logger.debug("发送群聊消息")
            target_id = int(group_info["group_id"])
            action = "send_group_msg"
            id_name = "group_id"
        elif user_info and user_info.get("user_id"):
            logger.debug("发送私聊消息")
            target_id = int(user_info["user_id"])
            action = "send_private_msg"
            id_name = "user_id"
        else:
            logger.error("无法识别的消息类型")
            return
        logger.debug(
            f"准备发送到napcat的消息体: action='{action}', {id_name}='{target_id}', message='{processed_message}'"
        )
        response = await self.send_message_to_napcat(
            action or "",
            {
                id_name or "target_id": target_id,
                "message": processed_message,
            },
        )
        if response.get("status") == "ok":
            logger.info("消息发送成功")
        else:
            logger.warning(f"消息发送失败，napcat返回：{response!s}")

    async def send_command(self, envelope: MessageEnvelope) -> None:
        """
        处理命令类
        """
        logger.debug("处理命令中")
        message_info: dict[str, Any] = envelope.get("message_info", {})
        group_info: dict[str, Any] | None = message_info.get("group_info")
        segment: SegPayload = envelope.get("message_segment", {})  # type: ignore[assignment]
        seg_data: dict[str, Any] = segment.get("data", {}) if isinstance(segment, dict) else {}
        command_name: str | None = seg_data.get("name")
        try:
            args = seg_data.get("args", {})
            if not isinstance(args, dict):
                args = {}

            if command_name == CommandType.GROUP_BAN.name:
                command, args_dict = self.handle_ban_command(args, group_info)
            elif command_name == CommandType.GROUP_WHOLE_BAN.name:
                command, args_dict = self.handle_whole_ban_command(args, group_info)
            elif command_name == CommandType.GROUP_KICK.name:
                command, args_dict = self.handle_kick_command(args, group_info)
            elif command_name == CommandType.SEND_POKE.name:
                command, args_dict = self.handle_poke_command(args, group_info)
            elif command_name == CommandType.DELETE_MSG.name:
                command, args_dict = self.delete_msg_command(args)
            elif command_name == CommandType.AI_VOICE_SEND.name:
                command, args_dict = self.handle_ai_voice_send_command(args, group_info)
            elif command_name == CommandType.SET_EMOJI_LIKE.name:
                command, args_dict = self.handle_set_emoji_like_command(args)
            elif command_name == CommandType.SEND_AT_MESSAGE.name:
                command, args_dict = self.handle_at_message_command(args, group_info)
            elif command_name == CommandType.SEND_LIKE.name:
                command, args_dict = self.handle_send_like_command(args)
            else:
                logger.error(f"未知命令: {command_name}")
                return
        except Exception as e:
            logger.error(f"处理命令时发生错误: {e}")
            return None

        if not command or not args_dict:
            logger.error("命令或参数缺失")
            return None

        logger.debug(f"准备向 Napcat 发送命令: command='{command}', args_dict='{args_dict}'")
        response = await self.send_message_to_napcat(command, args_dict)
        logger.debug(f"收到 Napcat 的命令响应: {response}")

        if response.get("status") == "ok":
            logger.info(f"命令 {command_name} 执行成功")
        else:
            logger.warning(f"命令 {command_name} 执行失败，napcat返回：{response!s}")

    async def handle_adapter_command(self, envelope: MessageEnvelope) -> None:
        """
        处理适配器命令类 - 用于直接向Napcat发送命令并返回结果
        """
        logger.info("处理适配器命令中")
        segment: SegPayload = envelope.get("message_segment", {})  # type: ignore[assignment]
        seg_data: dict[str, Any] = segment.get("data", {}) if isinstance(segment, dict) else {}

        try:
            action = seg_data.get("action")
            params = seg_data.get("params", {})
            request_id = seg_data.get("request_id")
            timeout = float(seg_data.get("timeout", 20.0))

            if not action:
                logger.error("适配器命令缺少action参数")
                return

            logger.debug(f"执行适配器命令: {action}")

            if action == "get_cookies":
                response = await self.send_message_to_napcat(action, params, timeout=40.0)
            else:
                response = await self.send_message_to_napcat(action, params, timeout=timeout)

            try:
                from src.plugin_system.apis.send_api import put_adapter_response

                if request_id:
                    put_adapter_response(str(request_id), response)
            except Exception as e:
                logger.debug(f"回填 adapter 响应失败: {e}")

            if response.get("status") == "ok":
                logger.info(f"适配器命令 {action} 执行成功")
            else:
                logger.warning(f"适配器命令 {action} 执行失败，napcat返回：{response!s}")
            logger.debug(f"适配器命令 {action} 的完整响应: {response}")

        except Exception as e:
            logger.error(f"处理适配器命令时发生错误: {e}")

    def get_level(self, seg_data: SegPayload) -> int:
        if seg_data.get("type") == "seglist":
            return 1 + max(self.get_level(seg) for seg in seg_data.get("data", []) if isinstance(seg, dict))
        return 1

    async def handle_seg_recursive(self, seg_data: SegPayload, user_info: UserInfoPayload) -> list:
        payload: list = []
        if seg_data.get("type") == "seglist":
            if not seg_data.get("data"):
                return []
            for seg in seg_data["data"]:
                if not isinstance(seg, dict):
                    continue
                payload = await self.process_message_by_type(seg, payload, user_info)
        else:
            payload = await self.process_message_by_type(seg_data, payload, user_info)
        return payload

    async def process_message_by_type(self, seg: SegPayload, payload: list, user_info: UserInfoPayload) -> list:
        new_payload = payload
        seg_type = seg.get("type")
        if seg_type == "reply":
            target_id = seg.get("data")
            target_id = str(target_id)
            if target_id == "notice":
                return payload
            new_payload = self.build_payload(payload, await self.handle_reply_message(target_id, user_info), True)
        elif seg_type == "text":
            text = seg.get("data")
            if not text:
                return payload
            new_payload = self.build_payload(payload, self.handle_text_message(str(text)), False)
        elif seg_type == "face":
            logger.warning("MoFox-Bot 发送了qq原生表情，暂时不支持")
        elif seg_type == "image":
            image = seg.get("data")
            new_payload = self.build_payload(payload, self.handle_image_message(str(image)), False)
        elif seg_type == "emoji":
            emoji = seg.get("data")
            new_payload = self.build_payload(payload, self.handle_emoji_message(str(emoji)), False)
        elif seg_type == "voice":
            voice = seg.get("data")
            new_payload = self.build_payload(payload, self.handle_voice_message(str(voice)), False)
        elif seg_type == "voiceurl":
            voice_url = seg.get("data")
            new_payload = self.build_payload(payload, self.handle_voiceurl_message(str(voice_url)), False)
        elif seg_type == "music":
            song_id = seg.get("data")
            new_payload = self.build_payload(payload, self.handle_music_message(str(song_id)), False)
        elif seg_type == "videourl":
            video_url = seg.get("data")
            new_payload = self.build_payload(payload, self.handle_videourl_message(str(video_url)), False)
        elif seg_type == "file":
            file_path = seg.get("data")
            new_payload = self.build_payload(payload, self.handle_file_message(str(file_path)), False)
        elif seg_type == "seglist":
            # 嵌套列表继续递归
            nested_payload: list = []
            for sub_seg in seg.get("data", []):
                if not isinstance(sub_seg, dict):
                    continue
                nested_payload = await self.process_message_by_type(sub_seg, nested_payload, user_info)
            new_payload = self.build_payload(payload, nested_payload, False)
        return new_payload

    def build_payload(self, payload: list, addon: dict | list, is_reply: bool = False) -> list:
        """构建发送的消息体"""
        if is_reply:
            temp_list = []
            if isinstance(addon, list):
                temp_list.extend(addon)
            else:
                temp_list.append(addon)
            for i in payload:
                if isinstance(i, dict) and i.get("type") == "reply":
                    logger.debug("检测到多个回复，使用最新的回复")
                    continue
                temp_list.append(i)
            return temp_list

        if isinstance(addon, list):
            payload.extend(addon)
        else:
            payload.append(addon)
        return payload

    async def handle_reply_message(self, message_id: str, user_info: UserInfoPayload) -> dict | list:
        """处理回复消息"""
        logger.debug(f"开始处理回复消息，消息ID: {message_id}")
        reply_seg = {"type": "reply", "data": {"id": message_id}}

        # 检查是否启用引用艾特功能
        enable_reply_at = False
        reply_at_rate = 0.5
        if self.adapter.plugin and self.adapter.plugin.config:
            enable_reply_at = self.adapter.plugin.config.features.enable_reply_at
            reply_at_rate = self.adapter.plugin.config.features.reply_at_rate

        if not enable_reply_at:
            logger.info("引用艾特功能未启用，仅发送普通回复")
            return reply_seg

        try:
            msg_info_response = await self.send_message_to_napcat("get_msg", {"message_id": message_id})
            logger.debug(f"获取消息 {message_id} 的详情响应: {msg_info_response}")

            replied_user_id = None
            if msg_info_response and msg_info_response.get("status") == "ok":
                sender_info = msg_info_response.get("data", {}).get("sender")
                if sender_info:
                    replied_user_id = sender_info.get("user_id")

            if not replied_user_id:
                logger.warning(f"无法获取消息 {message_id} 的发送者信息，跳过 @")
                logger.debug(f"最终返回的回复段: {reply_seg}")
                return reply_seg

            if random.random() < reply_at_rate:
                at_seg = {"type": "at", "data": {"qq": str(replied_user_id)}}
                text_seg = {"type": "text", "data": {"text": " "}}
                result_seg = [reply_seg, at_seg, text_seg]
                logger.debug(f"最终返回的回复段: {result_seg}")
                return result_seg

        except Exception as e:
            logger.error(f"处理引用回复并尝试@时出错: {e}")
            logger.debug(f"最终返回的回复段: {reply_seg}")
            return reply_seg

        logger.debug(f"最终返回的回复段: {reply_seg}")
        return reply_seg

    def handle_text_message(self, message: str) -> dict:
        """处理文本消息"""
        return {"type": "text", "data": {"text": message}}

    def handle_image_message(self, encoded_image: str) -> dict:
        """处理图片消息"""
        return {
            "type": "image",
            "data": {
                "file": f"base64://{encoded_image}",
                "subtype": 0,
            },
        }

    def handle_emoji_message(self, encoded_emoji: str) -> dict:
        """处理表情消息"""
        encoded_image = encoded_emoji
        image_format = get_image_format(encoded_emoji)
        if image_format != "gif":
            encoded_image = convert_image_to_gif(encoded_emoji)
        return {
            "type": "image",
            "data": {
                "file": f"base64://{encoded_image}",
                "subtype": 1,
                "summary": "[动画表情]",
            },
        }

    def handle_voice_message(self, encoded_voice: str) -> dict:
        """处理语音消息"""
        if not encoded_voice:
            logger.warning("接收到空的语音消息，跳过处理")
            return {}
        return {
            "type": "record",
            "data": {"file": f"base64://{encoded_voice}"},
        }

    def handle_voiceurl_message(self, voice_url: str) -> dict:
        """处理语音链接消息"""
        return {
            "type": "record",
            "data": {"file": voice_url},
        }

    def handle_music_message(self, song_id: str) -> dict:
        """处理音乐消息"""
        return {
            "type": "music",
            "data": {"type": "163", "id": song_id},
        }

    def handle_videourl_message(self, video_url: str) -> dict:
        """处理视频链接消息"""
        return {
            "type": "video",
            "data": {"file": video_url},
        }

    def handle_file_message(self, file_path: str) -> dict:
        """处理文件消息"""
        return {
            "type": "file",
            "data": {"file": f"file://{file_path}"},
        }

    def delete_msg_command(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """处理删除消息命令"""
        return "delete_msg", {"message_id": args["message_id"]}

    def handle_ban_command(self, args: dict[str, Any], group_info: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """处理封禁命令"""
        duration: int = int(args["duration"])
        user_id: int = int(args["qq_id"])
        group_id: int = int(group_info["group_id"]) if group_info and group_info.get("group_id") else 0
        if duration < 0:
            raise ValueError("封禁时间必须大于等于0")
        if not user_id or not group_id:
            raise ValueError("封禁命令缺少必要参数")
        if duration > 2592000:
            raise ValueError("封禁时间不能超过30天")
        return (
            CommandType.GROUP_BAN.value,
            {
                "group_id": group_id,
                "user_id": user_id,
                "duration": duration,
            },
        )

    def handle_whole_ban_command(self, args: dict[str, Any], group_info: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """处理全体禁言命令"""
        enable = args["enable"]
        assert isinstance(enable, bool), "enable参数必须是布尔值"
        group_id: int = int(group_info["group_id"]) if group_info and group_info.get("group_id") else 0
        if group_id <= 0:
            raise ValueError("群组ID无效")
        return (
            CommandType.GROUP_WHOLE_BAN.value,
            {
                "group_id": group_id,
                "enable": enable,
            },
        )

    def handle_kick_command(self, args: dict[str, Any], group_info: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """处理群成员踢出命令"""
        user_id: int = int(args["qq_id"])
        group_id: int = int(group_info["group_id"]) if group_info and group_info.get("group_id") else 0
        if group_id <= 0:
            raise ValueError("群组ID无效")
        if user_id <= 0:
            raise ValueError("用户ID无效")
        return (
            CommandType.GROUP_KICK.value,
            {
                "group_id": group_id,
                "user_id": user_id,
                "reject_add_request": False,
            },
        )

    def handle_poke_command(self, args: dict[str, Any], group_info: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """处理戳一戳命令"""
        user_id: int = int(args["qq_id"])
        group_id: int | None = None
        if group_info and group_info.get("group_id"):
            group_id = int(group_info["group_id"])
            if group_id <= 0:
                raise ValueError("群组ID无效")
        if user_id <= 0:
            raise ValueError("用户ID无效")
        return (
            CommandType.SEND_POKE.value,
            {
                "group_id": group_id,
                "user_id": user_id,
            },
        )

    def handle_set_emoji_like_command(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """处理设置表情回应命令"""
        try:
            message_id = int(args["message_id"])
            emoji_id = int(args["emoji_id"])
            set_like = bool(args["set"])
        except (KeyError, ValueError) as e:
            logger.error(f"处理表情回应命令时发生错误: {e}, 原始参数: {args}")
            raise ValueError(f"缺少必需参数或参数类型错误: {e}")

        return (
            CommandType.SET_EMOJI_LIKE.value,
            {"message_id": message_id, "emoji_id": emoji_id, "set": set_like},
        )

    def handle_send_like_command(self, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """处理发送点赞命令的逻辑。"""
        try:
            user_id: int = int(args["qq_id"])
            times: int = int(args["times"])
        except (KeyError, ValueError):
            raise ValueError("缺少必需参数: qq_id 或 times")

        return (
            CommandType.SEND_LIKE.value,
            {"user_id": user_id, "times": times},
        )

    def handle_at_message_command(self, args: dict[str, Any], group_info: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """处理艾特并发送消息命令"""
        at_user_id = args.get("qq_id")
        text = args.get("text")

        if not at_user_id or not text:
            raise ValueError("艾特消息命令缺少 qq_id 或 text 参数")

        if not group_info or not group_info.get("group_id"):
            raise ValueError("艾特消息命令必须在群聊上下文中使用")

        message_payload = [
            {"type": "at", "data": {"qq": str(at_user_id)}},
            {"type": "text", "data": {"text": " " + str(text)}},
        ]

        return (
            "send_group_msg",
            {
                "group_id": group_info["group_id"],
                "message": message_payload,
            },
        )

    def handle_ai_voice_send_command(self, args: dict[str, Any], group_info: dict[str, Any] | None) -> tuple[str, dict[str, Any]]:
        """
        处理AI语音发送命令的逻辑。
        并返回 NapCat 兼容的 (action, params) 元组。
        """
        if not group_info or not group_info.get("group_id"):
            raise ValueError("AI语音发送命令必须在群聊上下文中使用")
        if not args:
            raise ValueError("AI语音发送命令缺少参数")

        group_id: int = int(group_info["group_id"])
        character_id = args.get("character")
        text_content = args.get("text")

        if not character_id or not text_content:
            raise ValueError(f"AI语音发送命令参数不完整: character='{character_id}', text='{text_content}'")

        return (
            CommandType.AI_VOICE_SEND.value,
            {
                "group_id": group_id,
                "text": text_content,
                "character": character_id,
            },
        )

    async def send_message_to_napcat(self, action: str, params: dict, timeout: float = 20.0) -> dict:
        """通过 adapter API 发送到 napcat"""
        try:
            response = await self.adapter.send_napcat_api(action, params, timeout=timeout)
            return response or {"status": "error", "message": "no response"}
        except Exception as e:
            logger.error(f"发送消息失败: {e}")
            return {"status": "error", "message": str(e)}

