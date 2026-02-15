from enum import Enum
from typing import Any

from src.kernel.llm import (
    EmbeddingRequest,
    LLMContextManager,
    LLMRequest,
    LLMUsable,
    ModelSet,
    RerankRequest,
    ToolCall,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
)
from src.core.config import get_model_config

class TaskType(Enum):
    UTILS = "utils"
    UTILS_SMALL = "utils_small"
    ACTOR = "actor"
    SUB_ACTOR = "sub_actor"
    VLM = "vlm"
    VOICE = "voice"
    VIDEO = "video"
    TOOL_USE = "tool_use"


def create_llm_request(
    model_set: ModelSet,
    request_name: str = "",
    context_manager: LLMContextManager | None = None,
) -> LLMRequest:
    """创建 LLMRequest 实例

    Args:
        model_set: 模型集
        request_name: 请求名称（可选）
        context_manager: 上下文管理器（可选）

    Returns:
        LLMRequest 实例
    """
    return LLMRequest(
        model_set=model_set,
        request_name=request_name,
        context_manager=context_manager,
    )


def create_embedding_request(
    model_set: ModelSet,
    request_name: str = "",
    inputs: list[str] | None = None,
) -> EmbeddingRequest:
    """创建 EmbeddingRequest 实例。"""
    return EmbeddingRequest(
        model_set=model_set,
        request_name=request_name,
        inputs=list(inputs or []),
    )


def create_rerank_request(
    model_set: ModelSet,
    request_name: str = "",
    query: str = "",
    documents: list[Any] | None = None,
    top_n: int | None = None,
) -> RerankRequest:
    """创建 RerankRequest 实例。"""
    return RerankRequest(
        model_set=model_set,
        request_name=request_name,
        query=query,
        documents=list(documents or []),
        top_n=top_n,
    )

def get_model_set_by_task(name: str) -> ModelSet:
    """根据任务名称获取 ModelSet

    Args:
        name: 模型集名称

    Returns:
        ModelSet 实例
    """
    return get_model_config().get_task(name)

def create_tool_registry(tools: list[type[LLMUsable]] | None = None) -> ToolRegistry:
    """创建工具注册表实例"""
    registry = ToolRegistry()
    if tools:
        for tool in tools:
            registry.register(tool)
    return registry
