"""proactive_message_plugin 状态管理测试。"""

from __future__ import annotations

from datetime import datetime

from plugins.proactive_message_plugin.service import (
    PendingFollowup,
    ProactiveMessageService,
)


def test_prepare_post_send_state_preserves_last_bot_message() -> None:
    """发送后清理等待态时，应保留上一条 Bot 消息信息。"""

    service = ProactiveMessageService()
    service.clear_all()

    state = service.get_or_create_state("sid_a")
    state.last_bot_message_time = datetime.now()
    state.last_bot_message_excerpt = "上一条显式回复"
    state.followup_chain_count = 1
    state.pending_followup = PendingFollowup(
        topic="补充",
        thought="还有一点想说",
        followup_type="add_detail",
        delay_seconds=30.0,
        scheduled_at=datetime.now(),
        check_at=datetime.now(),
    )
    state.is_waiting = True
    state.active_check_kind = "followup"
    state.scheduler_task_name = "proactive_check_sid_a"

    service.prepare_post_send_state("sid_a", reset_followup_chain=False)
    current = service.get_state("sid_a")

    assert current is not None
    assert current.last_bot_message_excerpt == "上一条显式回复"
    assert current.pending_followup is None
    assert current.followup_chain_count == 1
    assert current.is_waiting is False
    assert current.active_check_kind is None


def test_user_message_reset_clears_followup_chain() -> None:
    """收到用户消息后，应清空续话链状态。"""

    service = ProactiveMessageService()
    service.clear_all()

    state = service.get_or_create_state("sid_b")
    state.followup_chain_count = 2
    state.pending_followup = PendingFollowup(
        topic="追问",
        thought="还想补一句",
        followup_type="share_new_thought",
        delay_seconds=20.0,
        scheduled_at=datetime.now(),
        check_at=datetime.now(),
    )
    state.followup_cooldown_until = datetime.now()
    state.is_waiting = True
    state.active_check_kind = "followup"

    class _ChatStream:
        stream_id = "sid_b"

    service.on_user_message(_ChatStream(), cancel_task=False)
    current = service.get_state("sid_b")

    assert current is not None
    assert current.followup_chain_count == 0
    assert current.pending_followup is None
    assert current.active_check_kind is None
    assert current.is_waiting is False


def test_mark_followup_trigger_sent_only_counts_once() -> None:
    """同一轮延迟续话触发中，多次发送也只计一次链增量。"""

    service = ProactiveMessageService()
    service.clear_all()

    state = service.get_or_create_state("sid_c")
    state.followup_chain_count = 0

    service.mark_followup_trigger_active("sid_c")
    first = service.mark_followup_trigger_sent("sid_c")
    second = service.mark_followup_trigger_sent("sid_c")
    service.clear_followup_trigger("sid_c")

    current = service.get_state("sid_c")
    assert current is not None
    assert first is True
    assert second is False
    assert current.followup_chain_count == 1
    assert current.followup_trigger_active is False
