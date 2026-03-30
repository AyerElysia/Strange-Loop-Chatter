"""life_engine 中枢文件系统工具集。

为生命中枢提供限定在 workspace 内的文件系统操作能力。
所有操作都限制在配置的 workspace_path 目录下，确保安全。
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any

from src.core.components import BaseTool
from src.app.plugin_system.api import log_api

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


class LifeEngineReadFileTool(BaseTool):
    """读取文件内容工具。"""

    tool_name: str = "nucleus_read_file"
    tool_description: str = "读取工作空间内指定文件的内容。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的文件路径"],
        encoding: Annotated[str, "文件编码，默认utf-8"] = "utf-8",
    ) -> tuple[bool, str | dict]:
        """读取文件内容。

        Args:
            path: 相对于工作空间的文件路径
            encoding: 文件编码，默认为 utf-8

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
            content = target.read_text(encoding=encoding)
            stat = target.stat()
            return True, {
                "action": "read_file",
                "path": path,
                "absolute_path": str(target),
                "content": content,
                "size": stat.st_size,
                "size_human": _format_size(stat.st_size),
                "modified_at": _format_time(stat.st_mtime),
            }
        except UnicodeDecodeError as e:
            return False, f"文件编码错误，请尝试其他编码: {e}"
        except Exception as e:
            logger.error(f"读取文件失败 {path}: {e}", exc_info=True)
            return False, f"读取文件失败: {e}"


class LifeEngineWriteFileTool(BaseTool):
    """写入文件工具（覆盖）。"""

    tool_name: str = "nucleus_write_file"
    tool_description: str = "在工作空间内创建或覆盖文件。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的文件路径"],
        content: Annotated[str, "要写入的内容"],
        encoding: Annotated[str, "文件编码，默认utf-8"] = "utf-8",
    ) -> tuple[bool, str | dict]:
        """写入文件（覆盖模式）。

        Args:
            path: 相对于工作空间的文件路径
            content: 要写入的内容
            encoding: 文件编码，默认为 utf-8

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
            return True, {
                "action": "write_file",
                "path": path,
                "absolute_path": str(target),
                "size": stat.st_size,
                "size_human": _format_size(stat.st_size),
                "created": not existed,
                "modified_at": _format_time(stat.st_mtime),
            }
        except Exception as e:
            logger.error(f"写入文件失败 {path}: {e}", exc_info=True)
            return False, f"写入文件失败: {e}"


class LifeEngineEditFileTool(BaseTool):
    """编辑文件工具（查找替换）。"""

    tool_name: str = "nucleus_edit_file"
    tool_description: str = "编辑工作空间内文件的特定内容（查找并替换）。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        path: Annotated[str, "相对于工作空间的文件路径"],
        old_text: Annotated[str, "要查找的原始文本"],
        new_text: Annotated[str, "替换后的新文本"],
        encoding: Annotated[str, "文件编码，默认utf-8"] = "utf-8",
    ) -> tuple[bool, str | dict]:
        """编辑文件中的特定内容。

        Args:
            path: 相对于工作空间的文件路径
            old_text: 要查找的原始文本
            new_text: 替换后的新文本
            encoding: 文件编码，默认为 utf-8

        Returns:
            成功返回 (True, {"path": ..., "replacements": ..., "size": ...})
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
                return False, f"未找到要替换的文本"

            new_content = content.replace(old_text, new_text)
            target.write_text(new_content, encoding=encoding)
            stat = target.stat()

            return True, {
                "action": "edit_file",
                "path": path,
                "absolute_path": str(target),
                "replacements": count,
                "size": stat.st_size,
                "size_human": _format_size(stat.st_size),
                "modified_at": _format_time(stat.st_mtime),
            }
        except UnicodeDecodeError as e:
            return False, f"文件编码错误: {e}"
        except Exception as e:
            logger.error(f"编辑文件失败 {path}: {e}", exc_info=True)
            return False, f"编辑文件失败: {e}"


class LifeEngineMoveFileTool(BaseTool):
    """移动/重命名文件工具。"""

    tool_name: str = "nucleus_move_file"
    tool_description: str = "在工作空间内移动或重命名文件/目录。"
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
    tool_description: str = "删除工作空间内的文件或空目录。"
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
    tool_description: str = "列出工作空间内指定目录的文件和子目录。"
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
    tool_description: str = "获取工作空间内文件或目录的详细信息。"
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
    tool_description: str = "在工作空间内创建目录。"
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
]
