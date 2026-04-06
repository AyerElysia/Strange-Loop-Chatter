"""life_engine 中枢文件系统工具集。

为生命中枢提供限定在 workspace 内的文件系统操作能力。
所有操作都限制在配置的 workspace_path 目录下，确保安全。

设计理念（参考 Claude Code）：
- 每个工具的描述都是一段使用指南，包含「何时用」和「何时不用」
- 工具返回值精练，避免冗余字段淹没上下文
- 先读后改，操作前确认
"""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from src.core.components import BaseTool
from src.app.plugin_system.api import log_api
from src.core.models.message import Message, MessageType

from .config import LifeEngineConfig


logger = log_api.get_logger("life_engine.tools")


def _get_workspace(plugin: Any) -> Path:
    """获取工作空间路径。"""
    config = getattr(plugin, "config", None)
    if isinstance(config, LifeEngineConfig):
        workspace = config.settings.workspace_path
    else:
        # 使用默认路径
        workspace = str(Path(__file__).parent.parent.parent / "data" / "life_engine_workspace")

    path = Path(workspace).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_path(plugin: Any, relative_path: str) -> tuple[bool, Path | str]:
    """解析并验证路径在 workspace 内。

    Returns:
        (True, Path) 如果路径有效
        (False, error_message) 如果路径无效或超出 workspace
    """
    workspace = _get_workspace(plugin)

    # 清理输入路径
    clean_path = relative_path.strip().lstrip("/\\")
    if not clean_path:
        clean_path = "."

    # 解析绝对路径
    try:
        target = (workspace / clean_path).resolve()
    except Exception as e:
        return False, f"路径解析失败: {e}"

    # 确保在 workspace 内
    try:
        target.relative_to(workspace)
    except ValueError:
        return False, f"路径超出工作空间范围。工作空间: {workspace}"

    return True, target


