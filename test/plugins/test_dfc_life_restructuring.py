"""测试 DFC-Life 架构重构的核心改动。

覆盖：
- LLMContextManager.update_reminder() 方法
- _apply_reminders() 的末尾注入逻辑
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
# _apply_reminders 末尾注入测试
# ============================================================


class TestApplyRemindersAppend:
    """测试 reminder 注入到 USER block 末尾。"""

    def test_reminder_appended_to_tail(self):
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
        ctx.reminder("my_reminder", wrap_with_system_tag=True)

        user_payload = LLMPayload(ROLE.USER, Text("history text"))
        payloads = [user_payload]

        # 第一次注入
        result = ctx._apply_reminders(payloads)
        # 第二次注入（不应重复）
        result = ctx._apply_reminders(result)

        contents = result[0].content
        assert len(contents) == 2  # 历史 + reminder（不重复）

    def test_old_reminder_stripped_when_content_changes(self):
        """心跳更新 reminder 内容后，旧版本应被剥离，只保留最新版本。

        这是导致上下文暴涨的根因：每次心跳更新产生不同的 reminder，
        旧 reminder 滞留在 USER block 中，导致每轮增长数百 token。
        """
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("heartbeat v1", wrap_with_system_tag=True)

        user_payload = LLMPayload(ROLE.USER, Text("history text"))
        payloads = [user_payload]

        # 第一次心跳注入
        result = ctx._apply_reminders(payloads)
        assert len(result[0].content) == 2  # history + reminder_v1

        # 模拟心跳更新 reminder 内容
        ctx.update_reminder("heartbeat v2", wrap_with_system_tag=True)
        result = ctx._apply_reminders(result)

        contents = result[0].content
        assert len(contents) == 2  # history + reminder_v2（v1 已被剥离）
        assert contents[0].text == "history text"
        assert "heartbeat v2" in contents[1].text
        assert "heartbeat v1" not in contents[1].text

    def test_reminder_accumulation_prevented_across_many_updates(self):
        """模拟 10 次心跳更新，验证 USER block 中只有最新 reminder。"""
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("initial", wrap_with_system_tag=True)

        user_payload = LLMPayload(ROLE.USER, Text("history"))
        result = [user_payload]

        for i in range(10):
            ctx.update_reminder(f"heartbeat round {i}", wrap_with_system_tag=True)
            result = ctx._apply_reminders(result)

        contents = result[0].content
        # 只应有 history + 最新 reminder，不应累积
        assert len(contents) == 2
        assert contents[0].text == "history"
        assert "heartbeat round 9" in contents[1].text

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

    def test_tagged_reminder_at_head_also_stripped(self):
        """旧版 reminder 在头部的情况——应同样被识别并剥离。"""
        ctx = LLMContextManager(max_payloads=10)
        ctx.reminder("new_state", wrap_with_system_tag=True)

        # 模拟旧版格式：tagged reminder 在头部
        old_style_payload = LLMPayload(
            ROLE.USER, [Text("<system_reminder>\nold_state\n</system_reminder>"), Text("history")]
        )
        payloads = [old_style_payload]

        result = ctx._apply_reminders(payloads)
        contents = result[0].content
        # 旧 reminder 被剥离，新 reminder 追加到末尾
        assert len(contents) == 2
        assert contents[0].text == "history"
        assert "new_state" in contents[1].text


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
