"""default_chatter SendTextAction 工具函数测试。"""

from __future__ import annotations

from plugins.default_chatter.config import DefaultChatterConfig
from plugins.default_chatter.plugin import SendTextAction


def test_normalize_content_segments_accepts_plain_text() -> None:
    """普通字符串应被规范为单段列表。"""
    result = SendTextAction._normalize_content_segments("  你好呀  ")
    assert result == ["你好呀"]


def test_normalize_content_segments_accepts_json_list_string() -> None:
    """JSON 字符串数组应被解析为分段列表。"""
    result = SendTextAction._normalize_content_segments(
        '["第一条", " 第二条  ", "", 123]'
    )
    assert result == ["第一条", "第二条"]


def test_normalize_content_segments_accepts_native_list() -> None:
    """原生字符串数组应保留非空段并去除空白。"""
    result = SendTextAction._normalize_content_segments(
        ["  A  ", "", "B", 1]  # type: ignore[list-item]
    )
    assert result == ["A", "B"]


def test_sanitize_segment_strips_reason_leak() -> None:
    """应截断 reason 元字段泄漏。"""
    result = SendTextAction._sanitize_segment("先发结论，reason: 这是内心想法")
    assert result == "先发结论"


def test_sanitize_segment_strips_quoted_reason_leak() -> None:
    """带引号的 reason 键也应被截断。"""
    result = SendTextAction._sanitize_segment('正文内容, "reason": "这是内部原因"')
    assert result == "正文内容"


def test_normalize_content_segments_handles_mixed_json_payload_chain() -> None:
    """content 混有尾随 reason 和 <br/> JSON 链时，应只解析首个数组。"""
    content = (
        '["第一条", "第二条", "第三条"] , "reason": "内部说明"}'
        '<br/>{"description":"emoji"}<br/>{"delay_seconds":180}'
    )
    result = SendTextAction._normalize_content_segments(content)
    assert result == ["第一条", "第二条", "第三条"]


def test_calculate_typing_delay_uses_reply_config() -> None:
    """应按 reply 配置计算并钳制段间延迟。"""
    config = DefaultChatterConfig.from_dict(
        {
            "plugin": {
                "reply": {
                    "typing_chars_per_sec": 10.0,
                    "typing_delay_min": 0.5,
                    "typing_delay_max": 2.0,
                }
            }
        }
    )
    # len=5 => 0.5
    assert SendTextAction._calculate_typing_delay("12345", config) == 0.5
    # len=100 => 10.0，但上限 2.0
    assert SendTextAction._calculate_typing_delay("x" * 100, config) == 2.0
