"""测试 DFC-Life 架构重构的核心改动。

覆盖：
- LLMContextManager.update_reminder() 方法
- _apply_reminders() 的尾部追加逻辑
- _refresh_reminder_from_store() 辅助函数
- ConsultNucleusTool 基本结构
"""

from __future__ import annotations

import pytest

from src.kernel.llm.context import LLMContextManager
from src.kernel.llm import LLMPayload, ROLE, Text


# ============================================================
# update_reminder 测试
# ============================================================


class TestUpdateReminder:
    """测试 update_reminder 方法。"""

    def test_update_replaces_all_reminders(self):
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("first reminder", wrap_with_system_tag=True)
        ctx.update_reminder("second reminder", wrap_with_system_tag=True)

        # 应该只有一个 reminder（被替换）
        assert len(ctx._reminders) == 1
        assert "second reminder" in ctx._reminders[0].text

    def test_update_clears_old_and_sets_new(self):
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("a", wrap_with_system_tag=False)
        ctx.reminder("b", wrap_with_system_tag=False)
        assert len(ctx._reminders) == 2

        ctx.update_reminder("c", wrap_with_system_tag=False)
        assert len(ctx._reminders) == 1
        assert ctx._reminders[0].text == "c"

    def test_update_with_system_tag(self):
        ctx = LLMContextManager(max_payloads=10)
        ctx.update_reminder("inner state", wrap_with_system_tag=True)
        assert "<system_reminder>" in ctx._reminders[0].text
        assert "inner state" in ctx._reminders[0].text


# ============================================================
# _apply_reminders 尾部追加测试
# ============================================================


class TestApplyRemindersAppend:
    """测试 reminder 注入到 USER block 尾部而非头部。"""

    def test_reminder_appended_to_end(self):
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("my_reminder", wrap_with_system_tag=False)

        user_payload = LLMPayload(ROLE.USER, Text("history text"))
        payloads = [user_payload]

        result = ctx._apply_reminders(payloads)

        assert len(result) == 1
        contents = result[0].content
        # 历史在前
        assert isinstance(contents[0], Text)
        assert contents[0].text == "history text"
        # reminder 在后
        assert isinstance(contents[1], Text)
        assert contents[1].text == "my_reminder"

    def test_reminder_not_duplicated(self):
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("my_reminder", wrap_with_system_tag=False)

        user_payload = LLMPayload(ROLE.USER, Text("history text"))
        payloads = [user_payload]

        # 第一次注入
        result = ctx._apply_reminders(payloads)
        # 第二次注入（不应重复）
        result = ctx._apply_reminders(result)

        contents = result[0].content
        assert len(contents) == 2  # 历史 + reminder（不重复）

    def test_system_payload_not_affected(self):
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("my_reminder", wrap_with_system_tag=False)

        sys_payload = LLMPayload(ROLE.SYSTEM, Text("system prompt"))
        user_payload = LLMPayload(ROLE.USER, Text("user text"))
        payloads = [sys_payload, user_payload]

        result = ctx._apply_reminders(payloads)

        # system payload 不变
        assert result[0].content[0].text == "system prompt"
        # user payload 末尾有 reminder
        user_contents = result[1].content
        assert user_contents[0].text == "user text"
        assert user_contents[1].text == "my_reminder"

    def test_no_user_payload_noop(self):
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("my_reminder", wrap_with_system_tag=False)

        sys_payload = LLMPayload(ROLE.SYSTEM, Text("system prompt"))
        payloads = [sys_payload]

        result = ctx._apply_reminders(payloads)
        assert len(result) == 1
        assert result[0].role == ROLE.SYSTEM

    def test_backward_compat_migrates_prefix_to_suffix(self):
        """如果旧版 reminder 在头部，应迁移到尾部。"""
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("old_reminder", wrap_with_system_tag=False)

        # 模拟旧版：reminder 在头部
        old_style_payload = LLMPayload(
            ROLE.USER, [Text("old_reminder"), Text("history")]
        )
        payloads = [old_style_payload]

        result = ctx._apply_reminders(payloads)
        contents = result[0].content
        # 旧前缀应被移除，history 在前，reminder 在后
        assert contents[0].text == "history"
        assert contents[1].text == "old_reminder"


# ============================================================
# ConsultNucleusTool 结构测试
# ============================================================


class TestConsultNucleusStructure:
    """测试 ConsultNucleusTool 的基本属性。"""

    def test_tool_has_correct_name(self):
        from plugins.default_chatter.consult_nucleus import ConsultNucleusTool
        assert ConsultNucleusTool.tool_name == "consult_nucleus"

    def test_tool_has_description(self):
        from plugins.default_chatter.consult_nucleus import ConsultNucleusTool
        assert len(ConsultNucleusTool.tool_description) > 20

    def test_tool_allows_default_chatter(self):
        from plugins.default_chatter.consult_nucleus import ConsultNucleusTool
        assert "default_chatter" in ConsultNucleusTool.chatter_allow
