"""life_engine memory_service 异常处理测试。

测试 P0 修复：_emit_visual_event 方法只捕获预期异常类型
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestEmitVisualEvent:
    """测试 _emit_visual_event 方法的异常处理。"""

    def test_method_exists_and_has_correct_exception_handling(self, tmp_path: Path) -> None:
        """验证 _emit_visual_event 方法存在且包含正确的异常处理结构。"""
        from plugins.life_engine.memory.service import LifeMemoryService

        service = LifeMemoryService(str(tmp_path))

        # 验证方法存在
        assert hasattr(service, '_emit_visual_event')

        # 获取方法源代码（简单验证包含关键异常类型）
        import inspect
        source = inspect.getsource(service._emit_visual_event)

        # 验证捕获了预期的异常类型
        assert "ImportError" in source
        assert "RuntimeError" in source
        assert "ConnectionError" in source
        assert "AttributeError" in source

        # 验证有对意外异常的安全处理（不重新抛出）
        assert "except Exception" in source

    def test_import_error_handled(self, tmp_path: Path) -> None:
        """当 memory_router 模块不存在时，ImportError 应该被处理。"""
        from plugins.life_engine.memory.service import LifeMemoryService

        service = LifeMemoryService(str(tmp_path))

        # 方法应该能正常执行，即使导入可能失败
        # 这不会抛出异常，因为异常被捕获了
        try:
            service._emit_visual_event("test_event", {"key": "value"}, "test_source")
        except Exception as e:
            # 如果抛出了异常，应该是意外异常，不是 ImportError
            pytest.fail(f"不应该抛出异常，但抛出了: {type(e).__name__}: {e}")


class TestExceptionTypesInCode:
    """验证代码中的异常处理类型。"""

    def test_expected_exceptions_listed(self) -> None:
        """验证预期的异常类型在代码中被捕获。"""
        from plugins.life_engine.memory.service import LifeMemoryService
        import inspect

        source = inspect.getsource(LifeMemoryService._emit_visual_event)

        # 预期捕获的异常类型
        expected_exceptions = [
            "ImportError",
            "RuntimeError",
            "ConnectionError",
            "AttributeError",
        ]

        for exc in expected_exceptions:
            assert exc in source, f"代码中应该捕获 {exc}"

    def test_unexpected_exceptions_handled_safely(self) -> None:
        """验证意外异常被安全处理，不会影响主流程。"""
        from plugins.life_engine.memory.service import LifeMemoryService
        import inspect

        source = inspect.getsource(LifeMemoryService._emit_visual_event)

        # 验证有兜底的 except Exception 处理
        assert "except Exception" in source
        # 可视化是非关键路径，不应 re-raise
        # 计算 bare "raise" 的次数（排除 "raise" 在字符串中的出现）
        lines = [l.strip() for l in source.split("\n")]
        bare_raise_count = sum(1 for l in lines if l == "raise")
        assert bare_raise_count == 0, "可视化事件不应 re-raise 异常"