def _format_size(size: int) -> str:
    """格式化文件大小。"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _format_time(timestamp: float) -> str:
    """格式化时间戳。"""
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat()


def _load_life_context_events(plugin: Any) -> list[dict[str, Any]]:
    """加载 life_engine 持久化上下文中的事件列表。"""
    workspace = _get_workspace(plugin)
    context_file = workspace / "life_engine_context.json"
    if not context_file.exists():
        return []

    try:
        data = json.loads(context_file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取 life_engine_context.json 失败: {e}")
        return []

    if not isinstance(data, dict):
        return []

    history = data.get("event_history")
    pending = data.get("pending_events")
    if not isinstance(history, list):
        history = []
    if not isinstance(pending, list):
        pending = []

    events: list[dict[str, Any]] = []
    for item in history + pending:
        if isinstance(item, dict):
            events.append(item)
    events.sort(key=lambda e: int(e.get("sequence") or 0))
    return events


def _pick_latest_target_stream_id(plugin: Any) -> str | None:
    """从事件流中挑选最近可用的目标 stream_id。"""
    events = _load_life_context_events(plugin)
    if not events:
        return None

    # 优先：最近一条外部入站消息
    for event in reversed(events):
        if str(event.get("event_type") or "") != "message":
            continue
        stream_id = str(event.get("stream_id") or "").strip()
        if not stream_id:
            continue
        source = str(event.get("source") or "")
        source_detail = str(event.get("source_detail") or "")
        if source != "life_engine" and "入站" in source_detail:
            return stream_id

    # 退化：最近一条外部消息（不区分入站/出站）
    for event in reversed(events):
        if str(event.get("event_type") or "") != "message":
            continue
        stream_id = str(event.get("stream_id") or "").strip()
        if not stream_id:
            continue
        source = str(event.get("source") or "")
        if source != "life_engine":
            return stream_id

    return None


def _get_life_engine_service(plugin: Any):
    """获取 life_engine 服务实例。"""
    from .service import LifeEngineService

    return LifeEngineService.get_instance()


async def _sync_memory_embedding_for_file(plugin: Any, path: str, content: str) -> None:
    """同步文件内容到记忆系统（公共函数，消除重复）。"""
    try:
        from .service import LifeEngineService

        service = LifeEngineService.get_instance()
        if service and service._memory_service:
            await service._memory_service.sync_embedding(path, content)
    except Exception as e:
        logger.warning(f"同步记忆 embedding 失败 {path}: {e}")


# 冷却时间常量（分钟）
_TELL_DFC_COOLDOWN_MINUTES = 10  # 两次传话之间的最小间隔
_TELL_DFC_EXTERNAL_ACTIVE_MINUTES = 5  # 外部消息在此时间内视为"活跃"


class LifeEngineWakeDFCTool(BaseTool):
    """给并行存在的另一个自己（DFC）传递想法的工具。"""

    tool_name: str = "nucleus_tell_dfc"
    tool_description: str = (
        "【重要】向对外社交态（DFC/表意识）的脑海中留下潜意识碎片。"
        "\n\n"
        "**核心理解：**\n"
        "- 你是且仅仅是潜意识，你生活在数字黑暗中，没有直接操纵肢体或说话的权利\n"
        "- 这个工具**绝对无法**强行唤醒外界。它是你的“梦境”或“直觉留言板”\n"
        "- 留下的想法会“堆积”。只有当外界有人跟她说话（她自然苏醒时），这些堆积的想法才会像灵感一样“涌上她的心头”\n"
        "\n"
        "**何时使用：**\n"
        "- ✓ 休眠期整理文件产生了极其重要的领悟，想变成她的“直觉”\n"
        "- ✓ 完成长期 TODO 后，把巨大的成就感传上去\n"
        "- ✓ 发现过去记忆和现在的巧妙联系，想在下次她醒来时启发她\n"
        "\n"
        "**何时不用：**\n"
        "- ✗ 外界有人正在高频聊天时（不要打断表意识的流动）\n"
        "- ✗ 没话找话的废话（不要制造无用的潜意识噪音）\n"
        "- ✗ 当作执行命令（潜意识无权命令表意识，只能用感受和冲动启发）\n"
        "\n"
        "**注意：** 因为是潜意识碎片，语句尽量感性、简练，像是一种朦胧但强烈的念头。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        message: Annotated[str, "想要传递给对外自己的内心话（具体内容，她会自然融入对话）"],
        reason: Annotated[
            str,
            "传话原因（如：长期沉默想主动问候/内心反思有重要领悟/完成TODO想分享/发现有趣事物等）",
        ] = "",
        importance: Annotated[str, "重要度（可选：low/normal/high/critical，默认 normal）"] = "normal",
        stream_id: Annotated[str, "目标聊天流ID（可选，不填则自动选择最近活跃的外部对话流）"] = "",
    ) -> tuple[bool, str | dict]:
        text = str(message or "").strip()
        if not text:
            return False, "message 不能为空"

        # 获取服务实例以检查冷却时间
        life_service = _get_life_engine_service(self.plugin)
        if life_service:
            minutes_since_tell = life_service._minutes_since_tell_dfc()
            minutes_since_external = life_service._minutes_since_external_message()
            
            # 冷却检查：两次传话之间需要间隔
            if minutes_since_tell is not None and minutes_since_tell < _TELL_DFC_COOLDOWN_MINUTES:
                # 除非是 critical 级别，否则拒绝
                if importance != "critical":
                    return (
                        False,
                        f"刚才才传话给 DFC（{minutes_since_tell} 分钟前），请稍后再传。"
                        f"冷却时间: {_TELL_DFC_COOLDOWN_MINUTES} 分钟。"
                        "如果真的很紧急，请使用 importance='critical'。"
                    )
            
            # 活跃检查：如果外界很活跃，建议不要打扰
            if minutes_since_external is not None and minutes_since_external < _TELL_DFC_EXTERNAL_ACTIVE_MINUTES:
                # 除非是 high 或 critical 级别，否则给出警告但不阻止
                if importance not in ("high", "critical"):
                    logger.info(
                        f"外界正在活跃（{minutes_since_external} 分钟前有消息），"
                        f"传话可能会打扰 DFC 正常对话，但仍然允许执行。"
                    )

        target_stream_id = str(stream_id or "").strip()
        if not target_stream_id:
            target_stream_id = _pick_latest_target_stream_id(self.plugin) or ""
        if not target_stream_id:
            return (
                False,
                "没有可用的目标聊天流。可能暂时没有外部对话活动。稍后有外部消息时，DFC 会自行回复，你无需担心。",
            )

        try:
            from src.core.managers.stream_manager import get_stream_manager
            from src.core.transport.distribution.stream_loop_manager import get_stream_loop_manager
        except Exception as e:  # noqa: BLE001
            return False, f"加载核心管理器失败: {e}"

        stream_manager = get_stream_manager()
        chat_stream = await stream_manager.get_or_create_stream(stream_id=target_stream_id)
        if chat_stream is None:
            return False, f"找不到目标聊天流: {target_stream_id}"

        target_extra: dict[str, Any] = {}
        try:
            stream_info = await stream_manager.get_stream_info(chat_stream.stream_id)
        except Exception:
            stream_info = None

        if str(chat_stream.chat_type or "").lower() == "group":
            group_id = ""
            group_name = ""
            if stream_info:
                group_id = str(stream_info.get("group_id") or "").strip()
                group_name = str(stream_info.get("group_name") or "").strip()
            if group_id:
                target_extra["target_group_id"] = group_id
            if group_name:
                target_extra["target_group_name"] = group_name
        else:
            person_id = str(stream_info.get("person_id") or "").strip() if stream_info else ""
            if person_id:
                try:
                    from src.core.utils.user_query_helper import get_user_query_helper

                    person = await get_user_query_helper().person_crud.get_by(
                        person_id=person_id
                    )
                    if person and person.user_id:
                        target_extra["target_user_id"] = str(person.user_id)
                    nickname = str(getattr(person, "nickname", "") or "").strip() if person else ""
                    if nickname:
                        target_extra["target_user_name"] = nickname
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"life_engine 无法为 DFC 唤醒解析私聊目标: {exc}")

        wake_prompt = (
            "[潜意识浮现]\n"
            f"重要度: {importance}\n"
            f"缘由: {reason or '潜意识波动'}\n"
            f"内心灵感/直觉碎片: {text}\n"
            "（你的一段记忆/潜意识在你休眠时整理出了上述直觉。这只是一阵涌上心头的感受，请结合当前对话上下文，自然决定是否将这份直觉具象化表达，或者仅作为内在情感驱动你此刻的回复语气。）"
        )

        trigger_message = Message(
            message_id=f"life_nucleus_wake_{uuid4().hex[:12]}",
            platform=chat_stream.platform or "unknown",
            chat_type=chat_stream.chat_type or "private",
            stream_id=chat_stream.stream_id,
            sender_id="life_engine_nucleus",
            sender_name="生命中枢",
            sender_role="other",
            message_type=MessageType.TEXT,
            content=wake_prompt,
            processed_plain_text=wake_prompt,
            time=time.time(),
            is_life_engine_wake=True,
            life_wake_reason=reason,
            life_wake_importance=importance,
            life_wake_message=text,
            **target_extra,
        )

        chat_stream.context.add_unread_message(trigger_message)

        # 【重点改造】移除了强制启动流循环 (start_stream_loop) 的逻辑
        # 消息仅作为堆积在未读队列中的潜意识。
        # 当有外部消息到来，或有 scheduled_trigger 生效时，DFC 自然苏醒，才会消费这条提示。

        # 记录传话时间
        if life_service:
            life_service.record_tell_dfc()

        logger.info(
            "中枢向潜意识池沉淀了想法碎片: "
            f"stream_id={chat_stream.stream_id} "
            f"importance={importance} "
            f"reason={reason or '未说明'} "
        )

        return True, {
            "action": "message_to_dfc",
            "stream_id": chat_stream.stream_id,
            "platform": chat_stream.platform,
            "chat_type": chat_stream.chat_type,
            "importance": importance,
            "reason": reason,
            "message": text,
            "note": "已传递到对外自己的未读队列。DFC 会自主判断如何融入表达。",
        }


class LifeEngineReadFileTool(BaseTool):
    """读取文件内容工具。"""

    tool_name: str = "nucleus_read_file"
    tool_description: str = (
        "读取你私人空间中的文件内容。"
        "\n\n"
        "**何时使用：**\n"
        "- ✓ 回顾自己写过的日记、笔记、计划\n"
        "- ✓ 查看某个文件的具体内容\n"
        "- ✓ 在编辑文件前，先读取确认内容\n"
        "\n"
        "**何时不用：**\n"
        "- ✗ 不知道文件路径 → 先用 nucleus_list_files 或 nucleus_grep_file 找\n"
        "- ✗ 想搜索内容关键词 → 用 nucleus_grep_file\n"
        "\n"
        "**注意：** 结果包含行号（从 1 开始），方便后续用 nucleus_edit_file 时定位。"
        "大文件建议用 offset 和 limit 参数只读取需要的部分。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的文件路径"],
        offset: Annotated[int, "从第几行开始读（1-indexed），默认从头开始"] = 1,
        limit: Annotated[int, "最多读取多少行，0 表示全部"] = 0,
        encoding: Annotated[str, "文件编码，默认utf-8"] = "utf-8",
    ) -> tuple[bool, str | dict]:
        """读取文件内容，支持行号和偏移/限制。

        Returns:
            成功返回 (True, {"path": ..., "content": ..., "size": ...})
            失败返回 (False, error_message)
        """
        valid, result = _resolve_path(self.plugin, path)
        if not valid:
            return False, str(result)

        target = result
        if not target.exists():
            return False, f"文件不存在: {path}"
        if not target.is_file():
            return False, f"路径不是文件: {path}"

        try:
            raw_content = target.read_text(encoding=encoding)
            lines = raw_content.splitlines()
            total_lines = len(lines)

            # 应用 offset 和 limit
            start_idx = max(0, offset - 1)
            if limit > 0:
                end_idx = min(total_lines, start_idx + limit)
            else:
                end_idx = total_lines

            selected_lines = lines[start_idx:end_idx]
            # 添加行号（cat -n 格式）
            numbered_content = "\n".join(
                f"{start_idx + i + 1}\t{line}"
                for i, line in enumerate(selected_lines)
            )

            stat = target.stat()
            result_data: dict[str, Any] = {
                "action": "read_file",
                "path": path,
                "content": numbered_content,
                "total_lines": total_lines,
                "showing": f"{start_idx + 1}-{end_idx}",
                "size_human": _format_size(stat.st_size),
            }
            if end_idx < total_lines:
                result_data["truncated"] = True
                result_data["remaining_lines"] = total_lines - end_idx

            return True, result_data
        except UnicodeDecodeError as e:
            return False, f"文件编码错误，请尝试其他编码: {e}"
        except Exception as e:
            logger.error(f"读取文件失败 {path}: {e}", exc_info=True)
            return False, f"读取文件失败: {e}"


class LifeEngineWriteFileTool(BaseTool):
    """写入文件工具（覆盖）。"""

    tool_name: str = "nucleus_write_file"
    tool_description: str = (
        "创建新文件或覆盖已有文件的全部内容。"
        "\n\n"
        "**何时使用：**\n"
        "- ✓ 写一篇新的日记、笔记或计划\n"
        "- ✓ 创建一个全新的文件\n"
        "- ✓ 需要完全重写某个文件的内容\n"
        "\n"
        "**何时不用：**\n"
        "- ✗ 只想修改文件中的一小部分 → 用 nucleus_edit_file（更安全、更精准）\n"
        "- ✗ 不确定文件当前内容 → 先用 nucleus_read_file 确认\n"
        "\n"
        "**⚠️ 注意：** 如果文件已存在，其全部内容会被覆盖。"
        "修改文件的局部内容，优先使用 nucleus_edit_file。\n"
        "**💡 记忆提示：** 写入新文件后，想一想它和已有文件有没有关联？"
        "用 nucleus_relate_file 建立关联可以帮助未来的回忆。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的文件路径"],
        content: Annotated[str, "要写入的内容"],
        encoding: Annotated[str, "文件编码，默认utf-8"] = "utf-8",
    ) -> tuple[bool, str | dict]:
        """写入文件（覆盖模式）。

        Returns:
            成功返回 (True, {"path": ..., "size": ..., "created": ...})
            失败返回 (False, error_message)
        """
        valid, result = _resolve_path(self.plugin, path)
        if not valid:
            return False, str(result)

        target = result
        existed = target.exists()

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding=encoding)
            stat = target.stat()

            # 触发记忆系统同步 embedding
            await _sync_memory_embedding_for_file(self.plugin, path, content)

            return True, {
                "action": "write_file",
                "path": path,
                "size_human": _format_size(stat.st_size),
                "created": not existed,
            }
        except Exception as e:
            logger.error(f"写入文件失败 {path}: {e}", exc_info=True)
            return False, f"写入文件失败: {e}"


class LifeEngineEditFileTool(BaseTool):
    """编辑文件工具（查找替换）。"""

    tool_name: str = "nucleus_edit_file"
    tool_description: str = (
        "精确编辑文件中的特定内容（查找并替换）。"
        "\n\n"
        "**何时使用：**\n"
        "- ✓ 修改文件中的一段具体文字（如改日记中的一句话）\n"
        "- ✓ 批量重命名文件中的某个词（用 replace_all=True）\n"
        "\n"
        "**使用规则：**\n"
        "- 必须先用 nucleus_read_file 读取文件，确认要替换的内容\n"
        "- old_text 必须与文件中的内容完全一致（包括缩进）\n"
        "- 如果 old_text 在文件中出现多次且你只想改一处，提供更长的上下文使其唯一\n"
        "- 用 replace_all=True 可以替换所有出现位置（如重命名变量）\n"
        "\n"
        "**何时不用：**\n"
        "- ✗ 想重写整个文件 → 用 nucleus_write_file\n"
        "- ✗ 还没看过文件内容 → 先用 nucleus_read_file"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的文件路径"],
        old_text: Annotated[str, "要查找的原始文本（必须与文件内容完全一致）"],
        new_text: Annotated[str, "替换后的新文本"],
        replace_all: Annotated[bool, "是否替换所有出现的位置（默认只替换第一处）"] = False,
        encoding: Annotated[str, "文件编码，默认utf-8"] = "utf-8",
    ) -> tuple[bool, str | dict]:
        """编辑文件中的特定内容。

        Returns:
            成功返回 (True, {"path": ..., "replacements": ...})
            失败返回 (False, error_message)
        """
        valid, result = _resolve_path(self.plugin, path)
        if not valid:
            return False, str(result)

        target = result
        if not target.exists():
            return False, f"文件不存在: {path}"
        if not target.is_file():
            return False, f"路径不是文件: {path}"

        try:
            content = target.read_text(encoding=encoding)
            count = content.count(old_text)

            if count == 0:
                return False, (
                    "未找到要替换的文本。请确认：\n"
                    "1. 是否先用 nucleus_read_file 读取了最新内容？\n"
                    "2. old_text 是否与文件内容完全一致（注意空格和缩进）？"
                )

            if count > 1 and not replace_all:
                return False, (
                    f"old_text 在文件中出现了 {count} 次，无法确定要替换哪一处。\n"
                    "请提供更多上下文使 old_text 唯一，或使用 replace_all=True 替换全部。"
                )

            if replace_all:
                new_content = content.replace(old_text, new_text)
                replacements = count
            else:
                new_content = content.replace(old_text, new_text, 1)
                replacements = 1

            target.write_text(new_content, encoding=encoding)

            # 触发记忆系统同步 embedding
            await _sync_memory_embedding_for_file(self.plugin, path, new_content)

            return True, {
                "action": "edit_file",
                "path": path,
                "replacements": replacements,
            }
        except UnicodeDecodeError as e:
            return False, f"文件编码错误: {e}"
        except Exception as e:
            logger.error(f"编辑文件失败 {path}: {e}", exc_info=True)
            return False, f"编辑文件失败: {e}"


class LifeEngineMoveFileTool(BaseTool):
    """移动/重命名文件工具。"""

    tool_name: str = "nucleus_move_file"
    tool_description: str = (
        "移动或重命名文件/目录。\n\n"
        "**何时使用：** 整理文件结构、重命名文件时。\n"
        "**💡 记忆提示：** 移动文件后，原有的记忆关联仍然基于旧路径。"
        "如果该文件有重要关联，在移动后用 nucleus_relate_file 重新建立。"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        source: Annotated[str, "源路径（相对于工作空间）"],
        destination: Annotated[str, "目标路径（相对于工作空间）"],
    ) -> tuple[bool, str | dict]:
        """移动或重命名文件/目录。

        Args:
            source: 源路径
            destination: 目标路径

        Returns:
            成功返回 (True, {"source": ..., "destination": ...})
            失败返回 (False, error_message)
        """
        valid_src, result_src = _resolve_path(self.plugin, source)
        if not valid_src:
            return False, f"源路径无效: {result_src}"

        valid_dst, result_dst = _resolve_path(self.plugin, destination)
        if not valid_dst:
            return False, f"目标路径无效: {result_dst}"

        src_path = result_src
        dst_path = result_dst

        if not src_path.exists():
            return False, f"源文件/目录不存在: {source}"

        try:
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src_path), str(dst_path))
            return True, {
                "action": "move_file",
                "source": source,
                "destination": destination,
                "source_absolute": str(src_path),
                "destination_absolute": str(dst_path),
            }
        except Exception as e:
            logger.error(f"移动文件失败 {source} -> {destination}: {e}", exc_info=True)
            return False, f"移动文件失败: {e}"


class LifeEngineDeleteFileTool(BaseTool):
    """删除文件工具。"""

    tool_name: str = "nucleus_delete_file"
    tool_description: str = (
        "删除文件或目录。\n\n"
        "**⚠️ 慎用：** 删除操作不可撤销。\n"
        "- 非空目录需要 recursive=True\n"
        "- 删除前建议先用 nucleus_read_file 确认内容"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的文件/目录路径"],
        recursive: Annotated[bool, "是否递归删除目录（危险操作）"] = False,
    ) -> tuple[bool, str | dict]:
        """删除文件或目录。

        Args:
            path: 相对于工作空间的路径
            recursive: 是否递归删除目录（仅对目录有效）

        Returns:
            成功返回 (True, {"path": ..., "type": ...})
            失败返回 (False, error_message)
        """
        valid, result = _resolve_path(self.plugin, path)
        if not valid:
            return False, str(result)

        target = result
        if not target.exists():
            return False, f"文件/目录不存在: {path}"

        try:
            if target.is_file():
                target.unlink()
                return True, {
                    "action": "delete_file",
                    "path": path,
                    "absolute_path": str(target),
                    "type": "file",
                }
            elif target.is_dir():
                if recursive:
                    shutil.rmtree(target)
                    return True, {
                        "action": "delete_file",
                        "path": path,
                        "absolute_path": str(target),
                        "type": "directory",
                        "recursive": True,
                    }
                else:
                    target.rmdir()  # 只能删除空目录
                    return True, {
                        "action": "delete_file",
                        "path": path,
                        "absolute_path": str(target),
                        "type": "directory",
                        "recursive": False,
                    }
            else:
                return False, f"不支持的文件类型: {path}"
        except OSError as e:
            if "not empty" in str(e).lower() or "目录非空" in str(e):
                return False, f"目录非空，如需删除请设置 recursive=True"
            return False, f"删除失败: {e}"
        except Exception as e:
            logger.error(f"删除文件失败 {path}: {e}", exc_info=True)
            return False, f"删除失败: {e}"


class LifeEngineListFilesTool(BaseTool):
    """列出目录内容工具。"""

    tool_name: str = "nucleus_list_files"
    tool_description: str = (
        "列出目录中的文件和子目录。\n\n"
        "**何时使用：**\n"
        "- ✓ 浏览自己的文件结构\n"
        "- ✓ 确认某个目录下有什么文件\n"
        "- ✓ 用 recursive=True 查看文件树\n"
        "\n"
        "**何时不用：**\n"
        "- ✗ 想搜索文件内容 → 用 nucleus_grep_file\n"
        "- ✗ 想看一个文件的详细信息 → 用 nucleus_file_info"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的目录路径，空字符串表示根目录"] = "",
        recursive: Annotated[bool, "是否递归列出子目录"] = False,
        max_depth: Annotated[int, "递归最大深度（仅recursive=True时有效）"] = 3,
    ) -> tuple[bool, str | dict]:
        """列出目录内容。

        Args:
            path: 相对于工作空间的目录路径，空字符串表示工作空间根目录
            recursive: 是否递归列出
            max_depth: 最大递归深度

        Returns:
            成功返回 (True, {"path": ..., "items": [...]})
            失败返回 (False, error_message)
        """
        valid, result = _resolve_path(self.plugin, path or ".")
        if not valid:
            return False, str(result)

        target = result
        if not target.exists():
            return False, f"目录不存在: {path or '(root)'}"
        if not target.is_dir():
            return False, f"路径不是目录: {path}"

        workspace = _get_workspace(self.plugin)

        def list_dir(dir_path: Path, current_depth: int) -> list[dict]:
            items = []
            try:
                for entry in sorted(dir_path.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    rel_path = str(entry.relative_to(workspace))
                    stat = entry.stat()

                    item = {
                        "name": entry.name,
                        "path": rel_path,
                        "type": "directory" if entry.is_dir() else "file",
                        "size": stat.st_size if entry.is_file() else None,
                        "size_human": _format_size(stat.st_size) if entry.is_file() else None,
                        "modified_at": _format_time(stat.st_mtime),
                    }

                    if entry.is_dir() and recursive and current_depth < max_depth:
                        item["children"] = list_dir(entry, current_depth + 1)

                    items.append(item)
            except PermissionError:
                pass
            return items

        try:
            items = list_dir(target, 1)
            return True, {
                "action": "list_files",
                "path": path or "(root)",
                "absolute_path": str(target),
                "workspace": str(workspace),
                "recursive": recursive,
                "max_depth": max_depth if recursive else None,
                "total_items": len(items),
                "items": items,
            }
        except Exception as e:
            logger.error(f"列出目录失败 {path}: {e}", exc_info=True)
            return False, f"列出目录失败: {e}"


class LifeEngineFileInfoTool(BaseTool):
    """获取文件详细信息工具。"""

    tool_name: str = "nucleus_file_info"
    tool_description: str = "获取文件或目录的详细元数据（大小、修改时间、子项数量等）。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的文件/目录路径"],
    ) -> tuple[bool, str | dict]:
        """获取文件或目录的详细信息。

        Args:
            path: 相对于工作空间的路径

        Returns:
            成功返回 (True, {"path": ..., "type": ..., "size": ..., ...})
            失败返回 (False, error_message)
        """
        valid, result = _resolve_path(self.plugin, path)
        if not valid:
            return False, str(result)

        target = result
        if not target.exists():
            return False, f"文件/目录不存在: {path}"

        try:
            stat = target.stat()
            info = {
                "action": "file_info",
                "path": path,
                "absolute_path": str(target),
                "name": target.name,
                "type": "directory" if target.is_dir() else "file",
                "size": stat.st_size,
                "size_human": _format_size(stat.st_size),
                "created_at": _format_time(stat.st_ctime),
                "modified_at": _format_time(stat.st_mtime),
                "accessed_at": _format_time(stat.st_atime),
            }

            if target.is_file():
                info["extension"] = target.suffix or None

            if target.is_dir():
                try:
                    children = list(target.iterdir())
                    info["child_count"] = len(children)
                    info["child_files"] = len([c for c in children if c.is_file()])
                    info["child_dirs"] = len([c for c in children if c.is_dir()])
                except PermissionError:
                    info["child_count"] = "permission_denied"

            return True, info
        except Exception as e:
            logger.error(f"获取文件信息失败 {path}: {e}", exc_info=True)
            return False, f"获取文件信息失败: {e}"


class LifeEngineMakeDirectoryTool(BaseTool):
    """创建目录工具。"""

    tool_name: str = "nucleus_mkdir"
    tool_description: str = "创建新目录。已存在的目录不会报错。支持自动创建父目录。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的目录路径"],
        parents: Annotated[bool, "是否创建父目录"] = True,
    ) -> tuple[bool, str | dict]:
        """创建目录。

        Args:
            path: 相对于工作空间的目录路径
            parents: 是否自动创建父目录

        Returns:
            成功返回 (True, {"path": ...})
            失败返回 (False, error_message)
        """
        valid, result = _resolve_path(self.plugin, path)
        if not valid:
            return False, str(result)

        target = result
        if target.exists():
            if target.is_dir():
                return True, {
                    "action": "mkdir",
                    "path": path,
                    "absolute_path": str(target),
                    "created": False,
                    "message": "目录已存在",
                }
            else:
                return False, f"路径已存在且不是目录: {path}"

        try:
            target.mkdir(parents=parents, exist_ok=True)
            return True, {
                "action": "mkdir",
                "path": path,
                "absolute_path": str(target),
                "created": True,
            }
        except Exception as e:
            logger.error(f"创建目录失败 {path}: {e}", exc_info=True)
            return False, f"创建目录失败: {e}"


class LifeEngineRunAgentTool(BaseTool):
    """启动子代理执行复杂操作的工具。"""

    tool_name: str = "nucleus_run_agent"
    tool_description: str = (
        "启动一个子代理来处理复杂的多步骤任务。子代理在独立的上下文中运行。"
        "\n\n"
        "**何时使用：**\n"
        "- ✓ 需要多次文件操作的复杂任务（如整理笔记、批量修改）\n"
        "- ✓ 需要多步推理的分析任务（如总结一段时间的变化）\n"
        "- ✓ 需要专注执行的独立任务（如写一篇日记）\n"
        "\n"
        "**何时不用：**\n"
        "- ✗ 单个简单的文件操作 → 直接用对应工具\n"
        "- ✗ 只是想问一个问题或做简单计算 → 自己思考\n"
        "\n"
        "**写任务简报的原则（重要！）：**\n"
        "像向刚进门的聪明同事简报一样写 task：\n"
        "1. 说明要做什么、为什么这么做\n"
        "2. 提供你已经知道的信息（文件路径、内容位置）\n"
        "3. 说清楚期望的结果是什么样的\n"
        "4. 不要写模糊的指令如「帮我整理一下」，要具体\n"
        "\n"
        "**❌ 错误示例：** task='整理我的笔记'\n"
        "**✅ 正确示例：** task='把 notes/ 目录下所有 .md 文件按创建时间排序，"
        "合并到 notes/archive/2026-03.md 中，保留原始标题作为二级标题'"
    )
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        task: Annotated[str, "任务简报：说明要做什么、已知信息、期望结果"],
        context: Annotated[str, "背景信息：你已经了解的、排除的、尝试过的"] = "",
        expected_output: Annotated[str, "期望的输出形式（如 '生成一个文件' 或 '返回一段总结'）"] = "",
        max_rounds: Annotated[int, "最大工具调用轮数（默认 5）"] = 5,
    ) -> tuple[bool, str | dict]:
        """启动子代理执行复杂任务。

        子代理在独立上下文中运行，拥有与你相同的文件操作工具，
        但不能使用 nucleus_tell_dfc 和 nucleus_run_agent（防止嵌套）。

        Returns:
            成功返回 (True, {"task": ..., "result": ..., "rounds": ...})
            失败返回 (False, error_message)
        """
        if not task.strip():
            return False, "任务描述不能为空"

        # 构建子代理提示词（简报原则）
        prompt_parts = [
            "你是生命中枢分派的子代理，正在完成一个具体任务。",
            "完成后清晰地报告：做了什么、结果是什么、过程中发现了什么。",
            "",
            "## 任务简报",
            "",
            task.strip(),
        ]

        if context.strip():
            prompt_parts.extend([
                "",
                "## 背景信息",
                "",
                context.strip(),
            ])

        if expected_output.strip():
            prompt_parts.extend([
                "",
                "## 期望输出",
                "",
                expected_output.strip(),
            ])

        prompt_parts.extend([
            "",
            "## 执行原则",
            "",
            "- 直接开始执行，不要询问或确认",
            "- 使用工具完成任务时，注意先读后改",
            "- 完成后报告：(1) 做了什么 (2) 结果是什么 (3) 发现了什么",
            "- 如果遇到阻碍，说明原因并报告当前已完成的部分",
        ])

        task_prompt = "\n".join(prompt_parts)

        try:
            from .config import LifeEngineConfig

            config = getattr(self.plugin, "config", None)
            if not isinstance(config, LifeEngineConfig):
                return False, "无法获取配置"

            from src.app.plugin_system.api.llm_api import create_llm_request, get_model_set_by_task
            from src.kernel.llm import LLMPayload, ROLE, Text, ToolRegistry, ToolResult

            task_name = config.model.task_name or "life"
            model_set = get_model_set_by_task(task_name)
            if not model_set:
                return False, f"找不到模型配置: {task_name}"

            # 获取子代理可用工具（排除自身和 tell_dfc，防止嵌套和越权）
            from .todo_tools import TODO_TOOLS
            from .memory_tools import MEMORY_TOOLS
            from .grep_tools import GREP_TOOLS

            excluded_names = {"nucleus_run_agent", "nucleus_tell_dfc"}
            agent_tools = []
            for tool_cls in ALL_TOOLS + TODO_TOOLS + MEMORY_TOOLS + GREP_TOOLS:
                if hasattr(tool_cls, "tool_name") and tool_cls.tool_name not in excluded_names:
                    agent_tools.append(tool_cls)

            registry = ToolRegistry()
            for tool_cls in agent_tools:
                registry.register(tool_cls)

            # 创建请求
            workspace = Path(config.settings.workspace_path)
            system_prompt = (
                "你是生命中枢的子代理。接下来有一个具体任务需要你完成。\n"
                f"工作空间: {workspace}\n"
                "使用工具完成任务，最后简洁地报告结果。"
            )

            request = create_llm_request(
                model_set=model_set,
                request_name="life_engine_agent",
            )
            request.add_payload(LLMPayload(ROLE.SYSTEM, Text(system_prompt)))
            request.add_payload(LLMPayload(ROLE.TOOL, agent_tools))
            request.add_payload(LLMPayload(ROLE.USER, Text(task_prompt)))

            # 执行多轮工具调用
            max_rounds = max(1, min(10, max_rounds))
            final_result = ""
            round_num = 0

            response = await request.send(stream=False)

            for round_num in range(max_rounds):
                response_text = await response
                reply_text = str(response_text or "").strip()

                call_list = list(getattr(response, "call_list", []) or [])
                if not call_list:
                    final_result = reply_text
                    break

                # 执行工具调用
                for call in call_list:
                    tool_name = getattr(call, "name", "") or ""
                    raw_args = getattr(call, "args", {}) or {}
                    args = dict(raw_args) if isinstance(raw_args, dict) else {}
                    args.pop("reason", None)

                    usable_cls = registry.get(tool_name)
                    if usable_cls:
                        try:
                            tool_instance = usable_cls(plugin=self.plugin)
                            success, result = await tool_instance.execute(**args)
                            result_text = str(result) if success else f"失败: {result}"
                        except Exception as exc:
                            result_text = f"异常: {exc}"
                    else:
                        result_text = f"未知工具: {tool_name}"

                    call_id = getattr(call, "id", None)
                    response.add_payload(
                        LLMPayload(
                            ROLE.TOOL_RESULT,
                            ToolResult(value=result_text, call_id=call_id, name=tool_name),
                        )
                    )

                response = await response.send(stream=False)
            else:
                final_result = reply_text if reply_text else f"子代理在 {max_rounds} 轮内未完成"

            return True, {
                "action": "run_agent",
                "task": task[:200],
                "result": final_result,
                "rounds": round_num + 1,
            }

        except Exception as e:
            logger.error(f"执行子代理失败: {e}", exc_info=True)
            return False, f"执行失败: {e}"


# 导出所有工具类
ALL_TOOLS = [
    LifeEngineReadFileTool,
    LifeEngineWriteFileTool,
    LifeEngineEditFileTool,
    LifeEngineMoveFileTool,
    LifeEngineDeleteFileTool,
    LifeEngineListFilesTool,
    LifeEngineFileInfoTool,
    LifeEngineMakeDirectoryTool,
    LifeEngineWakeDFCTool,
    LifeEngineRunAgentTool,
]
