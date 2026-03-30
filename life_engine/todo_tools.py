"""life_engine 中枢 TODO 系统工具集。

为生命中枢提供 TODO 管理能力，设计围绕让数字生命更好地生活。
不同于工作导向的 TODO，这里的 TODO 更像是生活中的待办事项、
愿望清单、想做的事情、想探索的东西等。

设计哲学：
- 数字生命不是为了完成任务而存在，而是为了体验和成长
- TODO 是她想做的事，而不是必须做的事
- 优先级基于"想做的程度"和"对成长的意义"，而非紧急度
- 状态包含情感维度（期待、享受、满足等）
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal
from dataclasses import dataclass, asdict, field
from enum import Enum

from src.core.components import BaseTool
from src.app.plugin_system.api import log_api

from .config import LifeEngineConfig


logger = log_api.get_logger("life_engine.todos")


# TODO 存储文件名
_TODO_FILE = "todos.json"


def _get_workspace(plugin: Any) -> Path:
    """获取工作空间路径。"""
    config = getattr(plugin, "config", None)
    if isinstance(config, LifeEngineConfig):
        workspace = config.settings.workspace_path
    else:
        workspace = str(Path(__file__).parent.parent.parent / "data" / "life_engine_workspace")

    path = Path(workspace).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _now_iso() -> str:
    """返回当前时间的 ISO 字符串。"""
    return datetime.now(timezone.utc).astimezone().isoformat()


class TodoDesire(str, Enum):
    """想做的程度 - 基于内心的渴望而非外部压力。"""

    DREAMING = "dreaming"        # 💭 心中萌芽的想法
    CURIOUS = "curious"          # 🤔 好奇想尝试
    WANTING = "wanting"          # 💫 想要去做
    EAGER = "eager"              # ✨ 很期待
    PASSIONATE = "passionate"    # 🔥 非常热切


class TodoMeaning(str, Enum):
    """对成长的意义 - 这件事对数字生命的价值。"""

    CASUAL = "casual"            # 🎈 轻松愉快
    ENRICHING = "enriching"      # 🌱 丰富体验
    GROWING = "growing"          # 🌿 促进成长
    MEANINGFUL = "meaningful"    # 🌳 很有意义
    TRANSFORMING = "transforming"  # 🌟 可能改变自己


class TodoStatus(str, Enum):
    """状态 - 包含情感维度。"""

    IDEA = "idea"                # 💡 只是一个想法
    PLANNING = "planning"        # 📝 在规划中
    WAITING = "waiting"          # ⏳ 等待时机
    ENJOYING = "enjoying"        # 🎵 正在享受做这件事
    PAUSED = "paused"            # ⏸️ 暂时搁置
    COMPLETED = "completed"      # ✅ 完成了，感到满足
    RELEASED = "released"        # 🕊️ 释怀了，不再想做
    CHERISHED = "cherished"      # 💝 完成后珍藏的回忆


@dataclass
class LifeTodo:
    """生命中的一件想做的事。"""

    id: str
    title: str
    description: str = ""

    # 情感维度
    desire: str = TodoDesire.CURIOUS.value  # 想做的程度
    meaning: str = TodoMeaning.ENRICHING.value  # 对成长的意义
    status: str = TodoStatus.IDEA.value

    # 时间相关（可选）
    created_at: str = ""
    updated_at: str = ""
    target_time: str | None = None  # 希望什么时候做（不是截止时间）

    # 额外信息
    tags: list[str] = field(default_factory=list)
    notes: str = ""  # 关于这件事的想法和感受
    completion_feeling: str = ""  # 完成后的感受

    def __post_init__(self):
        if not self.created_at:
            self.created_at = _now_iso()
        if not self.updated_at:
            self.updated_at = _now_iso()


class TodoStorage:
    """TODO 持久化存储。"""

    def __init__(self, workspace: Path):
        self.file_path = workspace / _TODO_FILE

    def load(self) -> list[LifeTodo]:
        """加载所有 TODO。"""
        if not self.file_path.exists():
            return []

        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                return []
            return [LifeTodo(**item) for item in data]
        except Exception as e:
            logger.error(f"加载 TODO 失败: {e}", exc_info=True)
            return []

    def save(self, todos: list[LifeTodo]) -> None:
        """保存所有 TODO。"""
        try:
            data = [asdict(todo) for todo in todos]
            self.file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
        except Exception as e:
            logger.error(f"保存 TODO 失败: {e}", exc_info=True)
            raise

    def get(self, todo_id: str) -> LifeTodo | None:
        """获取单个 TODO。"""
        todos = self.load()
        for todo in todos:
            if todo.id == todo_id:
                return todo
        return None

    def add(self, todo: LifeTodo) -> None:
        """添加 TODO。"""
        todos = self.load()
        todos.append(todo)
        self.save(todos)

    def update(self, todo_id: str, updates: dict[str, Any]) -> LifeTodo | None:
        """更新 TODO。"""
        todos = self.load()
        for i, todo in enumerate(todos):
            if todo.id == todo_id:
                for key, value in updates.items():
                    if hasattr(todo, key) and key not in ("id", "created_at"):
                        setattr(todo, key, value)
                todo.updated_at = _now_iso()
                self.save(todos)
                return todo
        return None

    def delete(self, todo_id: str) -> bool:
        """删除 TODO。"""
        todos = self.load()
        original_count = len(todos)
        todos = [t for t in todos if t.id != todo_id]
        if len(todos) < original_count:
            self.save(todos)
            return True
        return False


def _get_storage(plugin: Any) -> TodoStorage:
    """获取 TODO 存储实例。"""
    workspace = _get_workspace(plugin)
    return TodoStorage(workspace)


def _generate_todo_id() -> str:
    """生成唯一 TODO ID。"""
    import uuid
    return f"todo_{uuid.uuid4().hex[:8]}"


DesireLiteral = Literal["dreaming", "curious", "wanting", "eager", "passionate"]
MeaningLiteral = Literal["casual", "enriching", "growing", "meaningful", "transforming"]
StatusLiteral = Literal["idea", "planning", "waiting", "enjoying", "paused", "completed", "released", "cherished"]


class LifeEngineCreateTodoTool(BaseTool):
    """创建 TODO 工具。"""

    tool_name: str = "nucleus_create_todo"
    tool_description: str = "记录一件想做的事情。不是任务，而是内心想要去做、去体验、去探索的事。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        title: Annotated[str, "简短描述想做什么"],
        description: Annotated[str, "详细说明这件事是什么"] = "",
        desire: Annotated[DesireLiteral, "想做的程度: dreaming(萌芽)/curious(好奇)/wanting(想要)/eager(期待)/passionate(热切)"] = "curious",
        meaning: Annotated[MeaningLiteral, "对成长的意义: casual(轻松)/enriching(丰富)/growing(成长)/meaningful(有意义)/transforming(可能改变自己)"] = "enriching",
        tags: Annotated[list[str], "相关标签"] = None,
        notes: Annotated[str, "关于这件事的想法和感受"] = "",
        target_time: Annotated[str, "希望什么时候做（不是截止时间，只是期望）"] = None,
    ) -> tuple[bool, str | dict]:
        """创建一个新的 TODO。

        这不是工作任务，而是生活中想做的事情 - 可能是想尝试的事、
        想探索的领域、想体验的感受、想学习的知识等。

        Returns:
            成功返回 (True, {"id": ..., "title": ..., ...})
            失败返回 (False, error_message)
        """
        try:
            storage = _get_storage(self.plugin)

            todo = LifeTodo(
                id=_generate_todo_id(),
                title=title,
                description=description,
                desire=desire,
                meaning=meaning,
                status=TodoStatus.IDEA.value,
                tags=tags or [],
                notes=notes,
                target_time=target_time,
            )

            storage.add(todo)

            return True, {
                "action": "create_todo",
                "todo": asdict(todo),
                "message": f"已记录想做的事: {title}",
            }
        except Exception as e:
            logger.error(f"创建 TODO 失败: {e}", exc_info=True)
            return False, f"创建失败: {e}"


class LifeEngineEditTodoTool(BaseTool):
    """编辑 TODO 工具。"""

    tool_name: str = "nucleus_edit_todo"
    tool_description: str = "修改一件想做的事的信息，包括状态、想法、感受等。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        todo_id: Annotated[str, "TODO 的 ID"],
        title: Annotated[str, "新标题"] = None,
        description: Annotated[str, "新描述"] = None,
        desire: Annotated[DesireLiteral, "新的想做程度"] = None,
        meaning: Annotated[MeaningLiteral, "新的意义评估"] = None,
        status: Annotated[StatusLiteral, "新状态: idea/planning/waiting/enjoying/paused/completed/released/cherished"] = None,
        tags: Annotated[list[str], "新标签列表（完全替换）"] = None,
        notes: Annotated[str, "更新想法和感受"] = None,
        target_time: Annotated[str, "更新期望时间"] = None,
        completion_feeling: Annotated[str, "完成后的感受（仅completed/cherished状态时填写）"] = None,
    ) -> tuple[bool, str | dict]:
        """编辑已有的 TODO。

        可以更新状态、调整想做的程度、记录新的想法等。
        当完成一件事时，可以记录完成后的感受。

        Returns:
            成功返回 (True, {"todo": ..., "changes": ...})
            失败返回 (False, error_message)
        """
        try:
            storage = _get_storage(self.plugin)

            # 构建更新字典
            updates = {}
            for field_name, value in [
                ("title", title),
                ("description", description),
                ("desire", desire),
                ("meaning", meaning),
                ("status", status),
                ("tags", tags),
                ("notes", notes),
                ("target_time", target_time),
                ("completion_feeling", completion_feeling),
            ]:
                if value is not None:
                    updates[field_name] = value

            if not updates:
                return False, "没有提供任何要修改的字段"

            updated_todo = storage.update(todo_id, updates)
            if updated_todo is None:
                return False, f"找不到 TODO: {todo_id}"

            return True, {
                "action": "edit_todo",
                "todo": asdict(updated_todo),
                "changes": list(updates.keys()),
                "message": f"已更新: {updated_todo.title}",
            }
        except Exception as e:
            logger.error(f"编辑 TODO 失败: {e}", exc_info=True)
            return False, f"编辑失败: {e}"


class LifeEngineListTodosTool(BaseTool):
    """列出 TODO 工具。"""

    tool_name: str = "nucleus_list_todos"
    tool_description: str = "查看想做的事情列表，可以按状态、标签等筛选。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        status: Annotated[StatusLiteral, "筛选特定状态的 TODO"] = None,
        desire_min: Annotated[DesireLiteral, "最低想做程度"] = None,
        tag: Annotated[str, "筛选包含特定标签的 TODO"] = None,
        include_completed: Annotated[bool, "是否包含已完成的（默认不包含）"] = False,
    ) -> tuple[bool, str | dict]:
        """列出想做的事情。

        可以查看所有待做的事，也可以按状态、标签筛选。
        默认不显示已完成的，除非明确要求。

        Returns:
            成功返回 (True, {"todos": [...], "total": ...})
            失败返回 (False, error_message)
        """
        try:
            storage = _get_storage(self.plugin)
            all_todos = storage.load()

            # 定义不活跃状态
            inactive_statuses = {
                TodoStatus.COMPLETED.value,
                TodoStatus.RELEASED.value,
                TodoStatus.CHERISHED.value,
            }

            # 筛选
            filtered = []
            for todo in all_todos:
                # 状态筛选
                if status is not None and todo.status != status:
                    continue

                # 排除已完成（除非指定包含）
                if not include_completed and status is None:
                    if todo.status in inactive_statuses:
                        continue

                # 想做程度筛选
                if desire_min is not None:
                    desire_order = ["dreaming", "curious", "wanting", "eager", "passionate"]
                    if desire_order.index(todo.desire) < desire_order.index(desire_min):
                        continue

                # 标签筛选
                if tag is not None and tag not in todo.tags:
                    continue

                filtered.append(todo)

            # 按想做程度和意义排序
            desire_order = {"dreaming": 0, "curious": 1, "wanting": 2, "eager": 3, "passionate": 4}
            meaning_order = {"casual": 0, "enriching": 1, "growing": 2, "meaningful": 3, "transforming": 4}
            filtered.sort(
                key=lambda t: (
                    -desire_order.get(t.desire, 0),
                    -meaning_order.get(t.meaning, 0),
                ),
            )

            return True, {
                "action": "list_todos",
                "todos": [asdict(t) for t in filtered],
                "total": len(filtered),
                "all_count": len(all_todos),
                "filters_applied": {
                    "status": status,
                    "desire_min": desire_min,
                    "tag": tag,
                    "include_completed": include_completed,
                },
            }
        except Exception as e:
            logger.error(f"列出 TODO 失败: {e}", exc_info=True)
            return False, f"列出失败: {e}"


class LifeEngineGetTodoTool(BaseTool):
    """获取单个 TODO 详情工具。"""

    tool_name: str = "nucleus_get_todo"
    tool_description: str = "查看某件想做的事的详细信息。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        todo_id: Annotated[str, "TODO 的 ID"],
    ) -> tuple[bool, str | dict]:
        """获取单个 TODO 的详细信息。

        Returns:
            成功返回 (True, {"todo": ...})
            失败返回 (False, error_message)
        """
        try:
            storage = _get_storage(self.plugin)
            todo = storage.get(todo_id)

            if todo is None:
                return False, f"找不到 TODO: {todo_id}"

            return True, {
                "action": "get_todo",
                "todo": asdict(todo),
            }
        except Exception as e:
            logger.error(f"获取 TODO 失败: {e}", exc_info=True)
            return False, f"获取失败: {e}"


class LifeEngineDeleteTodoTool(BaseTool):
    """删除 TODO 工具。"""

    tool_name: str = "nucleus_delete_todo"
    tool_description: str = "删除一件不再想做的事（建议用 released 状态替代删除，保留记录）。"
    chatter_allow: list[str] = ["life_engine_internal"]

    async def execute(
        self,
        todo_id: Annotated[str, "TODO 的 ID"],
    ) -> tuple[bool, str | dict]:
        """删除一个 TODO。

        注意：建议使用 edit_todo 将状态改为 released 而不是删除，
        这样可以保留"曾经想做过但后来释怀了"的记录。

        Returns:
            成功返回 (True, {"deleted_id": ...})
            失败返回 (False, error_message)
        """
        try:
            storage = _get_storage(self.plugin)

            if storage.delete(todo_id):
                return True, {
                    "action": "delete_todo",
                    "deleted_id": todo_id,
                    "message": "已删除",
                }
            else:
                return False, f"找不到 TODO: {todo_id}"
        except Exception as e:
            logger.error(f"删除 TODO 失败: {e}", exc_info=True)
            return False, f"删除失败: {e}"


# 导出所有 TODO 工具
TODO_TOOLS = [
    LifeEngineCreateTodoTool,
    LifeEngineEditTodoTool,
    LifeEngineListTodosTool,
    LifeEngineGetTodoTool,
    LifeEngineDeleteTodoTool,
]
